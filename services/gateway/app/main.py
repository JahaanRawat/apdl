"""ASGI edge exposing one direct APDL backend origin."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.config import GatewaySettings

logger = logging.getLogger(__name__)

_SUPPORTED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_ROUTED_METHODS = [*_SUPPORTED_METHODS, "CONNECT", "TRACE"]
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUEST_HEADER_DENYLIST = _HOP_BY_HOP_HEADERS | frozenset(
    {
        "content-length",
        "cookie",
        "expect",
        "forwarded",
        "host",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "x-request-id",
    }
)
_RESPONSE_HEADER_DENYLIST = _HOP_BY_HOP_HEADERS | frozenset(
    {
        "set-cookie",
        "x-request-id",
    }
)


class RequestBodyTooLarge(Exception):
    """Raised before upstream dispatch when the public body limit is exceeded."""


@dataclass(frozen=True)
class UpstreamRoute:
    name: Literal["admin-api", "ingestion", "config"]
    origin: str
    max_body_bytes: int
    long_read_timeout: bool = False


def _canonical_request_id(request: Request) -> str:
    values = request.headers.getlist("x-request-id")
    if len(values) == 1:
        try:
            parsed = uuid.UUID(values[0])
        except (ValueError, AttributeError):
            pass
        else:
            if str(parsed) == values[0]:
                return values[0]
    return str(uuid.uuid4())


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema_version": "error@1",
            "code": code,
            "message": message,
            "request_id": request_id,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Request-ID": request_id,
        },
    )


def _request_host(request: Request) -> str | None:
    try:
        values = [
            value.decode("ascii", errors="strict")
            for name, value in request.scope["headers"]
            if name.lower() == b"host"
        ]
    except UnicodeDecodeError:
        return None
    if len(values) != 1:
        return None
    value = values[0]
    if value != value.strip() or value != value.lower():
        return None
    return value


def _route_for(request: Request, settings: GatewaySettings) -> UpstreamRoute | None:
    path = request.scope["path"]
    if path.startswith("/api/"):
        accepts_sse = "text/event-stream" in request.headers.get("accept", "").lower()
        return UpstreamRoute(
            name="admin-api",
            origin=settings.admin_api_origin,
            max_body_bytes=settings.max_request_body_bytes,
            long_read_timeout=accepts_sse,
        )
    if path == "/v1/events":
        return UpstreamRoute(
            name="ingestion",
            origin=settings.ingestion_origin,
            max_body_bytes=settings.max_event_body_bytes,
        )
    if path == "/v1/flags":
        return UpstreamRoute(
            name="config",
            origin=settings.config_origin,
            max_body_bytes=settings.max_request_body_bytes,
        )
    if path == "/v1/stream":
        return UpstreamRoute(
            name="config",
            origin=settings.config_origin,
            max_body_bytes=settings.max_request_body_bytes,
            long_read_timeout=True,
        )
    return None


async def _bounded_body(request: Request, maximum: int) -> bytes:
    content_lengths = [
        value
        for name, value in request.scope["headers"]
        if name.lower() == b"content-length"
    ]
    declared: int | None = None
    if content_lengths:
        if len(content_lengths) != 1 or not content_lengths[0].isdigit():
            raise ValueError("invalid content length")
        declared = int(content_lengths[0])
        if declared > maximum:
            raise RequestBodyTooLarge

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise RequestBodyTooLarge
        body.extend(chunk)
    if declared is not None and declared != len(body):
        raise ValueError("content length differs from request body")
    return bytes(body)


def _target_url(request: Request, origin: str) -> httpx.URL:
    raw_path = request.scope.get("raw_path") or request.scope["path"].encode("utf-8")
    raw_query = request.scope.get("query_string", b"")
    combined = raw_path + (b"?" + raw_query if raw_query else b"")
    return httpx.URL(origin).copy_with(raw_path=combined)


def _upstream_headers(
    request: Request,
    *,
    request_id: str,
    public_scheme: str,
    public_host: str,
) -> list[tuple[str, str]]:
    denied = set(_REQUEST_HEADER_DENYLIST)
    for value in request.headers.getlist("connection"):
        denied.update(token.strip().lower() for token in value.split(","))
    headers = [
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in request.scope["headers"]
        if name.decode("latin-1").lower() not in denied
    ]
    headers.append(("X-Request-ID", request_id))
    headers.append(("X-Forwarded-Host", public_host))
    headers.append(("X-Forwarded-Proto", public_scheme))
    if request.client is not None:
        headers.append(("X-Forwarded-For", request.client.host))
    return headers


def _response_headers(response: httpx.Response, request_id: str) -> dict[str, str]:
    denied = set(_RESPONSE_HEADER_DENYLIST)
    for value in response.headers.get_list("connection"):
        denied.update(token.strip().lower() for token in value.split(","))
    headers = {
        name: value
        for name, value in response.headers.multi_items()
        if name.lower() not in denied
    }
    headers["X-Request-ID"] = request_id
    return headers


async def _response_body(response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        # Mock transports may return an already-buffered response even when the
        # client requested streaming. Real HTTP transports stay incremental.
        if response.is_stream_consumed:
            yield response.content
        else:
            async for chunk in response.aiter_raw():
                yield chunk
    finally:
        await response.aclose()


async def gateway(request: Request) -> Response:
    settings: GatewaySettings = request.app.state.settings
    request_id = _canonical_request_id(request)
    public_host = _request_host(request)
    if public_host is None or public_host not in settings.allowed_hosts:
        return _error_response(
            status_code=400,
            code="invalid_host",
            message="The request Host is not configured for this APDL backend.",
            request_id=request_id,
        )

    if request.method not in _SUPPORTED_METHODS:
        return _error_response(
            status_code=405,
            code="method_not_allowed",
            message="The request method is not supported by the APDL gateway.",
            request_id=request_id,
        )

    route = _route_for(request, settings)
    if route is None:
        return _error_response(
            status_code=404,
            code="route_not_found",
            message="The requested route is not part of the public APDL gateway.",
            request_id=request_id,
        )

    try:
        body = await _bounded_body(request, route.max_body_bytes)
    except RequestBodyTooLarge:
        return _error_response(
            status_code=413,
            code="payload_too_large",
            message="Request body exceeds the configured limit.",
            request_id=request_id,
        )
    except (ValueError, ClientDisconnect):
        return _error_response(
            status_code=400,
            code="invalid_request_body",
            message="The request body could not be read safely.",
            request_id=request_id,
        )

    client: httpx.AsyncClient = (
        request.app.state.stream_client
        if route.long_read_timeout
        else request.app.state.client
    )
    upstream_request = client.build_request(
        request.method,
        _target_url(request, route.origin),
        headers=_upstream_headers(
            request,
            request_id=request_id,
            public_scheme=settings.public_scheme,
            public_host=public_host,
        ),
        content=body,
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        logger.warning(
            "Gateway upstream timeout request_id=%s route=%s",
            request_id,
            route.name,
        )
        return _error_response(
            status_code=504,
            code="upstream_timeout",
            message="The APDL service did not respond in time.",
            request_id=request_id,
        )
    except httpx.RequestError:
        logger.warning(
            "Gateway upstream unavailable request_id=%s route=%s",
            request_id,
            route.name,
        )
        return _error_response(
            status_code=502,
            code="upstream_unavailable",
            message="The APDL service is temporarily unavailable.",
            request_id=request_id,
        )

    if 300 <= upstream.status_code < 400:
        await upstream.aclose()
        return _error_response(
            status_code=502,
            code="upstream_redirect_rejected",
            message="The APDL service attempted an unsupported redirect.",
            request_id=request_id,
        )

    headers = _response_headers(upstream, request_id)
    if request.method == "HEAD" or upstream.status_code in {204, 304}:
        await upstream.aclose()
        return Response(status_code=upstream.status_code, headers=headers)
    return StreamingResponse(
        _response_body(upstream),
        status_code=upstream.status_code,
        headers=headers,
    )


def create_app(
    settings: GatewaySettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    resolved = settings or GatewaySettings.from_env()
    limits = httpx.Limits(
        max_connections=resolved.max_connections,
        max_keepalive_connections=resolved.max_keepalive_connections,
    )

    def timeout(read_seconds: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=resolved.connect_timeout_seconds,
            read=read_seconds,
            write=resolved.write_timeout_seconds,
            pool=resolved.pool_timeout_seconds,
        )

    @asynccontextmanager
    async def lifespan(application: Starlette):
        application.state.settings = resolved
        async with (
            httpx.AsyncClient(
                timeout=timeout(resolved.read_timeout_seconds),
                limits=limits,
                follow_redirects=False,
                transport=transport,
            ) as client,
            httpx.AsyncClient(
                timeout=timeout(resolved.stream_read_timeout_seconds),
                limits=limits,
                follow_redirects=False,
                transport=transport,
            ) as stream_client,
        ):
            application.state.client = client
            application.state.stream_client = stream_client
            yield

    application = Starlette(
        routes=[
            Route("/", gateway, methods=_ROUTED_METHODS),
            Route("/{path:path}", gateway, methods=_ROUTED_METHODS),
        ],
        lifespan=lifespan,
    )
    application.state.settings = resolved
    return application


app = create_app()
