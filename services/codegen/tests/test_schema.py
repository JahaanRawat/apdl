"""Tests for fail-fast Codegen schema validation."""

from pathlib import Path

import pytest

from app.db import MIGRATION_NAME, MIGRATION_VERSION, REQUIRED_COLUMNS, assert_schema_ready


ROOT = Path(__file__).resolve().parents[3]
BASELINE = (ROOT / "pipeline/postgres/migrations/001_initial_schema.sql").read_text()


class FakeConn:
    def __init__(
        self,
        *,
        ledger_exists: bool = True,
        migration_name: str | None = MIGRATION_NAME,
        columns=REQUIRED_COLUMNS,
    ):
        self.ledger_exists = ledger_exists
        self.migration_name = migration_name
        self.columns = set(columns)

    async def fetchval(self, sql: str, *args):
        if "to_regclass" in sql:
            return self.ledger_exists
        if "apdl_schema_migrations" in sql:
            return self.migration_name
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args):
        assert "information_schema.columns" in sql
        return [
            {"table_name": table, "column_name": column}
            for table, column in self.columns
        ]


@pytest.mark.asyncio
async def test_accepts_complete_baseline_schema():
    await assert_schema_ready(FakeConn())


def test_startup_requires_canonical_initial_schema():
    assert MIGRATION_VERSION == 1
    assert MIGRATION_NAME == "001_initial_schema.sql"
    assert (
        "admin_project_execution_authorizations",
        "authorization_source",
    ) in REQUIRED_COLUMNS
    assert all("legacy" not in table and "legacy" not in column for table, column in REQUIRED_COLUMNS)


@pytest.mark.asyncio
async def test_rejects_missing_migration_ledger():
    with pytest.raises(RuntimeError, match="migration ledger is missing"):
        await assert_schema_ready(FakeConn(ledger_exists=False))


@pytest.mark.asyncio
async def test_rejects_database_without_initial_schema():
    with pytest.raises(RuntimeError, match="001_initial_schema.sql"):
        await assert_schema_ready(FakeConn(migration_name=None))


@pytest.mark.asyncio
async def test_rejects_incomplete_schema_at_startup():
    columns = REQUIRED_COLUMNS - {("github_repository_grants", "repository_id")}
    with pytest.raises(RuntimeError, match="github_repository_grants.repository_id"):
        await assert_schema_ready(FakeConn(columns=columns))


@pytest.mark.asyncio
async def test_rejects_missing_execution_authorization_contract_at_startup():
    columns = REQUIRED_COLUMNS - {
        ("admin_project_execution_authorizations", "actor")
    }
    with pytest.raises(
        RuntimeError,
        match="admin_project_execution_authorizations.actor",
    ):
        await assert_schema_ready(FakeConn(columns=columns))


def test_codegen_startup_contains_no_postgres_ddl():
    app_dir = Path(__file__).parents[1] / "app"
    main_source = (app_dir / "main.py").read_text()
    db_source = (app_dir / "db.py").read_text()
    assert "CREATE TABLE" not in main_source
    assert "ALTER TABLE" not in main_source
    assert "CREATE TABLE" not in db_source
    assert "ALTER TABLE" not in db_source


def test_baseline_defines_strict_codegen_authority_without_archives():
    changesets_start = BASELINE.index("CREATE TABLE public.codegen_changesets (")
    changesets = BASELINE[changesets_start : BASELINE.index("\n);", changesets_start)]

    assert "idempotency_key text NOT NULL" in changesets
    assert "idempotency_request_sha256 character(64) NOT NULL" in changesets
    assert "repository_grant_id text" in changesets
    assert "control_metadata jsonb" in changesets
    assert "publication_authorization jsonb" in changesets
    assert "publication_authorization_legacy" not in changesets
    assert "publication_authorization_segmentless_legacy" not in changesets
    assert "publication_authorization_egress_unattested_legacy" not in changesets
    assert "publication_authorization_pre_tenant_legacy" not in changesets
    assert "llm_execution_snapshot_v1_legacy" not in changesets
    assert "llm_execution_snapshot jsonb" in changesets
    assert "codegen_changesets_publication_authorization_check" in changesets
    assert "tenant_publication_authorization@1" in changesets
    assert "codegen_llm_execution_snapshot@2" in BASELINE
    assert "development_publication_authorization@1" in BASELINE


def test_baseline_defines_project_scoped_codegen_llm_authority():
    for table in (
        "llm_vault_provider_credentials",
        "llm_vault_connection_consumers",
        "codegen_project_provider_connections",
        "codegen_project_provider_models",
        "codegen_project_model_assignments",
        "codegen_llm_attempts",
    ):
        assert f"CREATE TABLE public.{table}" in BASELINE

    assert "codegen_project_provider_credentials" not in BASELINE
    assert "llm_project_provider_credentials" not in BASELINE
    assert "llm_vault_provider_credentials_one_active_idx" in BASELINE
    assert "codegen_project_provider_connections_vault_credential_fk" in BASELINE
    assert "codegen_project_model_assignments_validate" in BASELINE
    assert "codegen_llm_attempts_validate_snapshot" in BASELINE
    assert "('public.codegen_llm_attempts')" in BASELINE


def test_baseline_defines_append_only_pr_publication_recovery():
    assert "CREATE TABLE public.codegen_pull_request_publication_events" in BASELINE
    assert "pull_request_publication_intent@1" in BASELINE
    assert "pull_request_create_accepted@1" in BASELINE
    assert "pull_request_identity_validated@1" in BASELINE
    assert "uq_codegen_pr_publication_intent" in BASELINE
    assert "codegen_pr_publication_events_require_intent" in BASELINE
    assert "codegen_pr_publication_events_append_only" in BASELINE


def test_shutdown_awaits_requeued_jobs_before_closing_database():
    main_source = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    await_requeued = "await asyncio.gather(*requeued_jobs, return_exceptions=True)"
    assert main_source.index(await_requeued) < main_source.index("await pool.close()")
