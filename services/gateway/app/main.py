"""ASGI edge exposing one direct APDL backend origin."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.client_identity import resolve_client_ip
from app.config import GatewaySettings
from app.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger(__name__)

_SUPPORTED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_ROUTED_METHODS = [*_SUPPORTED_METHODS, "CONNECT", "TRACE"]
_CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_METHODS = frozenset(_CORS_ALLOW_METHODS.split(", "))
_CORS_ALLOW_HEADERS = "Authorization, Content-Type, Last-Event-ID"
_CORS_HEADERS = frozenset(header.lower() for header in _CORS_ALLOW_HEADERS.split(", "))
_CORS_VARY = "Origin, Access-Control-Request-Method, Access-Control-Request-Headers"
_CONSOLE_CONFIG_STREAM_PATH = re.compile(
    r"^/api/projects/[^/]+/config/v1/stream$"
)
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


class ConsoleCORSMiddleware:
    """Own exact-origin CORS for /api/* without buffering streamed bodies."""

    def __init__(self, app: ASGIApp, allowed_origins: frozenset[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = _canonical_request_id(request)
        origin_values = request.headers.getlist("origin")
        cors_origin: str | None = None
        if origin_values:
            if len(origin_values) != 1 or origin_values[0] not in self.allowed_origins:
                response = _error_response(
                    status_code=403,
                    code="origin_not_allowed",
                    message="The browser Origin is not allowed by this APDL backend.",
                    request_id=request_id,
                )
                await response(scope, receive, _cors_send(send, None))
                return
            cors_origin = origin_values[0]

        cors_send = _cors_send(send, cors_origin)
        if cors_origin is not None and request.method not in _CORS_METHODS:
            response = _error_response(
                status_code=405,
                code="cors_method_not_allowed",
                message="The browser request method is not allowed by console CORS.",
                request_id=request_id,
            )
            await response(scope, receive, cors_send)
            return
        if request.method == "OPTIONS":
            error = _preflight_error(request)
            if cors_origin is None or error is not None:
                response = _error_response(
                    status_code=400,
                    code="invalid_cors_preflight",
                    message=error or "CORS preflight requires one allowed Origin.",
                    request_id=request_id,
                )
                await response(scope, receive, cors_send)
                return
            response = Response(
                status_code=204,
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, cors_send)
            return

        await self.app(scope, receive, cors_send)


def _cors_send(send: Send, origin: str | None) -> Send:
    async def send_with_cors(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != b"vary"
                and not name.lower().startswith(b"access-control-")
            ]
            headers.append((b"vary", _CORS_VARY.encode("ascii")))
            if origin is not None:
                headers.extend(
                    (
                        (b"access-control-allow-origin", origin.encode("ascii")),
                        (
                            b"access-control-allow-methods",
                            _CORS_ALLOW_METHODS.encode("ascii"),
                        ),
                        (
                            b"access-control-allow-headers",
                            _CORS_ALLOW_HEADERS.encode("ascii"),
                        ),
                        (b"access-control-expose-headers", b"X-Request-ID"),
                        (b"access-control-max-age", b"600"),
                    )
                )
            message = {**message, "headers": headers}
        await send(message)

    return send_with_cors


def _preflight_error(request: Request) -> str | None:
    method_values = request.headers.getlist("access-control-request-method")
    if len(method_values) != 1 or method_values[0] not in _CORS_METHODS:
        return "CORS preflight requested an unsupported method."

    header_values = request.headers.getlist("access-control-request-headers")
    if len(header_values) > 1:
        return "CORS preflight must use one requested-headers field."
    if not header_values:
        return None
    requested = [header.strip().lower() for header in header_values[0].split(",")]
    if (
        not requested
        or any(not header for header in requested)
        or len(requested) != len(set(requested))
        or any(header not in _CORS_HEADERS for header in requested)
    ):
        return "CORS preflight requested an unsupported header."
    return None


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
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Request-ID": request_id,
    }
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content={
            "schema_version": "error@1",
            "code": code,
            "message": message,
            "request_id": request_id,
        },
        headers=headers,
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
            long_read_timeout=(
                accepts_sse or _CONSOLE_CONFIG_STREAM_PATH.fullmatch(path) is not None
            ),
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
    client_ip: str | None,
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
    if client_ip is not None:
        headers.append(("X-Forwarded-For", client_ip))
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

    client_ip = resolve_client_ip(request, settings.trusted_proxy_cidrs)
    if route.name == "admin-api" and request.method != "OPTIONS":
        retry_after = await request.app.state.api_rate_limiter.retry_after(
            client_ip or "unknown"
        )
        if retry_after:
            return _error_response(
                status_code=429,
                code="rate_limited",
                message="Too many requests. Try again later.",
                request_id=request_id,
                retry_after_seconds=retry_after,
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
            client_ip=client_ip,
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
    rate_limit_clock: Callable[[], float] | None = None,
) -> Starlette:
    resolved = settings or GatewaySettings.from_env()
    api_rate_limiter = FixedWindowRateLimiter(
        request_limit=resolved.api_rate_limit,
        window_seconds=resolved.api_rate_window_seconds,
        max_clients=resolved.api_rate_max_clients,
        clock=rate_limit_clock,
    )
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
    application.state.api_rate_limiter = api_rate_limiter
    application.middleware_stack = ConsoleCORSMiddleware(
        application.build_middleware_stack(),
        resolved.console_allowed_origins,
    )
    return application


app = create_app()
