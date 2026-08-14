from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.security import hash_password
from conftest import make_settings

DEPLOYMENT_ID = uuid.UUID("30000000-0000-4000-8000-000000000003")
USER_ID = uuid.UUID("20000000-0000-4000-8000-000000000002")
SESSION_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PASSWORD = "a-correct-horse-battery-staple"


class LoginConnection:
    def __init__(self) -> None:
        self.user_id = USER_ID
        self.password_hash = hash_password(PASSWORD)
        self.active = True
        self.rate_buckets: dict[tuple[str, str], tuple[datetime, int]] = {}
        self.source_risks: dict[tuple[str, str, str], tuple[int, datetime]] = {}
        self.account_failures = 0
        self.account_window_started_at: datetime | None = None
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    @asynccontextmanager
    async def transaction(self):
        snapshot = deepcopy(
            (
                self.rate_buckets,
                self.source_risks,
                self.account_failures,
                self.account_window_started_at,
            )
        )
        try:
            yield
        except Exception:
            (
                self.rate_buckets,
                self.source_risks,
                self.account_failures,
                self.account_window_started_at,
            ) = snapshot
            raise

    async def fetchrow(self, query: str, *args):
        if "INSERT INTO admin_login_rate_buckets" in query:
            scope, key_hash, window_seconds, now = args
            key = (str(scope), str(key_hash))
            previous = self.rate_buckets.get(key)
            if (
                previous is None
                or previous[0] <= now - timedelta(seconds=int(window_seconds))
            ):
                value = (now, 1)
            else:
                value = (previous[0], previous[1] + 1)
            self.rate_buckets[key] = value
            return {
                "window_started_at": value[0],
                "attempt_count": value[1],
            }
        if "INSERT INTO admin_login_account_risk" in query:
            _, _, now, window_seconds = args
            if (
                self.account_window_started_at is None
                or self.account_window_started_at
                <= now - timedelta(seconds=int(window_seconds))
            ):
                self.account_window_started_at = now
                self.account_failures = 1
            else:
                self.account_failures += 1
            return {
                "window_started_at": self.account_window_started_at,
                "failure_count": self.account_failures,
            }
        if "FROM admin_users" in query:
            if args[0] != "admin@example.com":
                return None
            return {
                "user_id": self.user_id,
                "email": "admin@example.com",
                "password_hash": self.password_hash,
                "active": self.active,
            }
        raise AssertionError(f"Unexpected fetchrow: {query}")

    async def fetchval(self, query: str, *args):
        if "SELECT next_allowed_at" in query:
            risk = self.source_risks.get(
                (str(args[0]), str(args[1]), str(args[2]))
            )
            return risk[1] if risk is not None else None
        if "INSERT INTO admin_login_source_risk" in query:
            key = (str(args[0]), str(args[1]), str(args[2]))
            now = args[3]
            previous = self.source_risks.get(key)
            failures = 1 if previous is None else previous[0] + 1
            next_allowed_at = now if previous is None else previous[1]
            self.source_risks[key] = (failures, next_allowed_at)
            return failures
        raise AssertionError(f"Unexpected fetchval: {query}")

    async def fetch(self, query: str, *args):
        assert "FROM admin_user_projects" in query
        assert args == (self.user_id,)
        return [{"project_id": "demo", "roles": ["config:read", "config:write"]}]

    async def execute(self, query: str, *args):
        self.executions.append((query, args))
        if (
            "DELETE FROM admin_login_source_risk" in query
            and "WHERE email_hash = $1" in query
        ):
            email_hash, network_hash = (str(arg) for arg in args)
            self.source_risks.pop(("network", network_hash, email_hash), None)
        elif "UPDATE admin_login_source_risk" in query:
            key = (str(args[0]), str(args[1]), str(args[2]))
            failures, next_allowed_at = self.source_risks[key]
            proposed = args[3] + timedelta(seconds=int(args[4]))
            self.source_risks[key] = (failures, max(next_allowed_at, proposed))
        return "OK"


class SessionConnection:
    def __init__(self, raw_token: str, *, deployment_matches: bool = True) -> None:
        self.raw_token = raw_token
        self.deployment_matches = deployment_matches
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc)

    async def fetchrow(self, query: str, *args):
        assert "FROM admin_sessions" in query
        assert "last_seen_at" not in query
        assert "s.deployment_id = $2" in query
        digest = hashlib.sha256(self.raw_token.encode()).hexdigest()
        assert args == (digest, DEPLOYMENT_ID)
        if not self.deployment_matches:
            return None
        return {
            "session_id": SESSION_ID,
            "token_hash": digest,
            "deployment_id": DEPLOYMENT_ID,
            "expires_at": self.expires_at,
            "user_id": USER_ID,
            "email": "admin@example.com",
        }

    async def fetch(self, query: str, *args):
        assert "FROM admin_user_projects" in query
        assert args == (USER_ID,)
        return [{"project_id": "demo", "roles": ["config:read"]}]

    async def execute(self, query: str, *args):
        self.executions.append((query, args))
        return "UPDATE 1"


