from __future__ import annotations

import asyncio
import ipaddress
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest

from app.config import GatewaySettings
from app.main import _response_body, create_app


REQUEST_ID = "11111111-1111-4111-8111-111111111111"


def settings(**changes: object) -> GatewaySettings:
    return replace(GatewaySettings.from_env({}), **changes)


@asynccontextmanager
async def gateway_client(
    upstream: httpx.AsyncBaseTransport,
    *,
    configured: GatewaySettings | None = None,
    client_address: tuple[str, int] = ("127.0.0.1", 1234),
    rate_limit_clock: Callable[[], float] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        configured or settings(),
        transport=upstream,
        rate_limit_clock=rate_limit_clock,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=client_address),
            base_url="http://localhost:8000",
            headers={"Host": "localhost:8000"},
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_api_path_query_and_required_headers_are_preserved() -> None:
    seen: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["origin"] = request.headers.get("origin")
        seen["last_event_id"] = request.headers.get("last-event-id")
        seen["request_id"] = request.headers.get("x-request-id")
        seen["forwarded_host"] = request.headers.get("x-forwarded-host")
        seen["forwarded_proto"] = request.headers.get("x-forwarded-proto")
        seen["cookie"] = request.headers.get("cookie")
        seen["dynamic_hop"] = request.headers.get("x-internal-hop")
        return httpx.Response(
            200,
            json={"ok": True},
            headers={
                "Connection": "X-Upstream-Hop",
                "X-Upstream-Hop": "private",
                "Set-Cookie": "internal=secret",
                "X-Request-ID": "replaced",
            },
        )

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(
            "/api/projects/demo/config/v1/admin/flags/a%2Fb?limit=3&cursor=x",
            headers={
                "Authorization": "Bearer human-session",
                "Origin": "https://console.apdl.dev",
                "Last-Event-ID": "event-7",
                "X-Request-ID": REQUEST_ID,
                "Cookie": "ambient=credential",
                "Connection": "X-Internal-Hop",
                "X-Internal-Hop": "private",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert seen == {
        "url": (
            "http://admin-api:8085/api/projects/demo/config/v1/admin/flags/"
            "a%2Fb?limit=3&cursor=x"
        ),
        "authorization": "Bearer human-session",
        "origin": "https://console.apdl.dev",
        "last_event_id": "event-7",
        "request_id": REQUEST_ID,
        "forwarded_host": "localhost:8000",
        "forwarded_proto": "http",
        "cookie": None,
        "dynamic_hop": None,
    }
    assert response.headers["x-request-id"] == REQUEST_ID
    assert "set-cookie" not in response.headers
    assert "x-upstream-hop" not in response.headers


@pytest.mark.asyncio
async def test_api_rate_limit_is_canonical_and_sdk_routes_are_independent() -> None:
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(204)

    configured = settings(
        api_rate_limit=1,
        api_rate_window_seconds=17,
        api_rate_max_clients=10,
    )
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
        rate_limit_clock=lambda: 100.0,
    ) as client:
        first_sdk = await client.get("/v1/flags")
        allowed = await client.get(
            "/api/console/v1/manifest",
            headers={"X-Request-ID": REQUEST_ID},
        )
        limited = await client.get(
            "/api/console/v1/session",
            headers={"X-Request-ID": REQUEST_ID},
        )
        second_sdk = await client.get("/v1/flags")

    assert first_sdk.status_code == second_sdk.status_code == 204
    assert allowed.status_code == 204
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "17"
    assert limited.headers["x-request-id"] == REQUEST_ID
    assert limited.json() == {
        "schema_version": "error@1",
        "code": "rate_limited",
        "message": "Too many requests. Try again later.",
        "request_id": REQUEST_ID,
    }
    assert calls == [
        "/v1/flags",
        "/api/console/v1/manifest",
        "/v1/flags",
    ]


@pytest.mark.asyncio
async def test_options_does_not_consume_the_future_cors_actual_request_budget() -> (
    None
):
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(204)

    configured = settings(
        api_rate_limit=1,
        api_rate_window_seconds=60,
        api_rate_max_clients=10,
    )
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
    ) as client:
        options = await client.options("/api/console/v1/session")
        allowed = await client.get("/api/console/v1/session")
        limited = await client.get("/api/console/v1/session")

    assert options.status_code == allowed.status_code == 204
    assert limited.status_code == 429
    assert calls.count("GET") == 1


