from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from app import proxy
from app.auth import AdminSession
from conftest import TEST_API_KEY, make_settings, proxy_client


class StreamAuthorityConnection:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args):
        self.calls.append((query, args))
        if not self.results:
            raise AssertionError("Unexpected stream authority revalidation")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class StreamAuthorityPool:
    def __init__(self, results: list[object]) -> None:
        self.connection = StreamAuthorityConnection(results)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class StubStreamingResponse:
    def __init__(
        self,
        *,
        busy: bool = False,
        chunks: tuple[bytes, ...] = (),
        hold_open: bool = True,
    ) -> None:
        self.busy = busy
        self.chunks = chunks
        self.hold_open = hold_open
        self.closed = False
        self.iterator_closed = False
        self.release = asyncio.Event()

    async def aiter_raw(self):
        try:
            for chunk in self.chunks:
                await asyncio.sleep(0)
                yield chunk
            if self.busy:
                while True:
                    await asyncio.sleep(0)
                    yield b": heartbeat\n\n"
            if self.hold_open:
                await self.release.wait()
        finally:
            self.iterator_closed = True

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


class FiniteAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        yield self.content


def stream_request(pool: StreamAuthorityPool):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pg_pool=pool)),
    )


def stream_control(chunk: bytes) -> dict[str, str]:
    lines = chunk.decode("utf-8").splitlines()
    assert lines[0] == "event: console_stream_control"
    assert lines[1].startswith("data: ")
    payload = json.loads(lines[1].removeprefix("data: "))
    assert set(payload) == {
        "schema_version",
        "code",
        "message",
        "project_id",
        "required_role",
    }
    assert payload["schema_version"] == "console_stream_control@1"
    assert payload["message"]
    return payload


@pytest.mark.asyncio
async def test_proxy_injects_server_key_and_discards_caller_credentials(
    admin_session: AdminSession,
) -> None:
    seen: dict[str, str | None] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        seen["cookie"] = request.headers.get("cookie")
        seen["authorization"] = request.headers.get("authorization")
        seen["request_id"] = request.headers.get("x-request-id")
        return httpx.Response(200, json={"flags": []})

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/flags",
            headers={
                "X-API-Key": "attacker-controlled",
                "Authorization": "Bearer attacker-controlled",
                "Cookie": "untrusted=value",
                "X-Request-ID": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
            },
        )

    assert response.status_code == 200
    assert seen == {
        "key": TEST_API_KEY,
        "cookie": None,
        "authorization": None,
        "request_id": "87fab7d6-dba0-4f77-8ffd-00e815fc7303",
    }


@pytest.mark.asyncio
async def test_proxy_mints_and_removes_ephemeral_key_for_dynamic_project(
    admin_session: AdminSession,
) -> None:
    seen_key = ""

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal seen_key
        seen_key = request.headers["x-api-key"]
        return httpx.Response(200, json={"flags": []})

    settings = make_settings(service_api_keys={})
    async with proxy_client(
        httpx.MockTransport(upstream), admin_session, settings
    ) as client:
        response = client.get("/api/projects/demo/config/v1/flags")
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert re.fullmatch(r"proj_demo_[0-9a-f]{48}", seen_key)
    insert = next(
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    )
    credential_id = insert[1][0]
    assert insert[1][1] == "demo"
    assert insert[1][2] == "proj_demo_"
    assert insert[1][3] == hashlib.sha256(seen_key.encode()).hexdigest()
    assert insert[1][4] == sorted(
        admin_session.projects["demo"] - {"credentials:manage"}
    )
    assert "credentials:manage" not in insert[1][4]
    assert insert[1][5] is None
    assert insert[1][6] == 300
    assert "'confidential'" in insert[0]
    removal = next(
        statement
        for statement in statements
        if "DELETE FROM auth_credentials WHERE credential_id = $1" in statement[0]
    )
    assert removal[1] == (credential_id,)


