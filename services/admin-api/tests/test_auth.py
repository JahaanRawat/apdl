from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.error_boundary import install_error_boundary
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


class RegistrationConnection:
    def __init__(
        self,
        *,
        account_exists: bool = False,
        account_count: int = 0,
        locked_account_count: int | None = None,
    ) -> None:
        self.user_id = uuid.UUID("40000000-0000-4000-8000-000000000004")
        self.account_exists = account_exists
        self.account_count = account_count
        self.locked_account_count = locked_account_count
        self.count_reads = 0
        self.rate_buckets: dict[tuple[str, str], tuple[datetime, int]] = {}
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.insert_attempts = 0
        self.pool_depth = 0

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query: str, *args):
        assert "INSERT INTO admin_login_rate_buckets" in query
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
        return {"window_started_at": value[0], "attempt_count": value[1]}

    async def fetchval(self, query: str, *args):
        self.executions.append((query, args))
        if "SELECT count(*) FROM admin_users" in query:
            self.count_reads += 1
            if self.count_reads >= 2 and self.locked_account_count is not None:
                return self.locked_account_count
            return self.account_count
        assert "INSERT INTO admin_users" in query
        self.insert_attempts += 1
        assert args[1] == "new-admin@example.com"
        assert str(args[2]).startswith("$argon2id$")
        if self.account_exists:
            return None
        self.account_count += 1
        return self.user_id

    async def execute(self, query: str, *args):
        self.executions.append((query, args))
        return "OK"


class RegistrationPool(FakePool):
    @asynccontextmanager
    async def acquire(self):
        self.connection.pool_depth += 1
        try:
            yield self.connection
        finally:
            self.connection.pool_depth -= 1


def make_registration_client(connection, *, settings=None) -> TestClient:
    app = FastAPI()
    app.state.settings = settings or make_settings(deployment_id=DEPLOYMENT_ID)
    app.state.pg_pool = RegistrationPool(connection)
    app.include_router(auth.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_registration_creates_zero_project_user_and_bearer_session() -> None:
    connection = RegistrationConnection()
    with make_registration_client(connection) as client:
        response = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "NEW-ADMIN@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["schema_version"] == "console_session@1"
    assert len(payload["access_token"]) == 43
    assert response.headers.get_list("set-cookie") == []
    assert connection.insert_attempts == 1
    assert not any(
        "admin_user_projects" in query for query, _ in connection.executions
    )
    session_insert = next(
        args
        for query, args in connection.executions
        if "INSERT INTO admin_sessions" in query
    )
    assert session_insert[1] == connection.user_id
    assert session_insert[2] == hashlib.sha256(
        payload["access_token"].encode()
    ).hexdigest()
    assert session_insert[3] == DEPLOYMENT_ID


def test_registration_is_disabled_before_database_or_hashing(monkeypatch) -> None:
    connection = RegistrationConnection()

    async def unexpected_hash(*_args):
        raise AssertionError("disabled registration must not hash")

    monkeypatch.setattr(auth.asyncio, "to_thread", unexpected_hash)
    settings = make_settings(
        deployment_id=DEPLOYMENT_ID,
        registration_enabled=False,
    )
    with make_registration_client(connection, settings=settings) as client:
        response = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert response.status_code == 403
    assert connection.executions == []


def test_registration_rejects_existing_email_without_starting_session() -> None:
    connection = RegistrationConnection(account_exists=True)
    with make_registration_client(connection) as client:
        response = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert response.status_code == 409
    assert not any(
        "INSERT INTO admin_sessions" in query for query, _ in connection.executions
    )


def test_registration_conflict_uses_canonical_public_error_without_enumeration() -> None:
    connection = RegistrationConnection(account_exists=True)
    app = FastAPI()
    app.state.settings = make_settings(deployment_id=DEPLOYMENT_ID)
    app.state.pg_pool = RegistrationPool(connection)
    install_error_boundary(app)
    app.include_router(auth.router)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "schema_version": "error@1",
        "code": "conflict",
        "message": "The request conflicts with the current resource state.",
        "request_id": response.headers["x-request-id"],
    }
    assert "account" not in response.text.lower()


def test_registration_hashes_without_holding_a_database_connection(monkeypatch) -> None:
    connection = RegistrationConnection()
    observed_depths: list[int] = []

    async def checked_to_thread(function, *args):
        observed_depths.append(connection.pool_depth)
        return function(*args)

    monkeypatch.setattr(auth.asyncio, "to_thread", checked_to_thread)
    with make_registration_client(connection) as client:
        response = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert response.status_code == 201
    assert observed_depths == [0]
    statements = [query for query, _ in connection.executions]
    lock_index = next(
        index
        for index, query in enumerate(statements)
        if "pg_advisory_xact_lock" in query
    )
    count_index = next(
        index
        for index, query in enumerate(statements[lock_index + 1 :], lock_index + 1)
        if "SELECT count(*) FROM admin_users" in query
    )
    insert_index = next(
        index
        for index, query in enumerate(statements)
        if "INSERT INTO admin_users" in query
    )
    assert lock_index < count_index < insert_index


def test_registration_enforces_early_and_locked_account_capacity(monkeypatch) -> None:
    async def unexpected_hash(*_args):
        raise AssertionError("early capacity rejection must not hash")

    at_capacity = RegistrationConnection(account_count=1)
    monkeypatch.setattr(auth.asyncio, "to_thread", unexpected_hash)
    settings = make_settings(deployment_id=DEPLOYMENT_ID, max_accounts=1)
    with make_registration_client(at_capacity, settings=settings) as client:
        early = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert early.status_code == 409
    assert at_capacity.insert_attempts == 0

    raced = RegistrationConnection(account_count=0, locked_account_count=1)

    async def hash_after_preflight(function, *args):
        return function(*args)

    monkeypatch.setattr(auth.asyncio, "to_thread", hash_after_preflight)
    with make_registration_client(raced, settings=settings) as client:
        locked = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert locked.status_code == 409
    assert raced.insert_attempts == 0
    assert not any(
        "INSERT INTO admin_sessions" in query for query, _ in raced.executions
    )


def test_registration_uses_shared_auth_rate_limit() -> None:
    connection = RegistrationConnection()
    settings = make_settings(
        deployment_id=DEPLOYMENT_ID,
        login_global_rate_limit=1,
    )
    with make_registration_client(connection, settings=settings) as client:
        first = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )
        second = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert connection.insert_attempts == 1


def test_registration_schema_is_strict_and_has_no_legacy_alias() -> None:
    connection = RegistrationConnection()
    with make_registration_client(connection) as client:
        short_password = client.post(
            "/api/console/v1/registrations",
            json={"email": "new-admin@example.com", "password": "too-short"},
        )
        unknown = client.post(
            "/api/console/v1/registrations",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
                "project_id": "demo",
            },
        )
        legacy = client.post(
            "/api/auth/register",
            json={
                "email": "new-admin@example.com",
                "password": "a-new-correct-horse-password",
            },
        )

    assert short_password.status_code == 422
    assert unknown.status_code == 422
    assert legacy.status_code == 404
    assert connection.executions == []


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