@pytest.mark.asyncio
async def test_trusted_forwarded_client_is_canonical_and_drives_rate_limit() -> None:
    forwarded: list[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        forwarded.append(request.headers.get_list("x-forwarded-for"))
        return httpx.Response(204)

    configured = settings(
        trusted_proxy_cidrs=(ipaddress.ip_network("127.0.0.1/32"),),
        api_rate_limit=1,
        api_rate_window_seconds=60,
        api_rate_max_clients=10,
    )
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
    ) as client:
        first = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        independent = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "2001:0db8:0:0::5"},
        )
        limited = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )

    assert first.status_code == independent.status_code == 204
    assert limited.status_code == 429
    assert forwarded == [["203.0.113.9"], ["2001:db8::5"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forwarded_headers",
    [
        [("X-Forwarded-For", "203.0.113.9, 198.51.100.7")],
        [
            ("X-Forwarded-For", "203.0.113.9"),
            ("X-Forwarded-For", "198.51.100.7"),
        ],
        [("X-Forwarded-For", "not-an-ip")],
        [("X-Forwarded-For", "fe80::1%25eth0")],
    ],
)
async def test_ambiguous_forwarding_never_reaches_admin_as_a_chain(
    forwarded_headers: list[tuple[str, str]],
) -> None:
    seen: list[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get_list("x-forwarded-for"))
        return httpx.Response(204)

    configured = settings(
        trusted_proxy_cidrs=(ipaddress.ip_network("127.0.0.1/32"),)
    )
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
    ) as client:
        response = await client.get("/api/health", headers=forwarded_headers)

    assert response.status_code == 204
    assert seen == [["127.0.0.1"]]


@pytest.mark.asyncio
async def test_untrusted_peer_cannot_rotate_spoofed_rate_limit_identities() -> None:
    seen: list[list[str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get_list("x-forwarded-for"))
        return httpx.Response(204)

    configured = settings(
        api_rate_limit=1,
        api_rate_window_seconds=60,
        api_rate_max_clients=10,
    )
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
    ) as client:
        allowed = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        limited = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "198.51.100.7"},
        )

    assert allowed.status_code == 204
    assert limited.status_code == 429
    assert seen == [["127.0.0.1"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_origin"),
    [
        ("/v1/events", "http://ingestion:8080/v1/events"),
        ("/v1/flags", "http://config:8081/v1/flags"),
        ("/v1/stream", "http://config:8081/v1/stream"),
    ],
)
async def test_only_registered_sdk_routes_reach_the_expected_service(
    path: str,
    expected_origin: str,
) -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.request("POST" if path == "/v1/events" else "GET", path)

    assert response.status_code == 200
    assert response.text == "ok"
    assert seen == [expected_origin]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/assets/app.js",
        "/api",
        "/v1",
        "/v1/events/",
        "/v1/unknown",
        "/anything-else",
    ],
)
async def test_unknown_and_ui_routes_are_canonical_404(path: str) -> None:
    calls = 0

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json()["schema_version"] == "error@1"
    assert response.json()["code"] == "route_not_found"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert calls == 0


@pytest.mark.asyncio
async def test_unsupported_method_is_a_canonical_error() -> None:
    async with gateway_client(
        httpx.MockTransport(lambda _request: httpx.Response(200))
    ) as client:
        response = await client.request("TRACE", "/api/console/v1/manifest")

    assert response.status_code == 405
    assert response.json()["schema_version"] == "error@1"
    assert response.json()["code"] == "method_not_allowed"
    assert response.headers["x-request-id"] == response.json()["request_id"]