@pytest.mark.asyncio
async def test_agents_mutation_uses_human_bound_ephemeral_credential(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    seen_key = ""

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal seen_key
        seen_key = request.headers["x-api-key"]
        return httpx.Response(202, json={"status": "queued"})

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        response = client.post(
            "/api/projects/demo/agents/v1/agents/run-1/approve",
            headers={"Origin": "http://admin.test"},
            json={"decisions": [{"item_id": "p1", "approved": True}]},
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 202
    assert seen_key != TEST_API_KEY
    assert re.fullmatch(r"proj_demo_[0-9a-f]{48}", seen_key)
    insert = next(
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    )
    assert str(insert[1][5]) == admin_session.user_id
    removal = next(
        statement
        for statement in statements
        if "DELETE FROM auth_credentials WHERE credential_id = $1" in statement[0]
    )
    assert removal[1] == (insert[1][0],)


@pytest.mark.asyncio
async def test_llm_connection_read_uses_live_management_authority_without_agents_read(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {"demo": frozenset({"config:read"})},
        }
    )
    seen_key = ""

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal seen_key
        seen_key = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "schema_version": "llm_provider_connection_list@1",
                "project_id": "demo",
                "connections": [],
            },
        )

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        response = client.get(
            "/api/projects/demo/agents/v1/agents/llm-connections",
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert seen_key != TEST_API_KEY
    authority = next(
        statement
        for statement in statements
        if "AS llm_connection_authorized" in statement[0]
    )
    assert authority[1] == ("demo", uuid.UUID(admin_session.user_id))
    credential_insert = next(
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    )
    assert credential_insert[1][4] == ["agents:read"]
    assert credential_insert[1][5] == uuid.UUID(admin_session.user_id)
    removal = next(
        statement
        for statement in statements
        if "DELETE FROM auth_credentials WHERE credential_id = $1" in statement[0]
    )
    assert removal[1] == (credential_insert[1][0],)


@pytest.mark.asyncio
async def test_llm_connection_read_fails_without_role_or_live_management_authority(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {"demo": frozenset({"config:read"})},
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        client.app.state.pg_pool.connection.llm_connection_authorized = False
        response = client.get(
            "/api/projects/demo/agents/v1/agents/llm-connections",
        )

    assert response.status_code == 403
    assert "agents:read" in response.json()["detail"]
    assert not called


@pytest.mark.asyncio
async def test_llm_vault_mutation_uses_live_dual_role_authority(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    seen: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key_header"] = request.headers.get("x-api-key")
        seen["project"] = request.headers.get("x-apdl-project-id")
        seen["actor"] = request.headers.get("x-apdl-actor-user-id")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "schema_version": "project_llm_connection@1",
                "connection_id": "10000000-0000-4000-8000-000000000001",
                "project_id": "demo",
                "provider": "openai",
                "version": 1,
            },
        )

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        response = client.post(
            "/api/projects/demo/llm-vault/v1/llm-connections",
            headers={"Origin": "http://admin.test"},
            json={
                "provider": "openai",
                "label": "Primary OpenAI",
                "api_key": "provider-secret",
                "consumers": ["agents", "codegen"],
            },
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 201
    assert seen["body"] == {
        "project_id": "demo",
        "provider": "openai",
        "label": "Primary OpenAI",
        "api_key": "provider-secret",
        "consumers": ["agents", "codegen"],
    }
    assert seen["authorization"] == "Bearer test-vault-admin-token-is-32-bytes-long"
    assert seen["api_key_header"] is None
    assert seen["project"] == "demo"
    assert seen["actor"] == admin_session.user_id
    authority = next(
        statement
        for statement in statements
        if "AS llm_connection_authorized" in statement[0]
    )
    assert authority[1] == ("demo", uuid.UUID(admin_session.user_id))
    mutation_audit = next(
        statement
        for statement in statements
        if "INSERT INTO admin_proxy_audit" in statement[0]
    )
    assert mutation_audit[1][4] == "llm-connections:manage"
    assert mutation_audit[1][5] == "llm-vault"
    assert all("INSERT INTO auth_credentials" not in query for query, _ in statements)
    assert all("provider-secret" not in repr(arguments) for _, arguments in statements)


@pytest.mark.asyncio
async def test_llm_vault_mutation_fails_when_live_authority_is_lost(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        client.app.state.pg_pool.connection.llm_connection_authorized = False
        response = client.post(
            "/api/projects/demo/llm-vault/v1/llm-connections/"
            "10000000-0000-4000-8000-000000000001/refresh",
            headers={"Origin": "http://admin.test"},
            json={"version": 1},
        )

    assert response.status_code == 403
    assert "ownership" in response.json()["detail"]
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/agents/v1/agents/llm-connections/openai"),
        (
            "POST",
            "/agents/v1/agents/llm-connections/openai/refresh-models",
        ),
        ("POST", "/agents/v1/agents/llm-connections/openai/revoke"),
        ("PUT", "/codegen/v1/llm-connections/openai"),
        ("POST", "/codegen/v1/llm-connections/openai/refresh-models"),
        ("POST", "/codegen/v1/llm-connections/openai/revoke"),
    ],
)
async def test_consumer_projection_mutations_are_not_exposed(
    admin_session: AdminSession,
    method: str,
    path: str,
) -> None:
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(
        httpx.MockTransport(upstream), admin_session
    ) as client:
        response = client.request(
            method,
            f"/api/projects/demo{path}",
            headers={"Origin": "http://admin.test"},
            json={"api_key": "provider-secret", "version": 1},
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 404
    assert not called
    assert not any(
        "AS llm_connection_authorized" in query for query, _ in statements
    )


def test_llm_connection_proxy_routes_are_strictly_mapped() -> None:
    assert (
        proxy.required_role(
            "agents",
            "PUT",
            "/v1/agents/llm-connections/openai",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "POST",
            "/v1/agents/llm-connections/google/revoke",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "GET",
            "/v1/agents/llm-connections/xai/models",
        )
        == "llm-connections:read"
    )
    assert (
        proxy.required_role(
            "agents",
            "PUT",
            "/v1/agents/llm-connections/OpenAI",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "POST",
            "/v1/agents/llm-connections/openai/unknown",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "POST",
            "/v1/agents/llm-connections/openai",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "PUT",
            "/v1/agents/llm-connections/openai/revoke",
        )
        == ""
    )

    assert (
        proxy.required_role(
            "codegen",
            "GET",
            "/v1/llm-connections",
        )
        == "agents:read"
    )
    assert (
        proxy.required_role(
            "codegen",
            "GET",
            "/v1/llm-connections/xai/models",
        )
        == "agents:read"
    )
    assert (
        proxy.required_role(
            "codegen",
            "PUT",
            "/v1/llm-connections/openai",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "POST",
            "/v1/llm-connections/google/refresh-models",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "POST",
            "/v1/llm-connections/google/revoke",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "GET",
            "/v1/llm-connections/openai",
        )
        == ""
    )

    connection_id = "10000000-0000-4000-8000-000000000001"
    assert (
        proxy.required_role("llm-vault", "GET", "/v1/llm-connections")
        == "llm-connections:read"
    )
    assert (
        proxy.required_role(
            "llm-vault", "GET", f"/v1/llm-connections/{connection_id}"
        )
        == "llm-connections:read"
    )
    assert (
        proxy.required_role("llm-vault", "POST", "/v1/llm-connections")
        == "llm-connections:manage"
    )
    assert (
        proxy.required_role(
            "llm-vault", "PUT", f"/v1/llm-connections/{connection_id}"
        )
        == "llm-connections:manage"
    )
    for action in ("refresh", "revoke"):
        assert (
            proxy.required_role(
                "llm-vault",
                "POST",
                f"/v1/llm-connections/{connection_id}/{action}",
            )
            == "llm-connections:manage"
        )
    assert (
        proxy.required_role(
            "codegen",
            "PUT",
            "/v1/llm-connections/OpenAI",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "POST",
            "/v1/llm-connections/openai/unknown",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "POST",
            "/v1/llm-connections/openai",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "codegen",
            "PUT",
            "/v1/llm-connections/openai/revoke",
        )
        == ""
    )


@pytest.mark.parametrize(
    ("service", "method", "path"),
    [
        ("config", "POST", "/v1/evaluate"),
        ("query", "POST", "/v1/query/events/count"),
        ("query", "POST", "/v1/query/events/timeseries"),
        ("query", "POST", "/v1/query/events/breakdown"),
        ("query", "POST", "/v1/query/events/names"),
        ("query", "POST", "/v1/query/funnel"),
        ("query", "POST", "/v1/query/retention"),
        ("query", "POST", "/v1/query/cohort"),
        ("query", "POST", "/v1/query/guardrails/evaluate"),
        ("agents", "POST", "/v1/agents/trigger"),
        ("agents", "POST", "/v1/agents/custom/test"),
        ("agents", "PUT", "/v1/agents/setup"),
        ("agents", "POST", "/v1/agents/setup/deactivate"),
        ("codegen", "POST", "/v1/changesets"),
        ("llm-vault", "POST", "/v1/llm-connections"),
        (
            "llm-vault",
            "PUT",
            "/v1/llm-connections/10000000-0000-4000-8000-000000000001",
        ),
        (
            "llm-vault",
            "POST",
            "/v1/llm-connections/10000000-0000-4000-8000-000000000001/refresh",
        ),
        (
            "llm-vault",
            "POST",
            "/v1/llm-connections/10000000-0000-4000-8000-000000000001/revoke",
        ),
    ],
)
def test_upstream_project_body_injection_registry_is_explicit(
    service: str,
    method: str,
    path: str,
) -> None:
    assert proxy._requires_upstream_project_body(service, method, path)


@pytest.mark.parametrize(
    ("service", "method", "path"),
    [
        ("config", "POST", "/v1/admin/flags"),
        ("query", "GET", "/v1/query/events/count"),
        ("agents", "POST", "/v1/agents/custom"),
        ("agents", "PUT", "/v1/agents/llm-connections/openai"),
        (
            "agents",
            "POST",
            "/v1/agents/llm-connections/google/refresh-models",
        ),
        ("agents", "POST", "/v1/agents/llm-connections/xai/revoke"),
        ("agents", "PUT", "/v1/agents/llm-connections/OpenAI"),
        ("agents", "POST", "/v1/agents/llm-connections/openai/unknown"),
        ("codegen", "POST", "/v1/changesets/cs_demo/retry"),
        ("codegen", "PUT", "/v1/llm-connections/anthropic"),
        ("codegen", "POST", "/v1/llm-connections/openai/refresh-models"),
        ("codegen", "POST", "/v1/llm-connections/google/revoke"),
        ("codegen", "PUT", "/v1/llm-connections/openai/models"),
        (
            "llm-vault",
            "PUT",
            "/v1/llm-connections/not-a-canonical-connection-id",
        ),
    ],
)
def test_upstream_project_body_injection_registry_rejects_unregistered_routes(
    service: str,
    method: str,
    path: str,
) -> None:
    assert not proxy._requires_upstream_project_body(service, method, path)


def test_agents_setup_proxy_routes_are_strictly_mapped() -> None:
    assert (
        proxy.required_role("agents", "GET", "/v1/agents/setup")
        == "agents:read"
    )
    assert (
        proxy.required_role("agents", "PUT", "/v1/agents/setup")
        == "llm-connections:manage"
    )
    assert (
        proxy.required_role(
            "agents",
            "POST",
            "/v1/agents/setup/deactivate",
        )
        == "llm-connections:manage"
    )
    assert (
        proxy.required_role("agents", "POST", "/v1/agents/setup") == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "PUT",
            "/v1/agents/setup/deactivate",
        )
        == ""
    )
    assert (
        proxy.required_role(
            "agents",
            "GET",
            "/v1/agents/setup/unknown",
        )
        == ""
    )


def test_codegen_repository_authorization_routes_are_not_exposed() -> None:
    authorization_id = "123e4567-e89b-42d3-a456-426614174000"
    base = f"/v1/github/repository-authorizations/{authorization_id}"

    for method, path in (
        ("POST", "/v1/github/repository-authorizations"),
        ("GET", base),
        ("POST", f"{base}/complete"),
        ("GET", "/v1/github/repos"),
    ):
        assert proxy.required_role("codegen", method, path) == ""


@pytest.mark.asyncio
async def test_agents_setup_status_uses_human_bound_ephemeral_credential(
    admin_session: AdminSession,
) -> None:
    seen_key = ""

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal seen_key
        seen_key = request.headers["x-api-key"]
        return httpx.Response(
            200,
            json={
                "schema_version": "agents_project_setup@1",
                "project_id": "demo",
                "state": "inactive",
                "version": 0,
            },
        )

    async with proxy_client(
        httpx.MockTransport(upstream),
        admin_session,
    ) as client:
        response = client.get("/api/projects/demo/agents/v1/agents/setup")
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert seen_key != TEST_API_KEY
    credential_insert = next(
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    )
    assert credential_insert[1][5] == uuid.UUID(admin_session.user_id)
    credential_removal = next(
        statement
        for statement in statements
        if "DELETE FROM auth_credentials WHERE credential_id = $1"
        in statement[0]
    )
    assert credential_removal[1] == (credential_insert[1][0],)


@pytest.mark.asyncio
async def test_agents_setup_mutation_rechecks_live_dual_role_authority(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    seen_body: object = None

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "schema_version": "agents_project_setup@1",
                "project_id": "demo",
                "state": "active",
                "version": 1,
            },
        )

    async with proxy_client(
        httpx.MockTransport(upstream),
        session,
    ) as client:
        response = client.put(
            "/api/projects/demo/agents/v1/agents/setup",
            headers={"Origin": "http://admin.test"},
            json={
                "fast_model": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "connection_version": 1,
                    "inventory_version": 1,
                },
                "reasoning_model": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-6",
                    "connection_version": 2,
                    "inventory_version": 3,
                },
                "version": 0,
            },
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert seen_body == {
        "project_id": "demo",
        "fast_model": {
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "connection_version": 1,
            "inventory_version": 1,
        },
        "reasoning_model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "connection_version": 2,
            "inventory_version": 3,
        },
        "version": 0,
    }
    authority = next(
        statement
        for statement in statements
        if "AS llm_connection_authorized" in statement[0]
    )
    assert authority[1] == ("demo", uuid.UUID(admin_session.user_id))
    credential_insert = next(
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    )
    assert credential_insert[1][5] == uuid.UUID(admin_session.user_id)


