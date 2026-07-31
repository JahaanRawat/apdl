from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.store import postgres


CONFIG_MIGRATION_SQL = (
    Path(__file__).resolve().parents[3]
    / "pipeline"
    / "postgres"
    / "migrations"
    / "001_initial_schema.sql"
).read_text()


def _table_definition(sql: str, table: str) -> str:
    start = sql.index(f"CREATE TABLE public.{table} (")
    return sql[start : sql.index("\n);", start) + 3]


@pytest.mark.asyncio
async def test_get_flags_filters_client_visible_modes():
    pool = RecordingPool()

    await postgres.get_flags(pool, "apdl", client_visible_only=True)

    assert "evaluation_mode IN ('client', 'both')" in pool.sql
    assert "client_exposed" not in pool.sql
    assert "SELECT *" not in pool.sql
    assert "default_variant" in pool.sql
    assert "variants" in pool.sql
    assert "default_value" not in pool.sql
    assert pool.args == ("apdl",)


@pytest.mark.asyncio
async def test_get_flag_uses_explicit_canonical_projection():
    pool = RecordingFetchRowPool(row=None)

    await postgres.get_flag(pool, "apdl", "checkout")

    assert "SELECT *" not in pool.sql
    assert "default_variant" in pool.sql
    assert "variants" in pool.sql
    assert "default_value" not in pool.sql
    assert pool.args == ("apdl", "checkout")


@pytest.mark.asyncio
async def test_flag_snapshot_reads_version_and_rows_in_repeatable_read():
    conn = SnapshotConnection()
    pool = SnapshotPool(conn)

    flags, project_version = await postgres.get_flag_snapshot(
        pool,
        "apdl",
        client_visible_only=True,
    )

    assert project_version == 8
    assert flags == []
    assert conn.transaction_kwargs == {
        "isolation": "repeatable_read",
        "readonly": True,
    }
    assert "config_project_versions" in conn.version_sql
    assert "evaluation_mode IN ('client', 'both')" in conn.flags_sql


def test_row_to_flag_omits_obsolete_columns_from_legacy_records():
    row = {
        **make_row(),
        "default_value": False,
        "variant_type": "boolean",
        "variants_json": "[]",
        "rollout_percentage": 100,
        "client_exposed": True,
    }

    flag = postgres._row_to_flag(row)

    assert "default_value" not in flag
    assert "variant_type" not in flag
    assert "variants_json" not in flag
    assert "rollout_percentage" not in flag
    assert "client_exposed" not in flag
    assert flag["default_variant"] == "control"
    assert flag["variants"] == [
        {"key": "control", "weight": 1},
        {"key": "treatment", "weight": 1},
    ]


def test_create_flags_table_defines_only_canonical_variant_columns():
    create_sql = _table_definition(CONFIG_MIGRATION_SQL, "flags")

    assert "default_variant text DEFAULT 'control'::text NOT NULL" in create_sql
    assert "variants jsonb DEFAULT" in create_sql
    assert "default_value" not in create_sql
    assert "variant_type" not in create_sql
    assert "variants_json" not in create_sql
    assert "fallthrough jsonb DEFAULT" in create_sql
    assert '"value"' not in create_sql
    assert "flags_fallthrough_rollout_only_check" in create_sql


def test_baseline_contains_no_obsolete_flag_shape_or_upgrade_rewrite():
    assert "default_value" not in CONFIG_MIGRATION_SQL
    assert "variant_type" not in CONFIG_MIGRATION_SQL
    assert "feature_flags_legacy" not in CONFIG_MIGRATION_SQL
    assert "DROP COLUMN" not in CONFIG_MIGRATION_SQL
    assert "flags_fallthrough_rollout_only_check" in CONFIG_MIGRATION_SQL


@pytest.mark.asyncio
async def test_get_experiments_uses_explicit_projection():
    pool = RecordingPool()

    await postgres.get_experiments(pool, "apdl")

    assert "SELECT *" not in pool.sql
    assert "flag_key" in pool.sql
    assert "default_variant" in pool.sql
    assert "primary_metric_json" in pool.sql
    assert "statistical_plan" in pool.sql
    assert pool.args == ("apdl",)


@pytest.mark.asyncio
async def test_get_experiment_uses_explicit_projection():
    pool = RecordingFetchRowPool(row=None)

    await postgres.get_experiment(pool, "apdl", "checkout")

    assert "SELECT *" not in pool.sql
    assert "flag_key" in pool.sql
    assert "archived_at IS NULL" not in pool.sql
    assert pool.args == ("apdl", "checkout")


