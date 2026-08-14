from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import projects
from app.auth import AdminSession, require_session
from conftest import make_settings


class ProjectConnection:
    def __init__(
        self,
        *,
        exists: bool = False,
        active: bool = True,
        project_count: int = 0,
    ) -> None:
        self.exists = exists
        self.active = active
        self.project_count = project_count
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query: str, *args):
        self.statements.append((query, args))
        assert "FROM admin_users" in query
        assert "FOR UPDATE" in query
        return {"active": self.active}

    async def fetchval(self, query: str, *args):
        self.statements.append((query, args))
        if "SELECT count(*)" in query:
            return self.project_count
        assert "INSERT INTO admin_projects" in query
        if self.exists:
            return None
        self.project_count += 1
        return args[0]

    async def execute(self, query: str, *args):
        self.statements.append((query, args))
        return "OK"


class ProjectPool:
    def __init__(self, connection: ProjectConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def make_client(connection: ProjectConnection, session: AdminSession) -> TestClient:
    app = FastAPI()
    app.state.settings = make_settings()
    app.state.pg_pool = ProjectPool(connection)
    app.include_router(projects.router)
    app.dependency_overrides[require_session] = lambda: session
    return TestClient(app)


def zero_project_session() -> AdminSession:
    return AdminSession(
        session_id="10000000-0000-4000-8000-000000000001",
        token_hash="a" * 64,
        deployment_id="30000000-0000-4000-8000-000000000003",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        user_id="20000000-0000-4000-8000-000000000002",
        email="admin@example.com",
        projects={},
    )


def test_zero_project_user_creates_project_and_receives_owner_roles() -> None:
    connection = ProjectConnection()
    with make_client(connection, zero_project_session()) as client:
        response = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "newproject"},
        )

    assert response.status_code == 201
    identity = response.json()
    assert identity["email"] == "admin@example.com"
    assert identity["projects"] == [
        {
            "project_id": "newproject",
            "roles": sorted(projects.PROJECT_CREATOR_ROLES),
        }
    ]
    membership = next(
        statement
        for statement in connection.statements
        if "INSERT INTO admin_user_projects" in statement[0]
    )
    assert membership[1][1:] == (
        "newproject",
        list(projects.PROJECT_CREATOR_ROLES),
    )


def test_self_registered_project_roles_are_core_only() -> None:
    assert projects.PROJECT_CREATOR_ROLES == (
        "events:write",
        "config:read",
        "config:write",
        "config:evaluate",
        "query:read",
        "agents:read",
        "credentials:manage",
        "members:manage",
    )
    assert not {
        "agents:run",
        "agents:manage",
        "agents:approve",
    }.intersection(projects.PROJECT_CREATOR_ROLES)


def test_project_creation_preserves_existing_profile_projects() -> None:
    session = zero_project_session()
    session = AdminSession(
        **{
            **session.__dict__,
            "projects": {"existing": frozenset({"config:read"})},
        }
    )
    connection = ProjectConnection()
    with make_client(connection, session) as client:
        response = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "second"},
        )

    assert response.status_code == 201
    assert [item["project_id"] for item in response.json()["projects"]] == [
        "existing",
        "second",
    ]


def test_project_creation_rejects_duplicates() -> None:
    session = zero_project_session()
    duplicate_connection = ProjectConnection(exists=True)
    with make_client(duplicate_connection, session) as client:
        duplicate = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "existing"},
        )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Project ID already exists"}
    assert not any(
        "admin_user_projects" in query for query, _ in duplicate_connection.statements
    )


def test_project_creation_enforces_a_serialized_per_user_quota() -> None:
    connection = ProjectConnection(project_count=5)
    with make_client(connection, zero_project_session()) as client:
        response = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "sixth"},
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": "project_quota_reached",
        "message": "This account has reached its project creation limit",
    }
    statements = [query for query, _ in connection.statements]
    assert "FOR UPDATE" in statements[0]
    assert "SELECT count(*)" in statements[1]
    assert not any("INSERT INTO admin_projects" in query for query in statements)
    assert not any("INSERT INTO admin_user_projects" in query for query in statements)


def test_project_creation_revalidates_the_active_user_under_lock() -> None:
    connection = ProjectConnection(active=False)
    with make_client(connection, zero_project_session()) as client:
        response = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "blocked"},
        )

    assert response.status_code == 401
    assert not any("INSERT INTO admin_projects" in query for query, _ in connection.statements)


def test_project_creation_uses_bearer_authority_and_strict_schema() -> None:
    connection = ProjectConnection()
    with make_client(connection, zero_project_session()) as client:
        invalid_id = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "not-valid"},
        )
        unknown_field = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "valid", "owner": "caller"},
        )
        bearer_only = client.post(
            "/api/projects",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "valid"},
        )

    assert invalid_id.status_code == 422
    assert unknown_field.status_code == 422
    assert bearer_only.status_code == 201
    assert any("INSERT INTO admin_projects" in query for query, _ in connection.statements)
