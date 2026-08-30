"""Canonical request identity and error envelope boundary for ``/api/*``."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = b"x-request-id"
_API_PREFIX = "/api/"
_UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
_ERROR_HEADER_ALLOWLIST = frozenset(
    {
        b"access-control-allow-credentials",
        b"access-control-allow-origin",
        b"access-control-expose-headers",
        b"allow",
        b"retry-after",
        b"set-cookie",
        b"vary",
        b"www-authenticate",
    }
)


class ErrorEnvelope(BaseModel):
    """Exact browser-facing failure contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["error@1"] = "error@1"
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=1024)
    request_id: str = Field(pattern=_UUID_PATTERN)


@dataclass(frozen=True)
class ErrorDefinition:
    code: str
    message: str


_ERRORS_BY_STATUS = {
    400: ErrorDefinition("bad_request", "The request could not be processed."),
    401: ErrorDefinition("unauthorized", "Authentication is required."),
    403: ErrorDefinition(
        "forbidden",
        "You do not have permission to perform this action.",
    ),
    404: ErrorDefinition("not_found", "The requested resource was not found."),
    405: ErrorDefinition(
        "method_not_allowed",
        "The request method is not supported.",
    ),
    409: ErrorDefinition(
        "conflict",
        "The request conflicts with the current resource state.",
    ),
    413: ErrorDefinition("payload_too_large", "The request body is too large."),
    415: ErrorDefinition(
        "unsupported_media_type",
        "The request content type is not supported.",
    ),
    422: ErrorDefinition(
        "validation_error",
        "The request does not match the required schema.",
    ),
    429: ErrorDefinition("rate_limited", "Too many requests. Try again later."),
    500: ErrorDefinition(
        "internal_error",
        "The server could not complete the request.",
    ),
    501: ErrorDefinition("not_implemented", "The operation is not implemented."),
    502: ErrorDefinition(
        "upstream_unavailable",
        "An upstream service is unavailable.",
    ),
    503: ErrorDefinition(
        "service_unavailable",
        "The service is temporarily unavailable.",
    ),
    504: ErrorDefinition("upstream_timeout", "An upstream service timed out."),
}
_REQUEST_FAILURE = ErrorDefinition(
    "request_failed",
    "The request could not be completed.",
)
_INTERNAL_FAILURE = _ERRORS_BY_STATUS[500]


def error_definition(status_code: int) -> ErrorDefinition:
    """Return one safe public definition without consulting exception detail."""
    defined = _ERRORS_BY_STATUS.get(status_code)
    if defined is not None:
        return defined
    return _REQUEST_FAILURE if 400 <= status_code < 500 else _INTERNAL_FAILURE


def _canonical_request_id(value: str) -> str | None:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    if parsed.int == 0 or str(parsed) != value:
        return None
    return value


def request_id_from_scope(scope: Scope) -> str:
    """Preserve exactly one canonical inbound UUID or create a fresh UUIDv4."""
    values = [
        value.decode("latin-1")
        for name, value in scope.get("headers", ())
        if name.lower() == _REQUEST_ID_HEADER_BYTES
    ]
    if len(values) == 1:
        preserved = _canonical_request_id(values[0])
        if preserved is not None:
            return preserved
    return str(uuid.uuid4())


def request_id_for_request(request: Request) -> str:
    """Return middleware state, remaining safe when a handler is used alone."""
    current = getattr(request.state, "request_id", None)
    if isinstance(current, str) and _canonical_request_id(current) is not None:
        return current
    generated = request_id_from_scope(request.scope)
    request.state.request_id = generated
    return generated


def _safe_error_headers(
    headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...],
) -> list[tuple[bytes, bytes]]:
    return [
        (name, value)
        for name, value in headers
        if name.lower() in _ERROR_HEADER_ALLOWLIST
    ]


def _replace_response_headers(
    headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...],
    *,
    request_id: str,
) -> list[tuple[bytes, bytes]]:
    replaced = [
        (name, value)
        for name, value in headers
        if name.lower() not in {_REQUEST_ID_HEADER_BYTES, b"cache-control"}
    ]
    replaced.extend(
        [
            (b"cache-control", b"no-store"),
            (_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")),
        ]
    )
    return replaced


def _error_body(status_code: int, request_id: str) -> bytes:
    definition = error_definition(status_code)
    envelope = ErrorEnvelope(
        code=definition.code,
        message=definition.message,
        request_id=request_id,
    )
    return json.dumps(
        envelope.model_dump(mode="json"),
        separators=(",", ":"),
    ).encode("utf-8")


def error_response(
    status_code: int,
    request_id: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the canonical response for registered exception handlers."""
    definition = error_definition(status_code)
    response_headers = {
        name: value
        for name, value in (headers or {}).items()
        if name.lower().encode("ascii", errors="ignore") in _ERROR_HEADER_ALLOWLIST
    }
    response_headers["Cache-Control"] = "no-store"
    response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(
            code=definition.code,
            message=definition.message,
            request_id=request_id,
        ).model_dump(mode="json"),
        headers=response_headers,
    )


async def http_exception_handler(
    request: Request,
    exception: StarletteHTTPException,
) -> JSONResponse:
    return error_response(
        exception.status_code,
        request_id_for_request(request),
        headers=exception.headers,
    )


async def validation_exception_handler(
    request: Request,
    _exception: RequestValidationError,
) -> JSONResponse:
    return error_response(422, request_id_for_request(request))


async def unhandled_exception_handler(
    request: Request,
    _exception: Exception,
) -> JSONResponse:
    request_id = request_id_for_request(request)
    logger.error("Unhandled Admin API error request_id=%s", request_id)
    return error_response(500, request_id)


class ConsoleErrorBoundaryMiddleware:
    """Assign request identity and replace every ``/api/*`` failure body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(
            _API_PREFIX
        ):
            await self.app(scope, receive, send)
            return

        request_id = request_id_from_scope(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        pending_error_status: int | None = None
        pending_error_headers: list[tuple[bytes, bytes]] = []
        downstream_started = False

        async def send_error(status_code: int) -> None:
            nonlocal downstream_started
            body = _error_body(status_code, request_id)
            headers = _safe_error_headers(pending_error_headers)
            headers.extend(
                [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": headers,
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )
            downstream_started = True

        async def send_boundary(message: Message) -> None:
            nonlocal pending_error_status, pending_error_headers
            nonlocal downstream_started
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", ()))
                if status_code >= 400:
                    pending_error_status = status_code
                    pending_error_headers = headers
                    return
                message["headers"] = _replace_response_headers(
                    headers,
                    request_id=request_id,
                )
                downstream_started = True
                await send(message)
                return
            if message["type"] == "http.response.body" and pending_error_status:
                if not message.get("more_body", False):
                    await send_error(pending_error_status)
                return
            await send(message)

        try:
            await self.app(scope, receive, send_boundary)
        except Exception:
            if downstream_started:
                raise
            logger.error("Unhandled Admin API error request_id=%s", request_id)
            await send_error(500)


def install_error_boundary(app: FastAPI) -> None:
    """Install one reusable exception and response boundary on an Admin app."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(ConsoleErrorBoundaryMiddleware)