@pytest.mark.asyncio
async def test_codegen_proxy_uses_project_scoped_service_key(
    admin_session: AdminSession,
) -> None:
    seen: list[tuple[str | None, str | None, dict[str, str]]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.headers.get("x-api-key"),
                request.headers.get("x-apdl-internal-token"),
                dict(request.url.params),
            )
        )
        return httpx.Response(200, json=[])

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get("/api/projects/demo/codegen/v1/changesets")

    assert response.status_code == 200
    assert seen == [(TEST_API_KEY, None, {"project_id": "demo"})]


@pytest.mark.asyncio
async def test_codegen_proxy_reuses_ephemeral_project_key_for_scope_and_forward(
    admin_session: AdminSession,
) -> None:
    seen_keys: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen_keys.append(request.headers["x-api-key"])
        if request.url.path == "/v1/changesets/cs_demo":
            return httpx.Response(200, json={"project_id": "demo"})
        return httpx.Response(200, json={"observations": []})

    settings = make_settings(service_api_keys={})
    async with proxy_client(
        httpx.MockTransport(upstream), admin_session, settings
    ) as client:
        response = client.get(
            "/api/projects/demo/codegen/v1/changesets/cs_demo/observations"
        )
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert len(seen_keys) == 2
    assert seen_keys[0] == seen_keys[1]
    assert re.fullmatch(r"proj_demo_[0-9a-f]{48}", seen_keys[0])
    inserts = [
        statement
        for statement in statements
        if "INSERT INTO auth_credentials" in statement[0]
    ]
    assert len(inserts) == 1
    removal = next(
        statement
        for statement in statements
        if "DELETE FROM auth_credentials WHERE credential_id = $1" in statement[0]
    )
    assert removal[1] == (inserts[0][1][0],)