class FakePool:
    def __init__(self, connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def make_client(connection, *, settings=None) -> TestClient:
    app = FastAPI()
    app.state.settings = settings or make_settings(deployment_id=DEPLOYMENT_ID)
    app.state.pg_pool = FakePool(connection)
    app.include_router(auth.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_login_returns_fixed_bearer_and_sets_no_cookie() -> None:
    connection = LoginConnection()
    with make_client(connection) as client:
        response = client.post(
            "/api/console/v1/sessions",
            headers={"Origin": "http://admin.test"},
            json={"email": "admin@example.com", "password": PASSWORD},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "console_session@1"
    assert len(payload["access_token"]) == 43
    expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    assert timedelta(hours=7, minutes=59) < expires_at - datetime.now(timezone.utc)
    assert expires_at - datetime.now(timezone.utc) <= timedelta(hours=8)
    assert response.headers.get_list("set-cookie") == []

    insert = next(
        (args for query, args in connection.executions if "INSERT INTO admin_sessions" in query),
    )
    assert insert[1] == USER_ID
    assert insert[2] == hashlib.sha256(payload["access_token"].encode()).hexdigest()
    assert insert[3] == DEPLOYMENT_ID
    assert insert[4] == expires_at
    assert PASSWORD not in repr(connection.executions)


def test_login_rejects_unknown_fields_and_has_no_legacy_aliases() -> None:
    connection = LoginConnection()
    with make_client(connection) as client:
        unknown = client.post(
            "/api/console/v1/sessions",
            headers={"Origin": "http://admin.test"},
            json={
                "email": "admin@example.com",
                "password": PASSWORD,
                "remember_me": True,
            },
        )
        legacy_login = client.post("/api/auth/login")
        legacy_me = client.get("/api/auth/me")
        legacy_logout = client.post("/api/auth/logout")
        legacy_register = client.post("/api/auth/register")

    assert unknown.status_code == 422
    assert {legacy_login.status_code, legacy_me.status_code, legacy_logout.status_code} == {404}
    assert legacy_register.status_code == 404


def test_invalid_credentials_remain_generic_and_create_no_session() -> None:
    connection = LoginConnection()
    with make_client(connection) as client:
        response = client.post(
            "/api/console/v1/sessions",
            headers={"Origin": "http://admin.test"},
            json={"email": "admin@example.com", "password": "incorrect-password"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers.get_list("set-cookie") == []
    assert not any("INSERT INTO admin_sessions" in query for query, _ in connection.executions)


def test_identity_accepts_only_the_canonical_authorization_header() -> None:
    token = "a" * 43
    connection = SessionConnection(token)
    with make_client(connection) as client:
        cookie_only = client.get(
            "/api/console/v1/session",
            cookies={"apdl_admin_session": token},
        )
        query_only = client.get(f"/api/console/v1/session?access_token={token}")
        alternate_only = client.get(
            "/api/console/v1/session",
            headers={"X-Access-Token": token},
        )
        malformed = client.get(
            "/api/console/v1/session",
            headers={"Authorization": f"bearer {token}"},
        )
        valid = client.get(
            "/api/console/v1/session",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert cookie_only.status_code == 401
    assert query_only.status_code == 401
    assert alternate_only.status_code == 401
    assert malformed.status_code == 401
    assert valid.status_code == 200
    assert valid.json() == {
        "schema_version": "console_identity@1",
        "user_id": str(USER_ID),
        "email": "admin@example.com",
        "projects": [{"project_id": "demo", "roles": ["config:read"]}],
    }
    assert connection.executions == []


def test_session_is_bound_to_the_configured_deployment() -> None:
    token = "b" * 43
    connection = SessionConnection(token, deployment_matches=False)
    with make_client(connection) as client:
        response = client.get(
            "/api/console/v1/session",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


def test_logout_revokes_the_token_without_setting_cookies() -> None:
    token = "c" * 43
    connection = SessionConnection(token)
    with make_client(connection) as client:
        response = client.delete(
            "/api/console/v1/session",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://admin.test",
            },
        )

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers.get_list("set-cookie") == []
    assert connection.executions == [
        (
            "UPDATE admin_sessions SET revoked_at = NOW() WHERE session_id = $1",
            (SESSION_ID,),
        )
    ]
