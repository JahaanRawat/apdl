"""Authoritative audit writes fail closed; telemetry is explicitly best effort."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.safety.audit import AuditLogger
from app.store.tool_result_artifacts import (
    ToolResultArtifactDraft,
    prepare_tool_result,
)


class _Acquire:
    async def __aenter__(self):
        raise RuntimeError("audit unavailable")

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def acquire(self) -> _Acquire:
        return _Acquire()


@pytest.mark.asyncio
async def test_required_audit_failure_is_raised() -> None:
    audit = AuditLogger(_Pool())
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await audit.log_required("run-1", "mutation_authorized")


@pytest.mark.asyncio
async def test_best_effort_audit_failure_is_only_for_telemetry() -> None:
    audit = AuditLogger(_Pool())
    assert await audit.log("run-1", "supervisor_heartbeat") == -1


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc) -> bool:
        return False


class _WritingConn:
    def __init__(self, *, fail_artifact: bool = False) -> None:
        self.fail_artifact = fail_artifact
        self.queries: list[str] = []
        self.audit_ids = iter((42, 43))

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchval(self, query: str, *args):
        self.queries.append(query)
        if "INSERT INTO agent_tool_result_artifacts" in query:
            if self.fail_artifact:
                raise RuntimeError("artifact unavailable")
            return "11111111-1111-4111-8111-111111111111"
        if "INSERT INTO agent_audit_log" in query:
            return next(self.audit_ids)
        raise AssertionError(query)


class _WritingPool:
    def __init__(self, conn: _WritingConn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _artifact() -> ToolResultArtifactDraft:
    prepared = prepare_tool_result({"count": 7})
    assert prepared is not None
    return ToolResultArtifactDraft(
        project_id="demo",
        run_id="run-1",
        source_id="warehouse:" + "a" * 24,
        tool_name="query_events",
        prepared=prepared,
    )


@pytest.mark.asyncio
async def test_tool_artifact_is_inserted_atomically_with_its_audit_row() -> None:
    conn = _WritingConn()

    entry_id = await AuditLogger(_WritingPool(conn)).log(
        "run-1",
        "behavior_analysis_tool_call",
        {"tool": "query_events"},
        tool_result_artifact=_artifact(),
    )

    assert entry_id == 42
    assert sum("INSERT INTO agent_audit_log" in query for query in conn.queries) == 1
    assert sum(
        "INSERT INTO agent_tool_result_artifacts" in query for query in conn.queries
    ) == 1


@pytest.mark.asyncio
async def test_artifact_failure_falls_back_to_metadata_only_audit() -> None:
    conn = _WritingConn(fail_artifact=True)

    entry_id = await AuditLogger(_WritingPool(conn)).log(
        "run-1",
        "behavior_analysis_tool_call",
        {"tool": "query_events"},
        tool_result_artifact=_artifact(),
    )

    assert entry_id == 43
    assert sum("INSERT INTO agent_audit_log" in query for query in conn.queries) == 2
    assert sum(
        "INSERT INTO agent_tool_result_artifacts" in query for query in conn.queries
    ) == 1
