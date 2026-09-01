"""Confidential bounded tool-result preview storage and retention."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.safety.redaction import REDACTION_MARKER, RedactionLimitError
from app.store import tool_result_artifacts as artifacts


def test_prepare_hashes_full_result_and_redacts_secret_and_identity_values() -> None:
    output = {
        "rows": [
            {
                "user_id": "customer-123",
                "email": "person@example.test",
                "property_value": "customer-segment-123",
                "cohort_value": "enterprise-customer-456",
                "value": "target-account-789",
                "selector": "purchase[user_id=customer-123]",
                "count": 7,
                "note": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            }
        ]
    }

    prepared = artifacts.prepare_tool_result(output)

    assert prepared is not None
    canonical = json.dumps(
        output,
        allow_nan=True,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert prepared.content_sha256 == hashlib.sha256(canonical).hexdigest()
    assert prepared.source_byte_count == len(canonical)
    assert prepared.redacted is True
    assert prepared.truncated is False
    assert "customer-123" not in prepared.preview_text
    assert "person@example.test" not in prepared.preview_text
    assert "customer-segment-123" not in prepared.preview_text
    assert "enterprise-customer-456" not in prepared.preview_text
    assert "target-account-789" not in prepared.preview_text
    assert "purchase[user_id=customer-123]" not in prepared.preview_text
    assert "abcdefghijklmnopqrstuvwxyz" not in prepared.preview_text
    assert REDACTION_MARKER in prepared.preview_text
    assert '"count":7' in prepared.preview_text


def test_prepare_truncates_on_utf8_bytes_without_splitting_output() -> None:
    prepared = artifacts.prepare_tool_result({"text": "é" * 5_000})

    assert prepared is not None
    assert prepared.truncated is True
    assert len(prepared.preview_text.encode("utf-8")) <= 4_096
    assert prepared.preview_text.endswith("... [truncated]")


def test_prepare_omits_unscannable_preview_without_failing_tool(monkeypatch) -> None:
    def over_limit(_value: Any):
        raise RedactionLimitError("too large")

    monkeypatch.setattr(artifacts, "redact_json_value", over_limit)

    prepared = artifacts.prepare_tool_result({"safe": "value"})

    assert prepared is not None
    assert prepared.preview_text.startswith("[PREVIEW OMITTED")
    assert prepared.truncated is True
    assert prepared.redacted is True


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc) -> bool:
        return False


class _Conn:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetchval(self, query: str, *args: Any):
        self.fetchval_calls.append((query, args))
        if "INSERT INTO agent_tool_result_artifacts" in query:
            return uuid.UUID("11111111-1111-4111-8111-111111111111")
        if "SELECT count(*)::INTEGER FROM removed" in query:
            return 3
        raise AssertionError(query)

    async def fetchrow(self, query: str, *args: Any):
        self.fetchrow_calls.append((query, args))
        return self.row


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _draft() -> artifacts.ToolResultArtifactDraft:
    prepared = artifacts.prepare_tool_result({"count": 7})
    assert prepared is not None
    return artifacts.ToolResultArtifactDraft(
        project_id="demo",
        run_id="run-1",
        source_id="warehouse:" + "a" * 24,
        tool_name="query_events",
        prepared=prepared,
    )


@pytest.mark.asyncio
async def test_insert_uses_only_canonical_runtime_supplied_columns() -> None:
    conn = _Conn()

    artifact_id = await artifacts.insert_tool_result_artifact(
        conn,
        audit_entry_id=42,
        draft=_draft(),
    )

    assert str(artifact_id) == "11111111-1111-4111-8111-111111111111"
    query, args = conn.fetchval_calls[0]
    assert "data_classification" not in query
    assert "created_at" not in query
    assert "expires_at" not in query
    assert args[:5] == (
        42,
        "run-1",
        "demo",
        "warehouse:" + "a" * 24,
        "query_events",
    )


@pytest.mark.asyncio
async def test_read_is_bound_to_artifact_run_project_and_live_expiry() -> None:
    created = datetime(2026, 9, 1, tzinfo=timezone.utc)
    row = {
        "artifact_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "schema_version": "tool_result_artifact@1",
        "audit_entry_id": 42,
        "run_id": "run-1",
        "source_id": "warehouse:" + "a" * 24,
        "content_sha256": "b" * 64,
        "preview_text": '{"count":7}',
        "source_byte_count": 11,
        "preview_byte_count": 11,
        "truncated": False,
        "redacted": False,
        "data_classification": "confidential",
        "created_at": created,
        "expires_at": created + timedelta(days=7),
    }
    conn = _Conn(row)
    artifact_id = uuid.UUID("11111111-1111-4111-8111-111111111111")

    result = await artifacts.get_tool_result_artifact(
        _Pool(conn),
        artifact_id=artifact_id,
        run_id="run-1",
        project_id="demo",
    )

    assert result is not None
    assert result["artifact_id"] == str(artifact_id)
    query, args = conn.fetchrow_calls[0]
    assert "artifact.artifact_id = $1" in query
    assert "artifact.run_id = $2" in query
    assert "artifact.project_id = $3" in query
    assert "artifact.expires_at > statement_timestamp()" in query
    assert args == (artifact_id, "run-1", "demo")


@pytest.mark.asyncio
async def test_expiry_cleanup_is_bounded_and_skip_locked() -> None:
    conn = _Conn()

    deleted = await artifacts.delete_expired_tool_result_artifacts(
        _Pool(conn), batch_size=25
    )

    assert deleted == 3
    query, args = conn.fetchval_calls[0]
    assert "FOR UPDATE SKIP LOCKED" in query
    assert "ORDER BY expires_at, artifact_id" in query
    assert "LIMIT $1" in query
    assert args == (25,)

    with pytest.raises(ValueError, match="batch_size"):
        await artifacts.delete_expired_tool_result_artifacts(_Pool(conn), batch_size=0)


@pytest.mark.asyncio
async def test_retention_worker_fails_readiness_after_repeated_cleanup_errors(
    monkeypatch,
) -> None:
    attempts = 0

    async def fail_cleanup(_pool):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("postgres unavailable")

    async def advance_without_sleep(awaitable, *, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(artifacts, "delete_expired_tool_result_artifacts", fail_cleanup)
    monkeypatch.setattr(artifacts.asyncio, "wait_for", advance_without_sleep)

    with pytest.raises(RuntimeError, match="retention failed repeatedly"):
        await artifacts.purge_expired_tool_result_artifacts_forever(
            object(),
            artifacts.asyncio.Event(),
            interval_seconds=1,
        )

    assert attempts == artifacts.TOOL_RESULT_RETENTION_MAX_FAILURES
