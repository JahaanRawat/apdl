from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "pipeline" / "postgres" / "migrations"
SQL_028 = (MIGRATIONS / "028_admin_execution_authority.sql").read_text(
    encoding="utf-8"
)
SQL_051 = (MIGRATIONS / "051_agents_project_setup.sql").read_text(
    encoding="utf-8"
)
SQL_062 = (MIGRATIONS / "062_project_owner_permissions.sql").read_text(
    encoding="utf-8"
)

ALL_HUMAN_ROLES = (
    "events:write",
    "config:read",
    "config:write",
    "config:evaluate",
    "query:read",
    "agents:read",
    "agents:run",
    "agents:manage",
    "agents:approve",
    "credentials:manage",
    "members:manage",
)


def _function(sql: str, name: str) -> str:
    start = sql.index(f"CREATE OR REPLACE FUNCTION {name}")
    delimiter = f"${name}$;"
    return sql[start : sql.index(delimiter, start) + len(delimiter)]


def test_owner_permission_set_is_the_literal_canonical_human_role_order() -> None:
    function = _function(SQL_062, "apdl_all_admin_roles")
    array = re.search(r"ARRAY\[(?P<roles>.*?)\]::TEXT\[\]", function, re.DOTALL)

    assert array is not None
    assert tuple(re.findall(r"'([^']+)'", array.group("roles"))) == ALL_HUMAN_ROLES
    assert "public.apdl_canonical_admin_roles" in function


def test_existing_and_future_owners_receive_every_permission() -> None:
    assert "UPDATE admin_user_projects AS membership" in SQL_062
    assert "project.owner_user_id = membership.user_id" in SQL_062
    assert "SET roles = public.apdl_all_admin_roles()" in SQL_062

    sync = _function(SQL_062, "apdl_sync_project_owner_permissions")
    assert "SET roles = public.apdl_all_admin_roles()" in sync
    assert "user_id = NEW.owner_user_id" in sync
    assert "AFTER INSERT OR UPDATE OF owner_user_id, project_id" in SQL_062

    invariant = _function(SQL_062, "apdl_require_full_project_owner_permissions")
    assert "membership.roles = public.apdl_all_admin_roles()" in invariant
    assert "CREATE CONSTRAINT TRIGGER admin_projects_require_full_owner_permissions" in SQL_062
    assert "DEFERRABLE INITIALLY DEFERRED" in SQL_062


def test_owner_membership_cannot_be_deleted_moved_or_downscoped() -> None:
    protection = _function(SQL_062, "apdl_protect_project_owner_membership")

    assert "TG_OP = 'DELETE'" in protection
    assert "NEW.project_id IS DISTINCT FROM OLD.project_id" in protection
    assert "NEW.user_id IS DISTINCT FROM OLD.user_id" in protection
    assert "NEW.roles IS DISTINCT FROM public.apdl_all_admin_roles()" in protection
    assert "ERRCODE = '23514'" in protection


def test_human_approval_role_is_separate_from_credential_execution_authority() -> None:
    assert (
        "DROP TRIGGER IF EXISTS admin_user_projects_execution_authority\n"
        "    ON admin_user_projects;"
    ) in SQL_062
    assert "DROP TRIGGER IF EXISTS auth_credentials_execution_authority" not in SQL_062
    assert "CREATE OR REPLACE FUNCTION apdl_enforce_execution_roles" not in SQL_062

    assert "CREATE TRIGGER auth_credentials_execution_authority" in SQL_028
    enforcement = _function(SQL_051, "apdl_enforce_execution_roles")
    assert "'agents:approve' = ANY(NEW.roles)" in enforcement
    assert "PERFORM apdl_assert_execution_project_authorized" in enforcement


def test_owner_permissions_do_not_modify_effect_table_fences() -> None:
    for effect_fence in (
        "apdl_execution_table_registry",
        "apdl_analysis_table_registry",
        "apdl_enforce_execution_table_project",
        "apdl_enforce_analysis_table_project",
        "agent_approval_effects",
    ):
        assert effect_fence not in SQL_062
