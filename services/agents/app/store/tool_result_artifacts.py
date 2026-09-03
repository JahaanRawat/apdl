"""Bounded durable previews for normal-run warehouse tool results."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final

import asyncpg

from app.safety.redaction import RedactionLimitError, redact_json_value


logger = logging.getLogger(__name__)

TOOL_RESULT_ARTIFACT_SCHEMA: Final = "tool_result_artifact@1"
TOOL_RESULT_DATA_CLASSIFICATION: Final = "confidential"
TOOL_RESULT_PREVIEW_BYTE_CAP: Final = 4_096
TOOL_RESULT_RETENTION_SWEEP_SECONDS: Final = 300
TOOL_RESULT_RETENTION_BATCH_SIZE: Final = 500
TOOL_RESULT_RETENTION_MAX_FAILURES: Final = 3
_OMITTED_PREVIEW: Final = "[PREVIEW OMITTED: result exceeded redaction limits]"
_TRUNCATION_MARKER: Final = "... [truncated]"


@dataclass(frozen=True)
class PreparedToolResult:
    """Full-result integrity metadata plus the only persistable preview."""

    content_sha256: str
    source_byte_count: int
    preview_text: str
    truncated: bool
    redacted: bool


@dataclass(frozen=True)
class ToolResultArtifactDraft:
    """Run/tool identity joined to a prepared result for transactional storage."""

    project_id: str
    run_id: str
    source_id: str
    tool_name: str
    prepared: PreparedToolResult


def _bounded_utf8_preview(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= TOOL_RESULT_PREVIEW_BYTE_CAP:
        return value, False
    marker = _TRUNCATION_MARKER.encode("ascii")
    prefix = encoded[: TOOL_RESULT_PREVIEW_BYTE_CAP - len(marker)].decode(
        "utf-8", "ignore"
    )
    preview = prefix + _TRUNCATION_MARKER
    if len(preview.encode("utf-8")) > TOOL_RESULT_PREVIEW_BYTE_CAP:
        raise AssertionError("tool result preview exceeded its UTF-8 byte cap")
    return preview, True


def prepare_tool_result(output: Any) -> PreparedToolResult | None:
    """Hash a full canonical result and build a separately redacted preview."""
    try:
        canonical_text = json.dumps(
            output,
            allow_nan=True,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        canonical_bytes = canonical_text.encode("utf-8")
        content_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    except (OverflowError, RecursionError, TypeError, ValueError):
        logger.exception("Could not canonicalize tool result for artifact storage")
        return None

    try:
        normalized = json.loads(canonical_text)
        redaction = redact_json_value(normalized)
        redacted_text = json.dumps(
            redaction.value,
            allow_nan=True,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        preview_text, truncated = _bounded_utf8_preview(redacted_text)
        redacted = redaction.redacted
    except (RedactionLimitError, RecursionError, TypeError, ValueError):
        # Never persist a prefix that was not completely scanned.
        preview_text = _OMITTED_PREVIEW
        truncated = True
        redacted = True

    return PreparedToolResult(
        content_sha256=content_sha256,
        source_byte_count=len(canonical_bytes),
        preview_text=preview_text,
        truncated=truncated,
        redacted=redacted,
    )


async def insert_tool_result_artifact(
    conn: Any,
    *,
    audit_entry_id: int,
    draft: ToolResultArtifactDraft,
) -> uuid.UUID:
    """Insert one immutable preview under the audit row's tenant/run identity."""
    artifact_id = await conn.fetchval(
        """
        INSERT INTO agent_tool_result_artifacts (
            audit_entry_id, run_id, project_id, source_id, tool_name,
            content_sha256, preview_text, source_byte_count,
            truncated, redacted
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
        )
        RETURNING artifact_id
        """,
        audit_entry_id,
        draft.run_id,
        draft.project_id,
        draft.source_id,
        draft.tool_name,
        draft.prepared.content_sha256,
        draft.prepared.preview_text,
        draft.prepared.source_byte_count,
        draft.prepared.truncated,
        draft.prepared.redacted,
    )
    return uuid.UUID(str(artifact_id))


