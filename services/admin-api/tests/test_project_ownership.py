from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import projects
from app.auth import AdminSession, require_session
from conftest import make_settings

OWNER_ID = UUID("20000000-0000-4000-8000-000000000002")
TARGET_ID = UUID("30000000-0000-4000-8000-000000000003")
CREATOR_ID = UUID("40000000-0000-4000-8000-000000000004")
NEWEST_AUDIT_ID = UUID("70000000-0000-4000-8000-000000000007")
OLDER_AUDIT_ID = UUID("60000000-0000-4000-8000-000000000006")


class OwnershipConnection:
    def __init__(
        self,
        *,
        actor_id: UUID = OWNER_ID,
        owner_id: UUID | None = OWNER_ID,
        target_roles: list[str] | None = None,
        audit_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.actor_id = actor_id
        self.owner_id = owner_id
        self.target_roles = target_roles or ["config:read", "members:manage"]
        self.audit_rows = (
            audit_rows
            if audit_rows is not None
            else [
                {
                    "audit_id": OLDER_AUDIT_ID,
                    "project_id": "demo",
                    "previous_owner_user_id": OWNER_ID,
                    "previous_owner_email": "owner@example.com",
                    "new_owner_user_id": TARGET_ID,
                    "new_owner_email": "target@example.com",
                    "actor": "owner@example.com",
                    "reason": "Planned team handoff",
                    "created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
                }
            ]
        )
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.audits: list[tuple[object, ...]] = []
        self.membership_audits: list[tuple[object, ...]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query: str, *args):
        self.statements.append((query, args))
        if "execution.authorization_source" in query:
            return {
                "project_id": "demo",
                "created_by": CREATOR_ID,
                "owner_user_id": self.owner_id,
                "creator_email": "creator@example.com",
                "owner_email": (
                    "target@example.com"
                    if self.owner_id == TARGET_ID
                    else "owner@example.com"
                ),
                "execution_authorization_source": "self_registered_override",
            }
        if "SELECT project.owner_user_id" in query:
            return {"owner_user_id": self.owner_id}
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args):
        self.statements.append((query, args))
        if "FROM admin_project_ownership_audit" in query:
            before_created_at, before_audit_id, limit = args[1:]
            rows = self.audit_rows
            if before_created_at is not None:
                rows = [
                    row
                    for row in rows
                    if (row["created_at"], row["audit_id"])
                    < (before_created_at, before_audit_id)
                ]
            return rows[:limit]
        assert "FOR UPDATE OF membership, account" in query
        return [
            {
                "user_id": self.actor_id,
                "roles": ["members:manage"],
                "active": True,
            },
            {
                "user_id": TARGET_ID,
                "roles": self.target_roles,
                "email": "target@example.com",
                "active": True,
            },
        ]

    async def fetchval(self, query: str, *args):
        self.statements.append((query, args))
        assert "'members:manage' = ANY(membership.roles)" in query
        return self.actor_id

    async def execute(self, query: str, *args):
        self.statements.append((query, args))
        if "UPDATE admin_projects" in query:
            if self.owner_id != args[1]:
                return "UPDATE 0"
            self.owner_id = args[2]
            return "UPDATE 1"
        if "INSERT INTO admin_project_ownership_audit" in query:
            self.audits.append(args)
        if "INSERT INTO admin_project_membership_audit" in query:
            self.membership_audits.append(args)
        return "OK"


class OwnershipPool:
    def __init__(self, connection: OwnershipConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _session(*, user_id: UUID = OWNER_ID) -> AdminSession:
    return AdminSession(
        session_id="10000000-0000-4000-8000-000000000001",
        token_hash="a" * 64,
        deployment_id="30000000-0000-4000-8000-000000000003",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        user_id=str(user_id),
        email="owner@example.com",
        projects={"demo": frozenset({"config:read", "members:manage"})},
    )


def _client(connection: OwnershipConnection, session: AdminSession) -> TestClient:
    app = FastAPI()
    app.state.settings = make_settings()
    app.state.pg_pool = OwnershipPool(connection)
    app.include_router(projects.router)
    app.dependency_overrides[require_session] = lambda: session
    return TestClient(app)


def test_project_member_can_read_ownership_and_execution_authorization() -> None:
    connection = OwnershipConnection()
    with _client(connection, _session()) as client:
        response = client.get("/api/projects/demo/authorization")

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "demo",
        "creator": {
            "user_id": str(CREATOR_ID),
            "email": "creator@example.com",
        },
        "ownership": {
            "kind": "human",
            "owner_user_id": str(OWNER_ID),
            "owner_email": "owner@example.com",
        },
        "execution_authorization": {
            "authorized": True,
            "source": "self_registered_override",
        },
    }