@pytest.mark.asyncio
async def test_proxy_rejects_credentials_in_the_query_string(
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream?api_key=browser-secret"
        )

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
async def test_console_stream_rejects_every_query_parameter(
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream?access_token=browser-secret",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
async def test_console_stream_forwards_only_the_non_secret_reconnect_cursor(
    admin_session: AdminSession,
) -> None:
    seen: dict[str, str | None] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["last_event_id"] = request.headers.get("last-event-id")
        seen["authorization"] = request.headers.get("authorization")
        seen["service_key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "public, max-age=3600",
                "X-API-Key": "upstream-secret",
                "Authorization": "Bearer upstream-secret",
            },
            stream=FiniteAsyncStream(b": heartbeat\r\n\r\n"),
        )

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream",
            headers={
                "Accept": "text/event-stream",
                "Authorization": "Bearer browser-session",
                "Last-Event-ID": "version-17",
            },
        )

    assert response.status_code == 200
    assert response.content == b": heartbeat\r\n\r\n"
    assert seen == {
        "last_event_id": "version-17",
        "authorization": None,
        "service_key": TEST_API_KEY,
    }
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert "x-api-key" not in response.headers
    assert "authorization" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/projects/demo/config/v1/stream?project_id=demo",
        "/api/projects/demo/config/v1/stream?projectId=demo",
        "/api/projects/demo/config/v1/stream?project=demo",
    ],
)
async def test_console_stream_rejects_url_project_aliases(
    path: str,
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(path, headers={"Accept": "text/event-stream"})

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    ["Project-ID", "X-Project-ID", "X-APDL-Project-ID"],
)
async def test_console_stream_rejects_header_project_aliases(
    header: str,
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream",
            headers={"Accept": "text/event-stream", header: "demo"},
        )

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize("last_event_id", ["", "x" * 257, "bad\x00id"])
async def test_console_stream_rejects_noncanonical_last_event_id(
    last_event_id: str,
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream",
            headers={"Accept": "text/event-stream", "Last-Event-ID": last_event_id},
        )

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
async def test_sse_rechecks_membership_on_a_timer_without_upstream_chunks(
    admin_session: AdminSession,
) -> None:
    pool = StreamAuthorityPool(
        [
            {"session_active": True, "project_authorized": True},
            {"session_active": True, "project_authorized": False},
        ]
    )
    upstream = StubStreamingResponse()
    generator = proxy._authorized_sse(
        upstream,
        stream_request(pool),
        admin_session,
        make_settings(stream_authority_check_seconds=0.01),
        "demo",
        "config:read",
        None,
    )

    terminal = await asyncio.wait_for(anext(generator), timeout=0.2)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert stream_control(terminal) == {
        "schema_version": "console_stream_control@1",
        "code": "project_access_revoked",
        "message": "Project access was revoked.",
        "project_id": "demo",
        "required_role": "config:read",
    }
    assert upstream.closed
    assert len(pool.connection.calls) == 2
    query, args = pool.connection.calls[-1]
    assert "FROM admin_user_projects AS membership" in query
    assert "$5::TEXT = ANY(membership.roles)" in query
    assert args[3:] == ("demo", "config:read")


