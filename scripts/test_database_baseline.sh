#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/scripts/fixtures/docker-compose.database-baseline.yml"
PROJECT_NAME="apdl-database-baseline-$$"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/apdl-database-baseline.XXXXXX")"

compose() {
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

cleanup() {
    compose down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

assert_equal() {
    local expected="$1"
    local actual="$2"
    local description="$3"
    if [ "$actual" != "$expected" ]; then
        echo "$description: expected '$expected', got '$actual'" >&2
        exit 1
    fi
}

postgres_query() {
    compose exec -T \
        -e PGPASSWORD=apdl_dev \
        postgres \
        psql -X -A -t -v ON_ERROR_STOP=1 -U apdl -d apdl -c "$1"
}

clickhouse_query() {
    compose exec -T clickhouse clickhouse-client \
        --user apdl \
        --password apdl_dev \
        --database apdl \
        --format TSVRaw \
        --query "$1"
}

run_postgres_migrations() {
    COMPOSE_PROJECT_NAME="$PROJECT_NAME" \
    POSTGRES_COMPOSE_FILE="$COMPOSE_FILE" \
    POSTGRES_MIGRATIONS_DIR="${1:-$ROOT_DIR/pipeline/postgres/migrations}" \
        "$ROOT_DIR/scripts/init-postgres.sh"
}

run_clickhouse_migrations() {
    COMPOSE_PROJECT_NAME="$PROJECT_NAME" \
    CLICKHOUSE_COMPOSE_FILE="$COMPOSE_FILE" \
    CLICKHOUSE_MIGRATIONS_DIR="${1:-$ROOT_DIR/pipeline/clickhouse/migrations}" \
    CLICKHOUSE_BACKFILLS_DIR="$ROOT_DIR/pipeline/clickhouse/backfills" \
        "$ROOT_DIR/scripts/init-clickhouse.sh"
}

echo "==> Applying fresh PostgreSQL and ClickHouse baselines"
run_postgres_migrations
run_clickhouse_migrations

assert_equal \
    '1|001_initial_schema.sql' \
    "$(postgres_query "SELECT version || '|' || name FROM apdl_schema_migrations ORDER BY version")" \
    "PostgreSQL baseline ledger did not converge"
assert_equal \
    $'1\t001_initial_schema.sql' \
    "$(clickhouse_query "SELECT version, name FROM apdl_schema_migrations FINAL ORDER BY version")" \
    "ClickHouse baseline ledger did not converge"
assert_equal \
    '0' \
    "$(clickhouse_query "SELECT count() FROM apdl_schema_backfills FINAL")" \
    "Fresh baseline unexpectedly ran a retained-data backfill"

assert_equal \
    '0' \
    "$(postgres_query "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%legacy%'")" \
    "PostgreSQL baseline retained a legacy table"
assert_equal \
    '0' \
    "$(postgres_query "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' AND column_name IN ('legacy_unbound_credential', 'legacy_unbound_setup', 'publication_authorization_legacy', 'publication_authorization_segmentless_legacy', 'publication_authorization_egress_unattested_legacy', 'publication_authorization_pre_tenant_legacy', 'llm_execution_snapshot_v1_legacy')")" \
    "PostgreSQL baseline retained a transitional column"
assert_equal \
    '0' \
    "$(postgres_query "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('llm_project_provider_credentials', 'codegen_project_provider_credentials')")" \
    "PostgreSQL baseline retained a per-service credential store"
assert_equal \
    'false|true' \
    "$(postgres_query "SELECT has_table_privilege('apdl_runtime', 'public.llm_vault_provider_secrets', 'SELECT') || '|' || has_table_privilege('apdl_llm_vault', 'public.llm_vault_provider_secrets', 'SELECT')")" \
    "PostgreSQL baseline did not isolate vault secret custody"
assert_equal \
    '0' \
    "$(clickhouse_query "SELECT count() FROM system.tables WHERE database = 'apdl' AND (name LIKE '%__apdl_migration%' OR name IN ('events_v2', 'events_dlq_v2', 'decisions_v2', 'feeds_v2'))")" \
    "ClickHouse baseline retained an upgrade-only object"

echo "==> Proving idempotent reruns"
run_postgres_migrations
run_clickhouse_migrations

echo "==> Proving checksum drift fails closed"
cp -R "$ROOT_DIR/pipeline/postgres/migrations" "$WORK_DIR/postgres-migrations"
printf '\n-- intentional checksum drift\n' \
    >> "$WORK_DIR/postgres-migrations/001_initial_schema.sql"
if run_postgres_migrations "$WORK_DIR/postgres-migrations" >/dev/null 2>&1; then
    echo "PostgreSQL accepted baseline checksum drift" >&2
    exit 1
fi

cp -R "$ROOT_DIR/pipeline/clickhouse/migrations" "$WORK_DIR/clickhouse-migrations"
printf '\n-- intentional checksum drift\n' \
    >> "$WORK_DIR/clickhouse-migrations/001_initial_schema.sql"
if run_clickhouse_migrations "$WORK_DIR/clickhouse-migrations" >/dev/null 2>&1; then
    echo "ClickHouse accepted baseline checksum drift" >&2
    exit 1
fi

echo "==> Database baseline smoke passed"
