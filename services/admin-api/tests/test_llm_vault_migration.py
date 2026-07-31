"""Static schema contract for Admin API LLM Vault mutation auditing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    ROOT / "pipeline" / "postgres" / "migrations" / "001_initial_schema.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")


def test_llm_vault_is_an_allowed_admin_proxy_audit_service() -> None:
    constraint = next(
        line
        for line in SQL.splitlines()
        if "CONSTRAINT admin_proxy_audit_service_check" in line
    )

    assert "'llm-vault'::text" in constraint