async def get_tool_result_artifact(
    pool: asyncpg.Pool,
    *,
    artifact_id: uuid.UUID,
    run_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    """Return one live artifact using a single tenant/run/expiry predicate."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT artifact.artifact_id, artifact.schema_version,
                   artifact.audit_entry_id, artifact.run_id,
                   artifact.source_id,
                   artifact.content_sha256, artifact.preview_text,
                   artifact.source_byte_count, artifact.preview_byte_count,
                   artifact.truncated, artifact.redacted,
                   artifact.data_classification, artifact.created_at,
                   artifact.expires_at
            FROM agent_tool_result_artifacts AS artifact
            JOIN agent_runs AS run
              ON run.run_id = artifact.run_id
             AND run.project_id = artifact.project_id
            WHERE artifact.artifact_id = $1
              AND artifact.run_id = $2
              AND artifact.project_id = $3
              AND artifact.expires_at > statement_timestamp()
            """,
            artifact_id,
            run_id,
            project_id,
        )
    if row is None:
        return None
    if (
        row["schema_version"] != TOOL_RESULT_ARTIFACT_SCHEMA
        or row["data_classification"] != TOOL_RESULT_DATA_CLASSIFICATION
    ):
        raise RuntimeError("tool result artifact storage contract is invalid")
    return {
        "schema_version": row["schema_version"],
        "artifact_id": str(row["artifact_id"]),
        "run_id": row["run_id"],
        "audit_entry_id": row["audit_entry_id"],
        "source_id": row["source_id"],
        "content_sha256": row["content_sha256"],
        "preview_text": row["preview_text"],
        "source_byte_count": row["source_byte_count"],
        "preview_byte_count": row["preview_byte_count"],
        "truncated": row["truncated"],
        "redacted": row["redacted"],
        "data_classification": row["data_classification"],
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
    }


async def delete_expired_tool_result_artifacts(
    pool: asyncpg.Pool,
    *,
    batch_size: int = TOOL_RESULT_RETENTION_BATCH_SIZE,
) -> int:
    """Delete one bounded expiry batch without requiring row-lock privileges."""
    if type(batch_size) is not int or not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be an integer between 1 and 10000")
    async with pool.acquire() as conn:
        # Do not add a locking SELECT here: apdl_agents intentionally has no
        # UPDATE privilege. Concurrent DELETEs may contend for the same row,
        # but deletion remains idempotent and the bounded batch stays correct.
        deleted = await conn.fetchval(
            """
            WITH expired AS (
                SELECT artifact_id
                FROM agent_tool_result_artifacts
                WHERE expires_at <= statement_timestamp()
                ORDER BY expires_at, artifact_id
                LIMIT $1
            ), removed AS (
                DELETE FROM agent_tool_result_artifacts AS artifact
                USING expired
                WHERE artifact.artifact_id = expired.artifact_id
                RETURNING 1
            )
            SELECT count(*)::INTEGER FROM removed
            """,
            batch_size,
        )
    return int(deleted or 0)


async def purge_expired_tool_result_artifacts_forever(
    pool: asyncpg.Pool,
    stop: asyncio.Event,
    *,
    interval_seconds: int = TOOL_RESULT_RETENTION_SWEEP_SECONDS,
) -> None:
    """Drain expired previews at startup and periodically thereafter."""
    if type(interval_seconds) is not int or interval_seconds < 1:
        raise ValueError("interval_seconds must be a positive integer")
    consecutive_failures = 0
    while not stop.is_set():
        try:
            while await delete_expired_tool_result_artifacts(pool):
                pass
            consecutive_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            logger.exception("Could not purge expired tool result artifacts")
            if consecutive_failures >= TOOL_RESULT_RETENTION_MAX_FAILURES:
                raise RuntimeError(
                    "tool result artifact retention failed repeatedly"
                ) from exc
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