@pytest.mark.asyncio
async def test_console_stream_reconnect_gets_forbidden_after_project_loss(
    admin_session: AdminSession,
) -> None:
    restricted = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {},
        }
    )
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with proxy_client(httpx.MockTransport(upstream), restricted) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 403
    assert not called


@pytest.mark.asyncio
async def test_console_stream_rejects_a_non_sse_upstream_response(
    admin_session: AdminSession,
) -> None:
    async with proxy_client(
        httpx.MockTransport(lambda _request: httpx.Response(200, json={"flags": []})),
        admin_session,
    ) as client:
        response = client.get(
            "/api/projects/demo/config/v1/stream",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_sse_distinguishes_session_expiry_and_fails_closed_on_db_error(
    admin_session: AdminSession,
) -> None:
    settings = make_settings(stream_authority_check_seconds=0.01)

    expired_upstream = StubStreamingResponse()
    expired = proxy._authorized_sse(
        expired_upstream,
        stream_request(
            StreamAuthorityPool([{"session_active": False, "project_authorized": True}])
        ),
        admin_session,
        settings,
        "demo",
        "config:read",
        None,
    )
    assert stream_control(await anext(expired))["code"] == "session_expired"
    await expired.aclose()
    assert expired_upstream.closed

    failed_upstream = StubStreamingResponse()
    failed = proxy._authorized_sse(
        failed_upstream,
        stream_request(StreamAuthorityPool([ConnectionError("postgres down")])),
        admin_session,
        settings,
        "demo",
        "config:read",
        None,
    )
    assert stream_control(await anext(failed))["code"] == (
        "authorization_unavailable"
    )
    await failed.aclose()
    assert failed_upstream.closed


@pytest.mark.asyncio
async def test_busy_sse_cannot_starve_periodic_role_revalidation(
    admin_session: AdminSession,
) -> None:
    pool = StreamAuthorityPool(
        [
            {"session_active": True, "project_authorized": True},
            {"session_active": True, "project_authorized": False},
        ]
    )
    upstream = StubStreamingResponse(busy=True)
    generator = proxy._authorized_sse(
        upstream,
        stream_request(pool),
        admin_session,
        make_settings(stream_authority_check_seconds=0.01),
        "demo",
        "config:read",
        None,
    )

    chunk_count = 0
    while True:
        chunk = await asyncio.wait_for(anext(generator), timeout=0.2)
        if b"project_access_revoked" in chunk:
            break
        chunk_count += 1
    await generator.aclose()

    assert chunk_count > 0
    assert len(pool.connection.calls) == 2
    assert upstream.closed


@pytest.mark.asyncio
async def test_sse_preserves_split_utf8_and_every_sse_field_byte_for_byte(
    admin_session: AdminSession,
) -> None:
    chunks = (
        b"id: version-18\r\nretry: 1500\r\nevent: con",
        b"fig\r\ndata: caf\xc3",
        b"\xa9\r\ndata: second line\r\n\r\n: heartbeat\r\n\r\n",
    )
    upstream = StubStreamingResponse(chunks=chunks, hold_open=False)
    generator = proxy._authorized_sse(
        upstream,
        stream_request(
            StreamAuthorityPool(
                [{"session_active": True, "project_authorized": True}]
            )
        ),
        admin_session,
        make_settings(stream_authority_check_seconds=1),
        "demo",
        "config:read",
        None,
    )

    received = [chunk async for chunk in generator]

    assert received == list(chunks)
    assert b"".join(received).decode("utf-8") == (
        "id: version-18\r\n"
        "retry: 1500\r\n"
        "event: config\r\n"
        "data: caf\N{LATIN SMALL LETTER E WITH ACUTE}\r\n"
        "data: second line\r\n\r\n"
        ": heartbeat\r\n\r\n"
    )
    assert upstream.closed
    assert upstream.iterator_closed


@pytest.mark.asyncio
async def test_sse_delivers_the_first_chunk_before_upstream_completion(
    admin_session: AdminSession,
) -> None:
    upstream = StubStreamingResponse(chunks=(b": heartbeat\n\n",))
    generator = proxy._authorized_sse(
        upstream,
        stream_request(
            StreamAuthorityPool(
                [{"session_active": True, "project_authorized": True}]
            )
        ),
        admin_session,
        make_settings(stream_authority_check_seconds=1),
        "demo",
        "config:read",
        None,
    )

    assert await asyncio.wait_for(anext(generator), timeout=0.2) == b": heartbeat\n\n"
    assert not upstream.closed
    await generator.aclose()
    assert upstream.closed
    assert upstream.iterator_closed


@pytest.mark.asyncio
async def test_sse_client_cancellation_closes_upstream_immediately(
    admin_session: AdminSession,
) -> None:
    upstream = StubStreamingResponse()
    generator = proxy._authorized_sse(
        upstream,
        stream_request(
            StreamAuthorityPool(
                [{"session_active": True, "project_authorized": True}]
            )
        ),
        admin_session,
        make_settings(stream_authority_check_seconds=1),
        "demo",
        "config:read",
        None,
    )
    consumer = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert upstream.closed
    assert upstream.iterator_closed


@pytest.mark.asyncio
async def test_every_proxied_event_stream_uses_current_project_role(
    admin_session: AdminSession,
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/query/funnels"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=FiniteAsyncStream(b"event: result\ndata: {}\n\n"),
        )

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get("/api/projects/demo/query/v1/query/funnels")
        statements = client.app.state.audit_statements

    assert response.status_code == 200
    assert response.content == b"event: result\ndata: {}\n\n"
    authority_check = next(
        statement for statement in statements if "AS project_authorized" in statement[0]
    )
    assert authority_check[1][3:] == ("demo", "query:read")


@pytest.mark.asyncio
async def test_proxy_hides_projects_outside_the_session(
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get("/api/projects/other/config/v1/flags")

    assert response.status_code == 404
    assert not called


@pytest.mark.asyncio
async def test_proxy_requires_role_before_calling_upstream(
    admin_session: AdminSession,
) -> None:
    restricted = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {"demo": frozenset({"config:read"})},
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), restricted) as client:
        response = client.post(
            "/api/projects/demo/config/v1/admin/flags",
            headers={"Origin": "http://admin.test"},
            json={"key": "test"},
        )

    assert response.status_code == 403
    assert not called


