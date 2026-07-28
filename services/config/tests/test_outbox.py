"""Durable Config outbox delivery and retry tests."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app import outbox


@pytest.fixture(autouse=True)
def clear_alert_log_state():
    outbox._last_alert_log.clear()


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "+inf", "-inf"])
def test_environment_float_rejects_non_finite_values(monkeypatch, raw):
    monkeypatch.setenv("APDL_TEST_OUTBOX_FLOAT", raw)

    with pytest.raises(
        RuntimeError,
        match="APDL_TEST_OUTBOX_FLOAT must be a finite number",
    ):
        outbox._environment_float(
            "APDL_TEST_OUTBOX_FLOAT",
            1.0,
            minimum=0.0,
        )


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class RecordingClaimConn:
    def __init__(self):
        self.sql = ""

    def transaction(self):
        return _Context(None)

    async def fetchrow(self, sql: str):
        self.sql = sql
        return None


class RecordingClaimPool:
    def __init__(self):
        self.conn = RecordingClaimConn()

    def acquire(self):
        return _Context(self.conn)


class RecordingCleanupConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Context(None)

    async def fetch(self, sql: str, *args):
        self.calls.append((sql, args))
        return [{"id": 1}, {"id": 2}]


class RecordingCleanupPool:
    def __init__(self):
        self.conn = RecordingCleanupConn()

    def acquire(self):
        return _Context(self.conn)


class RecordingResolutionConn:
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Context(None)

    async def fetchrow(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.row

    async def execute(self, sql: str, *args):
        self.calls.append((sql, args))
        return "UPDATE 1"


class RecordingResolutionPool:
    def __init__(self, row):
        self.conn = RecordingResolutionConn(row)

    def acquire(self):
        return _Context(self.conn)


def exposure_row(*, attempts: int) -> dict:
    return {
        "id": 41,
        "project_id": "apdl",
        "kind": "exposure",
        "attempts": attempts,
        "payload": {
            "stream_key": "events:raw:apdl",
            "event": {
                "event": "$feature_flag_exposure",
                "type": "track",
                "timestamp": "2026-07-22T10:00:00Z",
                "server_timestamp": "2026-07-22T10:00:00Z",
                "message_id": "eval-1",
                "session_id": "server:eval-1",
                "user_id": "user-1",
                "context": {"library": {"name": "apdl-config"}},
                "properties": {"flag_key": "checkout"},
            },
        },
    }


@pytest.mark.asyncio
async def test_redis_failure_is_recorded_then_same_outbox_row_retries(
    monkeypatch,
):
    claim = AsyncMock(side_effect=[exposure_row(attempts=1), exposure_row(attempts=2)])
    failed = AsyncMock()
    processed = AsyncMock()
    monkeypatch.setattr(outbox, "claim_next", claim)
    monkeypatch.setattr(outbox, "mark_failed", failed)
    monkeypatch.setattr(outbox, "mark_processed", processed)
    monkeypatch.setattr(outbox, "quarantine_exhausted", AsyncMock(return_value=0))

    redis = AsyncMock()
    redis.eval = AsyncMock(
        side_effect=[RuntimeError("redis down"), [1, 1, b"1234567890-0"]]
    )
    pool = object()
    broadcaster = AsyncMock()

    assert await outbox.drain_once(pool, redis, broadcaster) is True
    failed.assert_awaited_once_with(pool, 41, 1, "redis down")
    processed.assert_not_awaited()

    assert await outbox.drain_once(pool, redis, broadcaster) is True
    processed.assert_awaited_once_with(pool, 41)
    assert redis.eval.await_count == 2


@pytest.mark.asyncio
async def test_exposure_uses_atomic_bounded_stream_admission():
    redis = AsyncMock()
    redis.eval.return_value = [1, 42, b"1234567890-0"]
    row = exposure_row(attempts=1)

    await outbox.deliver(row, redis, AsyncMock())

    event_json = json.dumps(
        row["payload"]["event"],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    redis.eval.assert_awaited_once_with(
        outbox._BOUNDED_XADD_LUA,
        1,
        "events:raw:apdl",
        1,
        outbox.EVENT_STREAM_MAX_ENTRIES,
        event_json,
    )
    redis.xadd.assert_not_awaited()
    assert "XLEN" in outbox._BOUNDED_XADD_LUA
    assert "MAXLEN" not in outbox._BOUNDED_XADD_LUA


@pytest.mark.asyncio
async def test_exposure_without_server_timestamp_is_permanently_rejected():
    row = exposure_row(attempts=1)
    del row["payload"]["event"]["server_timestamp"]

    with pytest.raises(outbox.PermanentDeliveryError, match="noncanonical fields"):
        await outbox.deliver(row, AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_stream_overload_keeps_outbox_row_pending_for_retry(
    monkeypatch,
    caplog,
):
    claim = AsyncMock(return_value=exposure_row(attempts=3))
    failed = AsyncMock()
    processed = AsyncMock()
    monkeypatch.setattr(outbox, "claim_next", claim)
    monkeypatch.setattr(outbox, "mark_failed", failed)
    monkeypatch.setattr(outbox, "mark_processed", processed)
    monkeypatch.setattr(outbox, "quarantine_exhausted", AsyncMock(return_value=0))
    redis = AsyncMock()
    redis.eval.return_value = [0, outbox.EVENT_STREAM_MAX_ENTRIES]

    with caplog.at_level(logging.ERROR, logger=outbox.__name__):
        assert await outbox.drain_once(object(), redis, AsyncMock()) is True

    processed.assert_not_awaited()
    failed.assert_awaited_once()
    pool, row_id, attempts, error = failed.await_args.args
    assert row_id == 41
    assert attempts == 3
    assert "durability capacity" in error
    overload = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "event_stream_overloaded"
    )
    assert overload.stream_key == "events:raw:apdl"
    assert overload.outstanding_entries == outbox.EVENT_STREAM_MAX_ENTRIES
    assert overload.max_entries == outbox.EVENT_STREAM_MAX_ENTRIES


@pytest.mark.asyncio
async def test_stream_pressure_emits_structured_alert(caplog):
    redis = AsyncMock()
    redis.eval.return_value = [
        1,
        outbox.EVENT_STREAM_ALERT_ENTRIES,
        b"1234567890-0",
    ]

    with caplog.at_level(logging.WARNING, logger=outbox.__name__):
        await outbox.deliver(exposure_row(attempts=1), redis, AsyncMock())

    pressure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "event_stream_pressure"
    )
    assert pressure.outstanding_entries == outbox.EVENT_STREAM_ALERT_ENTRIES
    assert pressure.alert_entries == outbox.EVENT_STREAM_ALERT_ENTRIES
    assert pressure.project_id == "apdl"
    assert pressure.outbox_id == 41


@pytest.mark.asyncio
async def test_failed_head_preserves_global_config_order_per_project():
    pool = RecordingClaimPool()

    assert await outbox.claim_next(pool) is None

    sql = pool.conn.sql
    assert "NOT EXISTS" in sql
    assert "earlier.project_id = pending.project_id" in sql
    assert "pending.kind IN" in sql
    assert "'flag_change', 'experiment_change'" in sql
    assert "earlier.kind IN" in sql
    assert "pending.kind = 'exposure'" in sql
    assert "earlier.kind = 'exposure'" in sql
    assert "earlier.processed_at IS NULL" in sql
    assert "earlier.quarantined_at IS NULL" in sql
    assert "pending.quarantined_at IS NULL" in sql
    assert f"pending.attempts < {outbox.MAX_DELIVERY_ATTEMPTS}" in sql
    assert "earlier.id < pending.id" in sql
    # A failed config head blocks later flag and experiment rows for only that
    # project. Exposure durability remains independent from config delivery.
    assert "earlier.available_at" not in sql


@pytest.mark.asyncio
async def test_flag_change_invalidates_then_broadcasts_versioned_payload(monkeypatch):
    invalidate = AsyncMock()
    monkeypatch.setattr(outbox.redis_cache, "invalidate_flags", invalidate)
    broadcaster = AsyncMock()
    row = {
        "project_id": "apdl",
        "kind": "flag_change",
        "payload": {
            "event_type": "flag_update",
            "project_version": 19,
            "data": {
                "action": "flag_updated",
                "key": "checkout",
                "version": 7,
            },
        },
    }
    redis = object()

    await outbox.deliver(row, redis, broadcaster)

    invalidate.assert_awaited_once_with(redis, "apdl", 19)
    project_id, event_type, raw = broadcaster.broadcast.await_args.args
    assert project_id == "apdl"
    assert event_type == "flag_update"
    assert json.loads(raw)["version"] == 7
    assert broadcaster.broadcast.await_args.kwargs == {"project_version": 19}


@pytest.mark.asyncio
async def test_config_change_with_invalid_project_version_fails_closed(monkeypatch):
    monkeypatch.setattr(outbox.redis_cache, "invalidate_flags", AsyncMock())
    row = {
        "project_id": "apdl",
        "kind": "flag_change",
        "payload": {
            "event_type": "flag_update",
            "project_version": "19",
            "data": {},
        },
    }

    with pytest.raises(ValueError, match="project_version"):
        await outbox.deliver(row, object(), AsyncMock())


@pytest.mark.asyncio
async def test_no_due_outbox_row_is_idle(monkeypatch):
    claim = AsyncMock(return_value=None)
    sweep = AsyncMock(return_value=0)
    monkeypatch.setattr(outbox, "claim_next", claim)
    monkeypatch.setattr(outbox, "quarantine_exhausted", sweep)

    assert await outbox.drain_once(object(), object(), object()) is False
    claim.assert_awaited_once()
    sweep.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_outbox_kind_fails_closed():
    with pytest.raises(ValueError, match="Unsupported"):
        await outbox.deliver(
            {"project_id": "apdl", "kind": "unknown", "payload": {}},
            object(),
            object(),
        )


@pytest.mark.asyncio
async def test_malformed_payload_is_quarantined_as_permanent(monkeypatch):
    row = exposure_row(attempts=1)
    row["payload"] = {"event": {}}
    pool = object()
    quarantine = AsyncMock()
    failed = AsyncMock()
    processed = AsyncMock()
    monkeypatch.setattr(outbox, "claim_next", AsyncMock(return_value=row))
    monkeypatch.setattr(outbox, "mark_quarantined", quarantine)
    monkeypatch.setattr(outbox, "mark_failed", failed)
    monkeypatch.setattr(outbox, "mark_processed", processed)
    monkeypatch.setattr(outbox, "quarantine_exhausted", AsyncMock(return_value=0))

    assert await outbox.drain_once(pool, object(), object()) is True

    quarantine.assert_awaited_once_with(
        pool,
        41,
        failure_class="permanent",
        failure_code="invalid_payload",
        error="Config outbox payload has noncanonical fields",
    )
    failed.assert_not_awaited()
    processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_retryable_failure_at_attempt_cap_is_quarantined(monkeypatch):
    row = exposure_row(attempts=outbox.MAX_DELIVERY_ATTEMPTS)
    pool = object()
    redis = AsyncMock()
    redis.eval.side_effect = RuntimeError("redis unavailable")
    quarantine = AsyncMock()
    monkeypatch.setattr(outbox, "claim_next", AsyncMock(return_value=row))
    monkeypatch.setattr(outbox, "mark_quarantined", quarantine)
    monkeypatch.setattr(outbox, "mark_failed", AsyncMock())
    monkeypatch.setattr(outbox, "mark_processed", AsyncMock())
    monkeypatch.setattr(outbox, "quarantine_exhausted", AsyncMock(return_value=0))

    assert await outbox.drain_once(pool, redis, AsyncMock()) is True

    quarantine.assert_awaited_once_with(
        pool,
        41,
        failure_class="attempts_exhausted",
        failure_code="delivery_attempts_exhausted",
        error="redis unavailable",
    )


@pytest.mark.asyncio
async def test_abandoned_final_attempt_is_terminalized_after_claim_timeout():
    pool = AsyncMock()
    pool.execute.return_value = "UPDATE 1"

    assert await outbox.quarantine_exhausted(pool) == 1

    sql = pool.execute.await_args.args[0]
    assert "attempts >= $1" in sql
    assert "failure_class = 'attempts_exhausted'" in sql
    assert "failure_code = 'delivery_attempts_exhausted'" in sql
    assert pool.execute.await_args.args[1:] == (
        outbox.MAX_DELIVERY_ATTEMPTS,
        outbox.CLAIM_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_quarantine_inspection_uses_project_keyset_page():
    pool = AsyncMock()
    pool.fetch.return_value = [
        {
            "id": row_id,
            "project_id": "apdl",
            "kind": "exposure",
            "payload": '{"event":{}}',
            "attempts": 8,
            "last_error": "timeout",
            "failure_class": "attempts_exhausted",
            "failure_code": "delivery_attempts_exhausted",
            "created_at": "2026-07-20T00:00:00+00:00",
            "quarantined_at": "2026-07-21T00:00:00+00:00",
        }
        for row_id in (43, 42, 41)
    ]

    entries, cursor = await outbox.list_quarantined(
        pool,
        "apdl",
        limit=2,
        before_id=50,
    )

    assert [entry["id"] for entry in entries] == [43, 42]
    assert entries[0]["payload"] == {"event": {}}
    assert cursor == 42
    sql, project_id, before_id, query_limit = pool.fetch.await_args.args
    assert "project_id = $1" in sql
    assert "id < $2" in sql
    assert "ORDER BY id DESC" in sql
    assert (project_id, before_id, query_limit) == ("apdl", 50, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "mutation"),
    [
        ("replay", "UPDATE config_outbox"),
        ("discard", "DELETE FROM config_outbox"),
    ],
)
async def test_quarantine_resolution_is_atomic_and_audited(action, mutation):
    row = {
        "id": 41,
        "project_id": "apdl",
        "kind": "exposure",
        "payload": {"event": {"message_id": "eval-1"}},
        "attempts": 8,
        "last_error": "timeout",
        "failure_class": "attempts_exhausted",
        "failure_code": "delivery_attempts_exhausted",
        "created_at": "2026-07-20T00:00:00+00:00",
        "quarantined_at": "2026-07-21T00:00:00+00:00",
    }
    pool = RecordingResolutionPool(row)

    entry = await outbox.resolve_quarantined(
        pool,
        "apdl",
        41,
        action=action,
        actor="credential:operator",
        reason="dependency repaired",
    )

    assert entry is not None
    assert entry["id"] == 41
    assert len(pool.conn.calls) == 3
    locked, audit, resolved = pool.conn.calls
    assert "FOR UPDATE" in locked[0]
    assert "project_id = $2" in locked[0]
    assert "INSERT INTO config_outbox_operator_log" in audit[0]
    assert audit[1][2:5] == (
        action,
        "credential:operator",
        "dependency repaired",
    )
    assert len(audit[1][-1]) == 64
    assert mutation in resolved[0]
    assert resolved[1] == (41, "apdl")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["flag_change", "experiment_change"])
async def test_config_change_replay_is_rejected_before_audit_or_mutation(kind):
    row = {
        "id": 41,
        "project_id": "apdl",
        "kind": kind,
        "payload": {"event_type": "flag_update", "project_version": 2, "data": {}},
        "attempts": 8,
        "last_error": "invalid",
        "failure_class": "permanent",
        "failure_code": "invalid_payload",
        "created_at": "2026-07-20T00:00:00+00:00",
        "quarantined_at": "2026-07-21T00:00:00+00:00",
    }
    pool = RecordingResolutionPool(row)

    with pytest.raises(outbox.UnsafeQuarantineReplay) as exc_info:
        await outbox.resolve_quarantined(
            pool,
            "apdl",
            41,
            action="replay",
            actor="credential:operator",
            reason="dependency repaired",
        )

    assert exc_info.value.kind == kind
    assert len(pool.conn.calls) == 1
    assert "FOR UPDATE" in pool.conn.calls[0][0]


@pytest.mark.asyncio
async def test_cleanup_uses_separate_bounded_skip_locked_horizons():
    pool = RecordingCleanupPool()

    assert await outbox.cleanup_once(pool) == {
        "processed": 2,
        "quarantined": 2,
        "receipts": 2,
    }

    assert len(pool.conn.calls) == 3
    processed, quarantined, receipts = pool.conn.calls
    for sql, args in pool.conn.calls:
        assert "FOR UPDATE" in sql
        assert "SKIP LOCKED" in sql
        assert "LIMIT $1" in sql
        assert args[0] == outbox.CLEANUP_BATCH_SIZE
    assert "ORDER BY processed_at, id" in processed[0]
    assert processed[1][1] == outbox.PROCESSED_RETENTION_SECONDS
    assert "ORDER BY quarantined_at, id" in quarantined[0]
    assert quarantined[1][1] == outbox.QUARANTINED_RETENTION_SECONDS
    assert "FROM config_exposure_receipts AS receipt" in receipts[0]
    assert "NOT EXISTS" in receipts[0]
    assert "outbox.kind = 'exposure'" in receipts[0]
    assert receipts[1][1] == outbox.EXPOSURE_RECEIPT_RETENTION_SECONDS


@pytest.mark.asyncio
async def test_worker_invokes_cleanup_before_delivery(monkeypatch):
    cleanup = AsyncMock(
        return_value={"processed": 0, "quarantined": 0, "receipts": 0}
    )
    drain = AsyncMock(side_effect=asyncio.CancelledError)
    sweep = AsyncMock(return_value=0)
    monkeypatch.setattr(outbox, "cleanup_once", cleanup)
    monkeypatch.setattr(outbox, "drain_once", drain)
    monkeypatch.setattr(outbox, "quarantine_exhausted", sweep)

    with pytest.raises(asyncio.CancelledError):
        await outbox.run_worker(object(), object(), object())

    cleanup.assert_awaited_once()
    sweep.assert_awaited_once()
    drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_continues_full_cleanup_batches_without_waiting(monkeypatch):
    cleanup = AsyncMock(
        side_effect=[
            {
                "processed": outbox.CLEANUP_BATCH_SIZE,
                "quarantined": 0,
                "receipts": 0,
            },
            asyncio.CancelledError,
        ]
    )
    drain = AsyncMock(return_value=False)
    sweep = AsyncMock(return_value=0)
    sleep = AsyncMock()
    monkeypatch.setattr(outbox, "cleanup_once", cleanup)
    monkeypatch.setattr(outbox, "drain_once", drain)
    monkeypatch.setattr(outbox, "quarantine_exhausted", sweep)
    monkeypatch.setattr(outbox.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await outbox.run_worker(object(), object(), object())

    assert cleanup.await_count == 2
    sweep.assert_awaited_once()
    drain.assert_awaited_once()
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_metrics_use_estimates_and_bounded_threshold_probe():
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "estimated_pending_count": 4,
        "estimated_processed_count": 12,
        "estimated_quarantined_count": 1,
        "estimated_receipt_count": 350,
        "quarantined_threshold_exceeded": True,
        "oldest_pending_age_seconds": 45.5,
        "oldest_processed_age_seconds": 3600.0,
        "oldest_quarantined_age_seconds": 12.25,
        "oldest_receipt_age_seconds": 7200.0,
    }

    metrics = await outbox.metrics_snapshot(conn)

    assert metrics == {
        "estimated_pending_count": 4,
        "estimated_processed_count": 12,
        "estimated_quarantined_count": 1,
        "estimated_receipt_count": 350,
        "quarantined_threshold_exceeded": True,
        "oldest_pending_age_seconds": 45.5,
        "oldest_processed_age_seconds": 3600.0,
        "oldest_quarantined_age_seconds": 12.25,
        "oldest_receipt_age_seconds": 7200.0,
    }
    sql = conn.fetchrow.await_args.args[0]
    assert "FROM config_outbox" in sql
    assert "FROM config_exposure_receipts" in sql
    assert "pg_stat_user_tables" in sql
    assert "quarantined_at IS NULL" in sql
    assert "count(*) FILTER" not in sql
    assert "count(*) AS pending_count" not in sql
    assert "count(*) AS quarantined_count" not in sql
    assert "OFFSET $1" in sql
    assert "ORDER BY created_at, id" in sql
    assert "idx_config_outbox_cleanup_processed" in sql
    assert conn.fetchrow.await_args.args[1] == outbox.READINESS_MAX_QUARANTINED_ROWS


@pytest.mark.parametrize(
    ("metrics_update", "reason"),
    [
        (
            {
                "oldest_pending_age_seconds": (
                    outbox.READINESS_MAX_PENDING_AGE_SECONDS + 1
                )
            },
            "oldest_pending_age_exceeded",
        ),
        (
            {"quarantined_threshold_exceeded": True},
            "quarantined_rows_exceeded",
        ),
        (
            {
                "oldest_processed_age_seconds": (
                    outbox.PROCESSED_RETENTION_SECONDS
                    + outbox.CLEANUP_READINESS_GRACE_SECONDS
                    + 1
                )
            },
            "processed_cleanup_overdue",
        ),
        (
            {
                "oldest_quarantined_age_seconds": (
                    outbox.QUARANTINED_RETENTION_SECONDS
                    + outbox.CLEANUP_READINESS_GRACE_SECONDS
                    + 1
                )
            },
            "quarantined_cleanup_overdue",
        ),
        (
            {
                "oldest_receipt_age_seconds": (
                    outbox.EXPOSURE_RECEIPT_RETENTION_SECONDS
                    + outbox.CLEANUP_READINESS_GRACE_SECONDS
                    + 1
                )
            },
            "receipt_cleanup_overdue",
        ),
    ],
)
def test_outbox_readiness_degrades_past_delivery_thresholds(
    metrics_update,
    reason,
):
    metrics = {**outbox.empty_metrics(), **metrics_update}

    readiness = outbox.readiness_snapshot(metrics)

    assert readiness["status"] == "degraded"
    assert readiness["degraded_reasons"] == [reason]


def test_exposure_receipts_outlive_clickhouse_event_retention():
    assert (
        outbox.EXPOSURE_RECEIPT_RETENTION_SECONDS
        > outbox.CLICKHOUSE_EVENT_RETENTION_MAX_SECONDS
    )