def test_owner_transfer_grants_every_role_and_writes_immutable_audits() -> None:
    connection = OwnershipConnection()
    with _client(connection, _session()) as client:
        response = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={
                "target_user_id": str(TARGET_ID),
                "reason": "  Planned team handoff  ",
            },
        )

    assert response.status_code == 200
    assert response.json()["ownership"] == {
        "kind": "human",
        "owner_user_id": str(TARGET_ID),
        "owner_email": "target@example.com",
    }
    assert len(connection.audits) == 1
    assert connection.audits[0][2:6] == (
        OWNER_ID,
        TARGET_ID,
        "owner@example.com",
        "Planned team handoff",
    )
    assert len(connection.membership_audits) == 1
    assert connection.membership_audits[0][2:] == (
        OWNER_ID,
        TARGET_ID,
        "target@example.com",
        ["config:read", "members:manage"],
        list(projects.PROJECT_OWNER_ROLES),
    )
    sql = [" ".join(query.split()) for query, _ in connection.statements]
    assert any("FOR UPDATE OF project" in query for query in sql)
    assert any("FOR UPDATE OF membership, account" in query for query in sql)
    assert any("UPDATE admin_user_projects SET roles" in query for query in sql)
    assert not any(
        "UPDATE admin_project_execution_authorizations" in query for query in sql
    )


def test_operator_managed_project_cannot_be_claimed_from_human_api() -> None:
    connection = OwnershipConnection(owner_id=None)
    with _client(connection, _session()) as client:
        response = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID)},
        )

    assert response.status_code == 409
    assert "cannot be claimed" in response.json()["detail"]
    assert connection.audits == []


def test_manager_can_read_immutable_ownership_history() -> None:
    connection = OwnershipConnection()
    with _client(connection, _session()) as client:
        response = client.get("/api/projects/demo/ownership/audit")

    assert response.status_code == 200
    assert response.json() == {
        "entries": [
            {
                "audit_id": str(OLDER_AUDIT_ID),
                "project_id": "demo",
                "previous_owner_user_id": str(OWNER_ID),
                "previous_owner_email": "owner@example.com",
                "new_owner_user_id": str(TARGET_ID),
                "new_owner_email": "target@example.com",
                "actor": "owner@example.com",
                "reason": "Planned team handoff",
                "created_at": "2026-07-30T00:00:00Z",
            }
        ],
        "next_cursor": None,
    }


def test_ownership_audit_uses_keyset_pagination() -> None:
    newest_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    older_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    rows = [
        {
            "audit_id": NEWEST_AUDIT_ID,
            "project_id": "demo",
            "previous_owner_user_id": OWNER_ID,
            "previous_owner_email": "owner@example.com",
            "new_owner_user_id": TARGET_ID,
            "new_owner_email": "target@example.com",
            "actor": "owner@example.com",
            "reason": "First page",
            "created_at": newest_at,
        },
        {
            "audit_id": OLDER_AUDIT_ID,
            "project_id": "demo",
            "previous_owner_user_id": TARGET_ID,
            "previous_owner_email": "target@example.com",
            "new_owner_user_id": OWNER_ID,
            "new_owner_email": "owner@example.com",
            "actor": "target@example.com",
            "reason": "Older page",
            "created_at": older_at,
        },
    ]
    connection = OwnershipConnection(audit_rows=rows)
    with _client(connection, _session()) as client:
        first = client.get("/api/projects/demo/ownership/audit?limit=1")
        cursor = first.json()["next_cursor"]
        second = client.get(
            "/api/projects/demo/ownership/audit",
            params={
                "limit": 1,
                "before_created_at": cursor["created_at"],
                "before_audit_id": cursor["audit_id"],
            },
        )

    assert [entry["reason"] for entry in first.json()["entries"]] == [
        "First page"
    ]
    assert cursor == {
        "created_at": "2026-08-01T00:00:00Z",
        "audit_id": str(NEWEST_AUDIT_ID),
    }
    assert [entry["reason"] for entry in second.json()["entries"]] == [
        "Older page"
    ]
    assert second.json()["next_cursor"] is None
    audit_queries = [
        (query, args)
        for query, args in connection.statements
        if "FROM admin_project_ownership_audit" in query
    ]
    assert all("(audit.created_at, audit.audit_id)" in query for query, _ in audit_queries)
    assert audit_queries[0][1] == ("demo", None, None, 2)
    assert audit_queries[1][1] == (
        "demo",
        newest_at,
        NEWEST_AUDIT_ID,
        2,
    )


def test_transfer_without_reason_records_explicit_marker() -> None:
    connection = OwnershipConnection()
    with _client(connection, _session()) as client:
        response = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID)},
        )

    assert response.status_code == 200
    assert connection.audits[0][4:] == (
        "owner@example.com",
        "No reason provided",
    )


def test_non_owner_and_ineligible_target_cannot_transfer() -> None:
    non_owner = UUID("50000000-0000-4000-8000-000000000005")
    connection = OwnershipConnection(actor_id=non_owner)
    with _client(connection, _session(user_id=non_owner)) as client:
        response = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID)},
        )
    assert response.status_code == 403

    connection = OwnershipConnection(target_roles=["config:read"])
    with _client(connection, _session()) as client:
        response = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID)},
        )
    assert response.status_code == 409
    assert connection.audits == []


def test_transfer_uses_bearer_authority_and_strict_schema() -> None:
    connection = OwnershipConnection()
    with _client(connection, _session()) as client:
        unknown = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID), "force": True},
        )
        bearer_only = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={"target_user_id": str(TARGET_ID)},
        )
        multiline_reason = client.post(
            "/api/projects/demo/ownership/transfer",
            headers={"Origin": "http://admin.test"},
            json={
                "target_user_id": str(TARGET_ID),
                "reason": "invalid\nreason",
            },
        )

    assert unknown.status_code == 422
    assert bearer_only.status_code == 200
    assert multiline_reason.status_code == 422
