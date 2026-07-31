"""Contracts for the canonical fresh-install PostgreSQL baseline."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
POSTGRES_MIGRATIONS = ROOT / "pipeline" / "postgres" / "migrations"
CLICKHOUSE_MIGRATIONS = ROOT / "pipeline" / "clickhouse" / "migrations"
BASELINE = (POSTGRES_MIGRATIONS / "001_initial_schema.sql").read_text()
POSTGRES_RUNNER = (ROOT / "pipeline" / "postgres" / "migrate.py").read_text()
CLICKHOUSE_RUNNER = (ROOT / "pipeline" / "clickhouse" / "migrate.py").read_text()


def _table_definition(table: str) -> str:
    start = BASELINE.index(f"CREATE TABLE public.{table} (")
    return BASELINE[start : BASELINE.index("\n);", start) + 3]


def test_each_database_has_one_canonical_initial_schema() -> None:
    for directory in (POSTGRES_MIGRATIONS, CLICKHOUSE_MIGRATIONS):
        names = sorted(path.name for path in directory.glob("*.sql"))
        assert names == ["001_initial_schema.sql"]
        assert all(re.fullmatch(r"[0-9]{3}_[a-z0-9_]+\.sql", name) for name in names)


def test_baseline_contains_only_current_schema_objects() -> None:
    lowered = BASELINE.lower()
    for retired in (
        "codegen_connections_legacy_unverified",
        "llm_calls_legacy_pre_governance_023",
        "publication_authorization_legacy",
        "publication_authorization_segmentless_legacy",
        "publication_authorization_egress_unattested_legacy",
        "publication_authorization_pre_tenant_legacy",
        "llm_execution_snapshot_v1_legacy",
        "legacy_unbound_credential",
        "legacy_unbound_setup",
        "llm_project_provider_credentials",
        "codegen_project_provider_credentials",
    ):
        assert retired not in lowered

    assert " rename to " not in lowered
    assert " drop column " not in lowered
    assert " add column " not in lowered


def test_core_service_tables_use_the_canonical_shapes() -> None:
    credentials = _table_definition("auth_credentials")
    memory = _table_definition("agent_memory")
    flags = _table_definition("flags")
    experiments = _table_definition("experiments")

    assert "credential_kind text NOT NULL" in credentials
    assert "key_hash character(64) NOT NULL" in credentials
    assert "embedding public.vector(384)" in memory
    assert "default_variant text DEFAULT 'control'::text NOT NULL" in flags
    assert "variants jsonb" in flags
    assert "bucket_by text NOT NULL" in experiments
    assert "statistical_plan jsonb" in experiments
    assert "minimum_exposure_config_version integer" in experiments


def test_llm_attempts_are_always_bound_to_current_project_setup() -> None:
    attempts = _table_definition("llm_provider_attempts")

    for column in (
        "setup_version bigint NOT NULL",
        "model_tier text NOT NULL",
        "connection_version bigint NOT NULL",
        "inventory_version bigint NOT NULL",
        "model_catalog_version text NOT NULL",
    ):
        assert column in attempts
    assert "llm_provider_attempts_credential_binding_check" in attempts
    assert "llm_provider_attempts_setup_binding_check" in attempts
    assert "legacy_unbound" not in attempts
    assert "llm_provider_attempts_protect_credential_binding BEFORE UPDATE" in BASELINE
    assert "llm_provider_attempts_protect_setup_binding BEFORE UPDATE" in BASELINE


def test_governance_and_execution_boundaries_are_installed() -> None:
    for function in (
        "apdl_assert_execution_project_authorized",
        "apdl_assert_agents_project_active",
        "apdl_enforce_experiment_archive_lifecycle",
        "apdl_enforce_experiment_enrollment_immutability",
        "apdl_validate_active_agents_setup",
        "apdl_purge_experiment_audit",
    ):
        assert f"CREATE FUNCTION public.{function}" in BASELINE

    assert "INSERT INTO public.apdl_execution_table_registry" in BASELINE
    assert "INSERT INTO public.apdl_analysis_table_registry" in BASELINE
    assert "SELECT public.apdl_assert_execution_table_registry();" in BASELINE
    assert "SELECT public.apdl_assert_analysis_table_registry();" in BASELINE


def test_runtime_and_operator_privileges_are_explicit() -> None:
    assert "GRANT CONNECT ON DATABASE %I TO apdl_runtime" in BASELINE
    assert "GRANT CONNECT ON DATABASE %I TO apdl_llm_vault" in BASELINE
    assert "ALTER DEFAULT PRIVILEGES IN SCHEMA public" in BASELINE
    assert ") OWNER TO apdl_audit_purge_definer;" in BASELINE
    assert ") TO apdl_audit_operator;" in BASELINE
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in BASELINE
    assert "public.llm_vault_provider_secrets" in BASELINE
    assert "FROM PUBLIC, apdl_runtime" in BASELINE
    assert "TO apdl_llm_vault" in BASELINE


def test_migration_runners_retain_immutable_exact_prefix_ledgers() -> None:
    assert "apdl_schema_migrations" in POSTGRES_RUNNER
    assert "hashlib.sha256" in POSTGRES_RUNNER
    assert "pg_advisory_xact_lock" in POSTGRES_RUNNER
    assert "apdl_reject_migration_ledger_mutation" in POSTGRES_RUNNER
    assert "apdl_schema_migrations" in CLICKHOUSE_RUNNER
    assert "checksum drift" in CLICKHOUSE_RUNNER
    assert "Misplaced PostgreSQL migration" in CLICKHOUSE_RUNNER
