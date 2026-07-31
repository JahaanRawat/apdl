"""Static contracts for the canonical fresh-install ClickHouse baseline."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = ROOT / "pipeline" / "clickhouse" / "migrations"
BACKFILLS_DIR = ROOT / "pipeline" / "clickhouse" / "backfills"
BASELINE = (MIGRATIONS_DIR / "001_initial_schema.sql").read_text()
MIGRATION_ENGINE = (ROOT / "pipeline" / "clickhouse" / "migrate.py").read_text()
CLICKHOUSE_INIT = (ROOT / "scripts" / "init-clickhouse.sh").read_text()


def _definition(prefix: str, name: str, next_prefix: str = "CREATE") -> str:
    start = BASELINE.index(f"{prefix} {name}")
    try:
        end = BASELINE.index(f"\n\n{next_prefix}", start)
    except ValueError:
        end = len(BASELINE)
    return BASELINE[start:end]


def test_clickhouse_has_one_canonical_baseline_and_no_initial_backfill() -> None:
    assert sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql")) == [
        "001_initial_schema.sql"
    ]
    assert list(BACKFILLS_DIR.glob("*.sql")) == []
    assert "Upgrade and retained-data backfill operations are intentionally absent" in BASELINE


def test_events_are_receipt_deduplicated_and_stream_provenance_bound() -> None:
    events = _definition("CREATE TABLE IF NOT EXISTS", "events")

    assert "`project_id` String" in events
    assert "`message_id` String" in events
    assert "`received_at` DateTime64(3)" in events
    assert "`source_stream_id_ms` UInt64" in events
    assert "ENGINE = ReplacingMergeTree(received_at)" in events
    assert "ORDER BY (project_id, message_id)" in events
    assert "TTL toDate(received_at) + toIntervalMonth(12)" in events


def test_every_identity_bearing_table_uses_the_canonical_retention_boundary() -> None:
    for table in (
        "sessions",
        "experiment_event_deliveries",
        "feature_flag_exposures",
        "frontend_health_events",
        "identity_alias_assertions",
    ):
        definition = _definition("CREATE TABLE IF NOT EXISTS", table)
        assert "PARTITION BY project_id" in definition
        assert "TTL toDate(received_at) + toIntervalMonth(12)" in definition


def test_materialized_views_project_only_from_the_canonical_event_stream() -> None:
    assert BASELINE.count("CREATE MATERIALIZED VIEW IF NOT EXISTS") == 4
    for view in (
        "feature_flag_exposures_mv",
        "frontend_health_events_mv",
        "identity_alias_assertions_mv",
        "experiment_event_deliveries_mv",
    ):
        definition = _definition("CREATE MATERIALIZED VIEW IF NOT EXISTS", view)
        assert "FROM events" in definition

    assert "event_name = '$feature_flag_exposure'" in BASELINE
    assert "event_name IN ('$frontend_error', '$web_vital')" in BASELINE
    assert "(event_type = 'identify')" in BASELINE
    assert "WHERE source_stream_id != ''" in BASELINE


def test_identity_resolution_is_conflict_preserving_and_retention_reversible() -> None:
    resolved = _definition("CREATE VIEW IF NOT EXISTS", "resolved_identity_aliases")

    assert "FROM identity_alias_assertions" in resolved
    assert "FINAL" in resolved
    assert "if(min(user_id) = max(user_id), min(user_id), '')" in resolved
    assert "min(user_id) != max(user_id) AS has_conflict" in resolved
    assert "identity_alias_resolution_state" not in BASELINE


def test_baseline_contains_no_upgrade_or_prototype_objects() -> None:
    for retired in (
        "events_v2",
        "events_dlq_v2",
        "decisions_v2",
        "feeds_v2",
        "__apdl_migration",
        "EXCHANGE TABLES",
        "INSERT INTO",
        "DROP TABLE",
    ):
        assert retired not in BASELINE


def test_clickhouse_runner_retains_exact_ledger_and_backfill_authority() -> None:
    assert "pipeline/clickhouse/migrate.py" in CLICKHOUSE_INIT
    assert "apdl_schema_migrations" in MIGRATION_ENGINE
    assert "apdl_schema_backfills" in MIGRATION_ENGINE
    assert "checksum drift" in MIGRATION_ENGINE
    assert "Migrations must be contiguous" not in MIGRATION_ENGINE
    assert "must be contiguous from 001" in MIGRATION_ENGINE