@pytest.mark.asyncio
async def test_invalid_host_is_rejected_before_upstream_dispatch() -> None:
    calls = 0

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(
            "/api/console/v1/manifest",
            headers={"Host": "attacker.example"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_host"
    assert calls == 0


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_upstream_dispatch() -> None:
    calls = 0

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    configured = settings(max_request_body_bytes=4, max_event_body_bytes=3)
    async with gateway_client(
        httpx.MockTransport(upstream),
        configured=configured,
    ) as client:
        api_response = await client.post("/api/write", content=b"12345")
        event_response = await client.post("/v1/events", content=b"1234")

    for response in (api_response, event_response):
        assert response.status_code == 413
        assert response.json()["schema_version"] == "error@1"
        assert response.json()["code"] == "payload_too_large"
        assert response.headers["x-request-id"] == response.json()["request_id"]
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_lengths",
    [(b"",), (b"+4",), (b" 4",), (b"four",), (b"4", b"4")],
)
async def test_ambiguous_content_length_is_rejected(
    content_lengths: tuple[bytes, ...],
) -> None:
    calls = 0

    def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    app = create_app(settings(), transport=httpx.MockTransport(upstream))
    async with app.router.lifespan_context(app):
        sent: list[dict[str, object]] = []
        received = False

        async def receive() -> dict[str, object]:
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": b"1234", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/write",
                "raw_path": b"/api/write",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"localhost:8000"),
                    *((b"content-length", value) for value in content_lengths),
                ],
                "client": ("127.0.0.1", 1234),
                "server": ("localhost", 8000),
            },
            receive,
            send,
        )

    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    assert start["status"] == 400
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "status_code", "code"),
    [
        (httpx.ConnectError, 502, "upstream_unavailable"),
        (httpx.ConnectTimeout, 504, "upstream_timeout"),
        (httpx.ReadTimeout, 504, "upstream_timeout"),
    ],
)
async def test_gateway_transport_failures_use_canonical_errors(
    exception_type: type[httpx.RequestError],
    status_code: int,
    code: str,
) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise exception_type("safe test failure", request=request)

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(
            "/api/console/v1/manifest",
            headers={"X-Request-ID": REQUEST_ID},
        )

    assert response.status_code == status_code
    assert response.json() == {
        "schema_version": "error@1",
        "code": code,
        "message": (
            "The APDL service did not respond in time."
            if status_code == 504
            else "The APDL service is temporarily unavailable."
        ),
        "request_id": REQUEST_ID,
    }
    assert response.headers["x-request-id"] == REQUEST_ID


@pytest.mark.asyncio
async def test_upstream_redirects_are_never_exposed_to_the_browser() -> None:
    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "https://internal.example/"})

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get("/api/console/v1/manifest")

    assert response.status_code == 502
    assert response.json()["code"] == "upstream_redirect_rejected"
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_malformed_request_id_is_replaced_with_a_uuid4() -> None:
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-request-id"])
        return httpx.Response(204)

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(
            "/api/health",
            headers={"X-Request-ID": "not-a-uuid"},
        )

    generated = uuid.UUID(response.headers["x-request-id"])
    assert generated.version == 4
    assert seen == [str(generated)]


@pytest.mark.asyncio
async def test_canonical_uuid_request_id_is_preserved() -> None:
    incoming = "123e4567-e89b-12d3-a456-426614174000"

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-request-id"] == incoming
        return httpx.Response(204)

    async with gateway_client(httpx.MockTransport(upstream)) as client:
        response = await client.get(
            "/api/health",
            headers={"X-Request-ID": incoming},
        )

    assert response.headers["x-request-id"] == incoming


class PausedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'event: ready\ndata: {"schema_version":"ready@1"}\n\n'
        await self.release.wait()
        yield b": heartbeat\n\n"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_upstream_response_iterator_delivers_first_chunk_before_completion() -> (
    None
):
    stream = PausedStream()
    response = httpx.Response(200, stream=stream)
    iterator = _response_body(response)

    first = await asyncio.wait_for(anext(iterator), timeout=0.1)
    assert first.startswith(b"event: ready")
    assert not stream.closed

    stream.release.set()
    assert await asyncio.wait_for(anext(iterator), timeout=0.1) == b": heartbeat\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert stream.closed


def test_gateway_errors_never_serialize_headers_or_bodies() -> None:
    # A focused source-level guard keeps future diagnostics from including the
    # two credential-bearing objects in gateway-generated messages or logs.
    from app import main

    constants: Iterator[object] = iter(main.gateway.__code__.co_consts)
    serialized = json.dumps([value for value in constants if isinstance(value, str)])
    assert "authorization" not in serialized.lower()
    assert "password" not in serialized.lower()