@pytest.mark.asyncio
async def test_get_due_experiments_uses_database_timestamp_comparison():
    pool = RecordingPool()
    now = object()

    await postgres.get_due_experiments(pool, now)

    assert "status = 'scheduled' AND start_date <= $1" in pool.sql
    assert "status = 'running' AND end_date <= $1" in pool.sql
    assert "archived_at IS NULL" in pool.sql
    assert pool.args == (now,)


def test_row_to_experiment_includes_canonical_columns():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    created = datetime(2026, 5, 1, tzinfo=timezone.utc)
    updated = datetime(2026, 5, 2, tzinfo=timezone.utc)
    row = {
        "key": "checkout",
        "project_id": "apdl",
        "status": "running",
        "description": "d",
        "flag_key": "checkout",
        "bucket_by": "anonymous_id",
        "default_variant": "control",
        "variants_json": "[]",
        "targeting_rules_json": "[]",
        "primary_metric_json": "{}",
        "statistical_plan": None,
        "traffic_percentage": 100.0,
        "minimum_exposure_config_version": 2,
        "start_date": start,
        "end_date": end,
        "version": 3,
        "created_at": created,
        "updated_at": updated,
        "archived_at": None,
        "archived_by": None,
    }

    exp = postgres._row_to_experiment(row)

    assert exp["flag_key"] == "checkout"
    assert exp["bucket_by"] == "anonymous_id"
    assert exp["default_variant"] == "control"
    assert exp["primary_metric_json"] == "{}"
    assert exp["statistical_plan"] is None
    assert exp["traffic_percentage"] == 100.0
    assert exp["minimum_exposure_config_version"] == 2
    assert exp["version"] == 3
    assert exp["start_date"] is start
    assert exp["end_date"] is end
    assert exp["created_at"] is created
    assert exp["updated_at"] is updated
    assert exp["archived_at"] is None
    assert exp["archived_by"] is None


def test_create_experiments_table_defines_canonical_columns():
    create_sql = _table_definition(CONFIG_MIGRATION_SQL, "experiments")

    assert "flag_key text DEFAULT ''::text NOT NULL" in create_sql
    assert "default_variant text DEFAULT 'control'::text NOT NULL" in create_sql
    assert "primary_metric_json text DEFAULT '{}'::text NOT NULL" in create_sql
    assert "experiments_status_check" in create_sql
    assert "bucket_by" in postgres.EXPERIMENT_COLUMNS


def test_baseline_installs_final_experiment_constraints_and_triggers():
    assert "experiments_status_check" in CONFIG_MIGRATION_SQL
    assert "experiments_variants_canonical_check" in CONFIG_MIGRATION_SQL
    assert "experiments_targeting_rules_check" in CONFIG_MIGRATION_SQL
    assert "experiments_enforce_enrollment_immutability" in CONFIG_MIGRATION_SQL
    assert "experiments_enforce_archive_lifecycle" in CONFIG_MIGRATION_SQL


class RecordingPool:
    sql: str = ""
    args: tuple = ()

    async def fetch(self, sql: str, *args):
        self.sql = sql
        self.args = args
        return []


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class SnapshotConnection:
    def __init__(self):
        self.transaction_kwargs = None
        self.version_sql = ""
        self.flags_sql = ""

    def transaction(self, **kwargs):
        self.transaction_kwargs = kwargs
        return _Context(None)

    async def fetchval(self, sql: str, *args):
        self.version_sql = sql
        assert args == ("apdl",)
        return 8

    async def fetch(self, sql: str, *args):
        self.flags_sql = sql
        assert args == ("apdl",)
        return []


class SnapshotPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Context(self.conn)


class RecordingFetchRowPool:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.args = ()

    async def fetchrow(self, sql: str, *args):
        self.sql = sql
        self.args = args
        return self.row


def make_row(overrides: dict | None = None) -> dict:
    row = {
        "key": "checkout",
        "project_id": "apdl",
        "name": "Checkout",
        "state": "draft",
        "owners": [],
        "review_by": None,
        "enabled": False,
        "description": "",
        "default_variant": "control",
        "variants": [
            {"key": "control", "weight": 1},
            {"key": "treatment", "weight": 1},
        ],
        "rules": [],
        "fallthrough": {
            "rollout": {"percentage": 0.0, "bucket_by": "user_id"},
        },
        "salt": "salt_123",
        "evaluation_mode": "client",
        "auto_disable": False,
        "guardrails": [],
        "disabled_reason": "",
        "disabled_by": "",
        "disabled_at": None,
        "version": 1,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "archived_at": None,
    }
    if overrides:
        row.update(overrides)
    return row
