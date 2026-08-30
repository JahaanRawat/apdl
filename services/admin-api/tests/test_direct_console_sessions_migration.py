from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    ROOT
    / "pipeline"
    / "postgres"
    / "migrations"
    / "060_direct_console_bearer_sessions.sql"
)


def test_bearer_session_migration_is_an_explicit_cookie_session_cutover() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "TRUNCATE TABLE admin_sessions" in sql
    assert "DROP COLUMN csrf_hash" in sql
    assert "DROP COLUMN last_seen_at" in sql
    assert "ADD COLUMN deployment_id UUID NOT NULL" in sql
    assert "(deployment_id, token_hash, expires_at)" in sql