@pytest.mark.asyncio
async def test_self_registered_project_cannot_mint_agents_execution_credential(
    admin_session: AdminSession,
) -> None:
    self_registered = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {
                "demo": frozenset(
                    {
                        "events:write",
                        "config:read",
                        "config:write",
                        "config:evaluate",
                        "query:read",
                        "agents:read",
                    }
                )
            },
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(202)

    async with proxy_client(
        httpx.MockTransport(upstream),
        self_registered,
        make_settings(service_api_keys={}),
    ) as client:
        response = client.post("/api/projects/demo/agents/v1/agents/trigger")
        statements = client.app.state.audit_statements

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient role"}
    assert not called
    assert statements == []


@pytest.mark.asyncio
async def test_agents_execution_capability_requires_run_role_at_proxy(
    admin_session: AdminSession,
) -> None:
    read_only = AdminSession(
        **{
            **admin_session.__dict__,
            "projects": {"demo": frozenset({"agents:read"})},
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), read_only) as client:
        response = client.get(
            "/api/projects/demo/agents/v1/agents/capabilities/execution"
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient role"}
    assert not called


@pytest.mark.asyncio
async def test_proxy_does_not_expose_global_repository_onboarding(
    admin_session: AdminSession,
) -> None:
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        repositories = client.get("/api/projects/demo/codegen/v1/github/repos")
        connect = client.post(
            "/api/projects/demo/codegen/v1/connections",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "demo", "repo": "other-tenant/secret"},
        )

    assert repositories.status_code == 404
    assert connect.status_code == 404
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "method", "path", "body"),
    [
        (
            "config",
            "POST",
            "/v1/evaluate",
            {"key": "checkout", "context": {}},
        ),
        (
            "query",
            "POST",
            "/v1/query/events/count",
            {
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
                "selectors": [{"event_name": "checkout", "filters": []}],
            },
        ),
        (
            "agents",
            "POST",
            "/v1/agents/trigger",
            {"agents": ["behavior_analysis"]},
        ),
        (
            "codegen",
            "POST",
            "/v1/changesets",
            {"proposal_id": "proposal-1"},
        ),
    ],
)
async def test_proxy_injects_path_project_into_registered_upstream_json_routes(
    admin_session: AdminSession,
    service: str,
    method: str,
    path: str,
    body: dict[str, object],
) -> None:
    seen: list[dict[str, object]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.request(
            method,
            f"/api/projects/demo/{service}{path}",
            headers={"Origin": "http://admin.test"},
            json=body,
        )

    assert response.status_code == 200
    assert seen == [{**body, "project_id": "demo"}]


@pytest.mark.asyncio
async def test_proxy_does_not_inject_project_into_unregistered_json_body_route(
    admin_session: AdminSession,
) -> None:
    seen: list[dict[str, object]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(201, json={"created": True})

    body = {"key": "checkout", "default_variant": "control"}
    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.post(
            "/api/projects/demo/config/v1/admin/flags",
            headers={"Origin": "http://admin.test"},
            json=body,
        )

    assert response.status_code == 201
    assert seen == [body]


@pytest.mark.asyncio
async def test_proxy_uses_bearer_authority_and_validates_project_assertions(
    admin_session: AdminSession,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    bodies: list[dict] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json={"accepted": 1})

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        bearer_only = client.post(
            "/api/projects/demo/ingestion/v1/events",
            headers={"Origin": "http://admin.test"},
            json={"events": []},
        )
        mismatch = client.post(
            "/api/projects/demo/ingestion/v1/events",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "other", "events": []},
        )
        query_alias = client.post(
            "/api/projects/demo/ingestion/v1/events?project_id=demo",
            headers={"Origin": "http://admin.test"},
            json={"events": []},
        )
        header_alias = client.post(
            "/api/projects/demo/ingestion/v1/events",
            headers={
                "Origin": "http://admin.test",
                "X-APDL-Project-ID": "demo",
            },
            json={"events": []},
        )
        accepted = client.post(
            "/api/projects/demo/ingestion/v1/events",
            headers={"Origin": "http://admin.test"},
            json={"project_id": "demo", "events": []},
        )
        audit_statements = client.app.state.audit_statements

    assert bearer_only.status_code == 202
    assert mismatch.status_code == 400
    assert query_alias.status_code == 400
    assert header_alias.status_code == 400
    assert accepted.status_code == 400
    assert bodies == [{"events": []}]
    insert = next(
        statement for statement in audit_statements if "INSERT INTO" in statement[0]
    )
    completed = next(
        statement for statement in audit_statements if "UPDATE" in statement[0]
    )
    assert str(insert[1][1]) == "20000000-0000-4000-8000-000000000002"
    assert insert[1][2:8] == (
        "admin@example.com",
        "demo",
        "events:write",
        "ingestion",
        "POST",
        "/v1/events",
    )
    assert completed[1][1] == 202
    assert "{'events':" not in repr(insert[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "alias"),
    [
        ("body", "projectId"),
        ("body", "Project-ID"),
        ("body", "x_apdl_project_id"),
        ("query", "projectId"),
        ("query", "Project-ID"),
        ("query", "x_apdl_project_id"),
        ("header", "Project_ID"),
        ("header", "X-ProjectId"),
        ("header", "X-APDL-Project_ID"),
    ],
)
async def test_proxy_rejects_normalized_project_scope_aliases(
    admin_session: AdminSession,
    location: str,
    alias: str,
) -> None:
    called = False

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(202, json={"accepted": 1})

    path = "/api/projects/demo/ingestion/v1/events"
    headers: dict[str, str] = {"Origin": "http://admin.test"}
    body: dict[str, object] = {"events": []}
    if location == "body":
        body[alias] = "demo"
    elif location == "query":
        path = f"{path}?{alias}=demo"
    else:
        headers[alias] = "demo"

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.post(path, headers=headers, json=body)

    assert response.status_code == 400
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type",
    [
        "application/merge-patch+json",
        "application/vnd.apdl+json",
        "text/json",
    ],
)
async def test_codegen_proxy_rejects_noncanonical_json_media_types(
    admin_session: AdminSession,
    media_type: str,
) -> None:
    session = AdminSession(
        **{
            **admin_session.__dict__,
        }
    )
    called = False

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, json={"changeset_id": "changeset-1"})

    async with proxy_client(httpx.MockTransport(upstream), session) as client:
        response = client.post(
            "/api/projects/demo/codegen/v1/changesets",
            headers={
                "Origin": "http://admin.test",
                "Content-Type": media_type,
            },
            content=json.dumps({"project_id": "other"}),
        )

    assert response.status_code == 415
    assert response.json() == {
            "detail": "Upstream request bodies must use application/json"
    }
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changeset_path",
    [
        "/v1/changesets/cs_other",
        "/v1/changesets/cs_other/observations",
        "/v1/changesets/cs_other/runtime-observations",
        "/v1/changesets/cs_other/future-child-resource",
    ],
)
async def test_codegen_proxy_hides_every_cross_tenant_changeset_child_route(
    admin_session: AdminSession,
    changeset_path: str,
) -> None:
    seen_paths: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"project_id": "other"})

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(f"/api/projects/demo/codegen{changeset_path}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Changeset not found"}
    assert seen_paths == ["/v1/changesets/cs_other"]


@pytest.mark.asyncio
async def test_codegen_proxy_hides_project_forbidden_changeset_as_not_found(
    admin_session: AdminSession,
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/changesets/cs_other"
        return httpx.Response(status_code=403)

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            "/api/projects/demo/codegen/v1/changesets/cs_other/observations"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Changeset not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "child_path",
    ["observations", "runtime-observations"],
)
async def test_codegen_proxy_forwards_authorized_changeset_child_routes(
    admin_session: AdminSession,
    child_path: str,
) -> None:
    seen_paths: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/v1/changesets/cs_demo":
            return httpx.Response(200, json={"project_id": "demo"})
        return httpx.Response(200, json={"observations": []})

    async with proxy_client(httpx.MockTransport(upstream), admin_session) as client:
        response = client.get(
            f"/api/projects/demo/codegen/v1/changesets/cs_demo/{child_path}"
        )

    assert response.status_code == 200
    assert seen_paths == [
        "/v1/changesets/cs_demo",
        f"/v1/changesets/cs_demo/{child_path}",
    ]
