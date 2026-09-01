"""Authenticated, tenant-scoped proxy from the admin UI to APDL services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import uuid
from collections.abc import Mapping
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import Response, StreamingResponse

from app.auth import AdminSession, require_session
from app.config import PROJECT_ID_PATTERN, SERVICE_NAMES, Settings
from app.error_boundary import (
    REQUEST_ID_HEADER,
    error_definition,
    request_id_for_request,
)
from app.request_body_limit import RequestBodyTooLarge

router = APIRouter(tags=["service proxy"])
logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD"})
_JSON_MEDIA_TYPE = "application/json"
_FORWARDED_REQUEST_HEADERS = frozenset({"accept", "content-type", "if-none-match"})
_FORWARDED_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-type",
        "etag",
        "retry-after",
        "x-cache",
    }
)
_MAX_RETRY_AFTER_LENGTH = 128
_CONSOLE_CONFIG_STREAM = ("config", "GET", "/v1/stream")
_NORMALIZED_PROJECT_SCOPE_ALIASES = frozenset(
    {
        "project",
        "projectid",
        "xapdlproject",
        "xapdlprojectid",
        "xproject",
        "xprojectid",
    }
)
_MAX_LAST_EVENT_ID_LENGTH = 256

_UPSTREAM_PROJECT_BODY_ROUTES = frozenset(
    {
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
    }
)
_UPSTREAM_PROJECT_BODY_PATTERNS = (
    (
        "llm-vault",
        "PUT",
        re.compile(r"^/v1/llm-connections/[0-9a-fA-F-]{36}$"),
    ),
    (
        "llm-vault",
        "POST",
        re.compile(r"^/v1/llm-connections/[0-9a-fA-F-]{36}/(?:refresh|revoke)$"),
    ),
)

_EPHEMERAL_CREDENTIAL_TTL_SECONDS = 300
_LLM_CONNECTION_READER = "llm-connections:read"
_LLM_CONNECTION_MANAGER = "llm-connections:manage"
_SERVICE_CREDENTIAL_ROLES = frozenset(
    {
        "events:write",
        "config:read",
        "config:write",
        "config:evaluate",
        "query:read",
        "agents:read",
        "agents:run",
        "agents:manage",
        "agents:approve",
    }
)
StreamAuthorityState = Literal[
    "authorized",
    "session_expired",
    "project_access_revoked",
]


def _safe_retry_after(response: httpx.Response) -> dict[str, str] | None:
    value = response.headers.get("retry-after")
    if (
        response.status_code not in {
            status.HTTP_429_TOO_MANY_REQUESTS,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        }
        or value is None
        or not value
        or len(value) > _MAX_RETRY_AFTER_LENGTH
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return None
    return {"Retry-After": value}


def _upstream_response_exception(response: httpx.Response) -> HTTPException:
    upstream_status = response.status_code
    if upstream_status in {status.HTTP_408_REQUEST_TIMEOUT, status.HTTP_504_GATEWAY_TIMEOUT}:
        public_status = status.HTTP_504_GATEWAY_TIMEOUT
    elif upstream_status >= 500 or upstream_status in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }:
        public_status = status.HTTP_502_BAD_GATEWAY
    elif 400 <= upstream_status < 500:
        public_status = upstream_status
    else:
        public_status = status.HTTP_502_BAD_GATEWAY
    return HTTPException(
        status_code=public_status,
        detail=error_definition(public_status).message,
        headers=_safe_retry_after(response),
    )


def _upstream_transport_exception(exception: httpx.RequestError) -> HTTPException:
    public_status = (
        status.HTTP_504_GATEWAY_TIMEOUT
        if isinstance(exception, httpx.TimeoutException)
        else status.HTTP_502_BAD_GATEWAY
    )
    return HTTPException(
        status_code=public_status,
        detail=error_definition(public_status).message,
    )


async def _service_credential(
    request: Request,
    project_id: str,
    roles: frozenset[str],
    settings: Settings,
    *,
    actor_user_id: str | None = None,
    force_ephemeral: bool = False,
) -> tuple[str, str | None]:
    configured = settings.service_api_keys.get(project_id)
    if configured is not None and not force_ephemeral:
        return configured, None

    service_roles = sorted(roles & _SERVICE_CREDENTIAL_ROLES)
    if not service_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No service roles available",
        )

    raw_key = f"proj_{project_id}_{secrets.token_hex(24)}"
    credential_id = f"adminproxy-{uuid.uuid4().hex}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    async with request.app.state.pg_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                DELETE FROM auth_credentials
                WHERE credential_id LIKE 'adminproxy-%'
                  AND expires_at <= NOW()
                """
            )
            await conn.execute(
                """
                INSERT INTO auth_credentials (
                    credential_id, project_id, credential_kind, key_prefix,
                    key_hash, roles, actor_user_id, expires_at
                ) VALUES (
                    $1, $2, 'confidential', $3, $4, $5, $6,
                    NOW() + ($7 * INTERVAL '1 second')
                )
                """,
                credential_id,
                project_id,
                f"proj_{project_id}_",
                digest,
                service_roles,
                uuid.UUID(actor_user_id) if actor_user_id is not None else None,
                _EPHEMERAL_CREDENTIAL_TTL_SECONDS,
            )
    return raw_key, credential_id


async def _remove_ephemeral_credential(
    request: Request, credential_id: str | None
) -> None:
    if credential_id is None:
        return
    try:
        async with request.app.state.pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM auth_credentials WHERE credential_id = $1",
                credential_id,
            )
    except Exception:
        logger.exception("Failed to remove ephemeral credential %s", credential_id)


# Match every route rooted at one changeset, including future child resources.
# Tenant authorization must not depend on maintaining an action allowlist here.
_CODEGEN_CHANGESET_PATH = re.compile(r"^/v1/changesets/([^/]+)(?:/[^/]+)*$")
_AGENT_TOOL_RESULT_ARTIFACT_PATH = re.compile(
    r"^/v1/agents/[^/]+/tool-result-artifacts/[^/]+$"
)


async def _start_mutation_audit(
    request: Request,
    session: AdminSession,
    project_id: str,
    role: str,
    service: str,
    path: str,
) -> uuid.UUID | None:
    if request.method in _SAFE_METHODS:
        return None
    audit_id = uuid.uuid4()
    async with request.app.state.pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admin_proxy_audit (
                audit_id, user_id, actor_email, project_id,
                required_role, service, method, path
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            audit_id,
            uuid.UUID(session.user_id),
            session.email,
            project_id,
            role,
            service,
            request.method,
            path,
        )
    return audit_id


async def _finish_mutation_audit(
    request: Request, audit_id: uuid.UUID | None, status_code: int
) -> None:
    if audit_id is None:
        return
    try:
        async with request.app.state.pg_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE admin_proxy_audit
                SET status_code = $2, completed_at = NOW()
                WHERE audit_id = $1
                """,
                audit_id,
                status_code,
            )
    except Exception:
        # The immutable attempt row already exists. Do not make a completed
        # upstream mutation look retryable merely because its status update
        # could not be written.
        logger.exception("Failed to complete admin proxy audit %s", audit_id)


async def _stream_authority_state(
    request: Request,
    session: AdminSession,
    settings: Settings,
    project_id: str,
    required_role: str | None,
) -> StreamAuthorityState:
    async with request.app.state.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                EXISTS (
                    SELECT 1
                    FROM admin_sessions AS s
                    JOIN admin_users AS u ON u.user_id = s.user_id
                    WHERE s.session_id = $1
                      AND s.user_id = $2
                      AND s.deployment_id = $3
                      AND s.revoked_at IS NULL
                      AND s.expires_at > NOW()
                      AND u.active
                ) AS session_active,
                EXISTS (
                    SELECT 1
                    FROM admin_user_projects AS membership
                    WHERE membership.user_id = $2
                      AND membership.project_id = $4
                      AND (
                          $5::TEXT IS NULL
                          OR $5::TEXT = ANY(membership.roles)
                      )
                ) AS project_authorized
            """,
            uuid.UUID(session.session_id),
            uuid.UUID(session.user_id),
            uuid.UUID(str(settings.deployment_id)),
            project_id,
            required_role,
        )
    if not bool(row["session_active"]):
        return "session_expired"
    if not bool(row["project_authorized"]):
        return "project_access_revoked"
    return "authorized"


def _stream_terminal_event(
    state: StreamAuthorityState | Literal["authorization_unavailable"],
    project_id: str,
    required_role: str,
) -> bytes:
    messages = {
        "session_expired": "The console session expired.",
        "project_access_revoked": "Project access was revoked.",
        "authorization_unavailable": "Stream authorization is temporarily unavailable.",
    }
    data = json.dumps(
        {
            "schema_version": "console_stream_control@1",
            "code": state,
            "message": messages[state],
            "project_id": project_id,
            "required_role": required_role,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return b"event: console_stream_control\ndata: " + data + b"\n\n"


async def _authorized_sse(
    response,
    request: Request,
    session: AdminSession,
    settings: Settings,
    project_id: str,
    required_role: str | None,
    credential_id: str | None,
):
    chunk_task: asyncio.Task | None = None
    authority_timer: asyncio.Task | None = None
    iterator = None
    try:
        try:
            authority = await _stream_authority_state(
                request,
                session,
                settings,
                project_id,
                required_role,
            )
        except Exception:
            logger.exception(
                "Failed to revalidate stream authority for user %s project %s",
                session.user_id,
                project_id,
            )
            if required_role is not None:
                yield _stream_terminal_event(
                    "authorization_unavailable", project_id, required_role
                )
            return
        if authority != "authorized":
            if required_role is not None:
                yield _stream_terminal_event(authority, project_id, required_role)
            return

        iterator = response.aiter_raw()
        chunk_task = asyncio.create_task(anext(iterator))
        authority_timer = asyncio.create_task(
            asyncio.sleep(settings.stream_authority_check_seconds)
        )
        while True:
            done, _ = await asyncio.wait(
                {chunk_task, authority_timer},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Authority wins a same-tick race with buffered upstream data.
            if authority_timer in done:
                try:
                    authority = await _stream_authority_state(
                        request,
                        session,
                        settings,
                        project_id,
                        required_role,
                    )
                except Exception:
                    logger.exception(
                        "Failed to revalidate stream authority for user %s project %s",
                        session.user_id,
                        project_id,
                    )
                    if required_role is not None:
                        yield _stream_terminal_event(
                            "authorization_unavailable", project_id, required_role
                        )
                    return
                if authority != "authorized":
                    if required_role is not None:
                        yield _stream_terminal_event(
                            authority, project_id, required_role
                        )
                    return
                authority_timer = asyncio.create_task(
                    asyncio.sleep(settings.stream_authority_check_seconds)
                )

            if chunk_task in done:
                try:
                    chunk = chunk_task.result()
                except StopAsyncIteration:
                    return
                yield chunk
                chunk_task = asyncio.create_task(anext(iterator))
    finally:
        pending = [
            task
            for task in (chunk_task, authority_timer)
            if task is not None and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            if iterator is not None and hasattr(iterator, "aclose"):
                await iterator.aclose()
        finally:
            await response.aclose()
            await _remove_ephemeral_credential(request, credential_id)


def required_role(service: str, method: str, path: str) -> str | None:
    if method == "GET" and path in {"/health", "/ready"}:
        return None
    if service == "ingestion":
        return "events:write" if method == "POST" and path == "/v1/events" else ""
    if service == "config":
        if method == "GET" and path in {"/v1/flags", "/v1/stream"}:
            return "config:read"
        if method == "POST" and path == "/v1/evaluate":
            return "config:evaluate"
        if path == "/v1/admin" or path.startswith("/v1/admin/"):
            return "config:write"
        return ""
    if service == "query":
        return "query:read" if path.startswith("/v1/query/") else ""
    if service == "llm-vault":
        connection = r"/v1/llm-connections/[0-9a-fA-F-]{36}"
        if method == "GET" and (
            path == "/v1/llm-connections"
            or re.fullmatch(connection, path) is not None
        ):
            return _LLM_CONNECTION_READER
        if method == "POST" and path == "/v1/llm-connections":
            return _LLM_CONNECTION_MANAGER
        if method == "PUT" and re.fullmatch(connection, path) is not None:
            return _LLM_CONNECTION_MANAGER
        if method == "POST" and re.fullmatch(
            connection + r"/(?:refresh|revoke)", path
        ) is not None:
            return _LLM_CONNECTION_MANAGER
        return ""
    if service == "agents":
        if not path.startswith("/v1/agents"):
            return ""
        if path == "/v1/agents/setup":
            if method == "GET":
                return "agents:read"
            if method == "PUT":
                return _LLM_CONNECTION_MANAGER
            return ""
        if path == "/v1/agents/setup/deactivate":
            return (
                _LLM_CONNECTION_MANAGER
                if method == "POST"
                else ""
            )
        if path.startswith("/v1/agents/setup"):
            return ""
        if path.startswith("/v1/agents/llm-connections"):
            provider_path = (
                r"/v1/agents/llm-connections/"
                r"(?:openai|anthropic|google|xai)"
            )
            if method == "GET" and (
                path == "/v1/agents/llm-connections"
                or re.fullmatch(provider_path + r"/models", path) is not None
            ):
                return _LLM_CONNECTION_READER
            return ""
        if method == "GET" and path == "/v1/agents/capabilities/execution":
            return "agents:run"
        if method == "GET":
            return "agents:read"
        if method == "POST" and path == "/v1/agents/trigger":
            return "agents:run"
        if method == "POST" and path.endswith("/approve"):
            return "agents:approve"
        if (
            method == "POST" and path in {"/v1/agents/custom", "/v1/agents/custom/test"}
        ) or (method in {"PUT", "DELETE"} and path.startswith("/v1/agents/custom/")):
            return "agents:manage"
        return ""
    if service == "codegen":
        if path.startswith("/v1/llm-connections"):
            provider_path = (
                r"/v1/llm-connections/"
                r"(?:openai|anthropic|google|xai)"
            )
            if method == "GET" and (
                path == "/v1/llm-connections"
                or re.fullmatch(provider_path + r"/models", path) is not None
            ):
                return "agents:read"
            return ""
        if method == "GET" and (
            path.startswith("/v1/changesets") or path.startswith("/v1/connections/")
        ):
            return "agents:read"
        if method == "POST" and path.endswith("/merge"):
            return "agents:approve"
        if (method == "POST" and path == "/v1/changesets") or (
            method == "POST" and re.search(r"/(?:abandon|revert|retry)$", path)
        ):
            return "agents:manage"
        return ""
    return ""


async def _has_llm_connection_authority(
    request: Request,
    project_id: str,
    user_id: str,
) -> bool:
    async with request.app.state.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT (
                account.active
                AND (
                    project.owner_user_id = $2
                    OR (
                        'agents:manage' = ANY(
                            COALESCE(membership.roles, ARRAY[]::TEXT[])
                        )
                        AND 'credentials:manage' = ANY(
                            COALESCE(membership.roles, ARRAY[]::TEXT[])
                        )
                    )
                )
            ) AS llm_connection_authorized
            FROM admin_projects AS project
            JOIN admin_users AS account ON account.user_id = $2
            LEFT JOIN admin_user_projects AS membership
              ON membership.project_id = project.project_id
             AND membership.user_id = account.user_id
            WHERE project.project_id = $1
            """,
            project_id,
            uuid.UUID(user_id),
        )
    return row is not None and bool(row["llm_connection_authorized"])


def _assert_tenant_value(value: object, project_id: str) -> None:
    if value is not None and str(value) != project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Project mismatch"
        )


def _is_project_scope_alias(name: str) -> bool:
    normalized = name.lower().replace("-", "").replace("_", "")
    return normalized in _NORMALIZED_PROJECT_SCOPE_ALIASES


def _requires_upstream_project_query(
    service: str,
    method: str,
    path: str,
) -> bool:
    if service == "agents":
        if path in {
            "/v1/agents/capabilities/execution",
            "/v1/agents/runs",
            "/v1/agents/definitions",
        }:
            return method == "GET"
        if path == "/v1/agents/setup":
            return method == "GET"
        if path == "/v1/agents/llm-connections":
            return method == "GET"
        if path.startswith("/v1/agents/llm-connections/") and path.endswith(
            "/models"
        ):
            return method == "GET"
        if path == "/v1/agents/custom" or path.startswith(
            "/v1/agents/custom/"
        ):
            return method in {"GET", "POST", "PUT", "DELETE"}
    if service == "codegen":
        if path == "/v1/changesets":
            return method == "GET"
        if path == "/v1/llm-connections":
            return method == "GET"
        if path.startswith("/v1/llm-connections/") and path.endswith("/models"):
            return method == "GET"
    if service == "llm-vault":
        return method == "GET" and (
            path == "/v1/llm-connections"
            or re.fullmatch(r"/v1/llm-connections/[0-9a-fA-F-]{36}", path)
            is not None
        )
    return False


def _requires_upstream_project_body(
    service: str,
    method: str,
    path: str,
) -> bool:
    route = (service, method, path)
    if route in _UPSTREAM_PROJECT_BODY_ROUTES:
        return True
    return any(
        service == registered_service
        and method == registered_method
        and pattern.fullmatch(path) is not None
        for registered_service, registered_method, pattern in (
            _UPSTREAM_PROJECT_BODY_PATTERNS
        )
    )


def _upstream_query_items(
    request: Request,
    service: str,
    path: str,
    project_id: str,
) -> list[tuple[str, str]]:
    for name, _value in request.query_params.multi_items():
        if _is_project_scope_alias(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project scope belongs only in the API path",
            )
    items = list(request.query_params.multi_items())
    if _requires_upstream_project_query(service, request.method, path):
        items.append(("project_id", project_id))
    return items


def _canonical_last_event_id(request: Request) -> str | None:
    values = request.headers.getlist("last-event-id")
    if not values:
        return None
    value = values[0]
    if (
        len(values) != 1
        or not value
        or len(value) > _MAX_LAST_EVENT_ID_LENGTH
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Last-Event-ID",
        )
    return value


def _require_path_only_stream_scope(request: Request) -> str | None:
    for name, _value in request.query_params.multi_items():
        if _is_project_scope_alias(name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Stream project scope must come from the path",
            )
    if request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The console stream does not accept query parameters",
        )
    if any(_is_project_scope_alias(name) for name in request.headers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stream project scope must come from the path",
        )
    return _canonical_last_event_id(request)


async def _request_body(
    request: Request,
    settings: Settings,
    project_id: str,
    *,
    inject_project: bool = False,
    require_json: bool = False,
) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > settings.max_request_bytes:
                raise RequestBodyTooLarge
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid content length"
            ) from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > settings.max_request_bytes:
            raise RequestBodyTooLarge
        body.extend(chunk)
    raw_body = bytes(body)
    media_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if (raw_body or inject_project) and require_json and media_type != _JSON_MEDIA_TYPE:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upstream request bodies must use application/json",
        )
    if raw_body and media_type == _JSON_MEDIA_TYPE:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            if inject_project:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upstream request body must be a JSON object",
                ) from exc
            return raw_body
        if isinstance(payload, Mapping):
            if any(_is_project_scope_alias(str(name)) for name in payload):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Project scope belongs only in the API path",
                )
            if inject_project:
                canonical_payload = {**payload, "project_id": project_id}
                encoded = json.dumps(
                    canonical_payload,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(encoded) > settings.max_request_bytes:
                    raise RequestBodyTooLarge
                return encoded
        elif inject_project:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upstream request body must be a JSON object",
            )
    elif inject_project:
        encoded = json.dumps(
            {"project_id": project_id},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > settings.max_request_bytes:
            raise RequestBodyTooLarge
        return encoded
    return raw_body


async def _require_codegen_scope(
    request: Request,
    project_id: str,
    path: str,
    api_key: str,
    settings: Settings,
) -> None:
    connection_prefix = "/v1/connections/"
    if path.startswith(connection_prefix):
        _assert_tenant_value(
            path[len(connection_prefix) :].split("/", 1)[0], project_id
        )
        return
    match = _CODEGEN_CHANGESET_PATH.fullmatch(path)
    if match is None:
        return
    try:
        response = await request.app.state.http_client.get(
            f"{settings.service_urls['codegen'].rstrip('/')}/v1/changesets/{match.group(1)}",
            headers={
                "X-API-Key": api_key,
                REQUEST_ID_HEADER: request_id_for_request(request),
            },
        )
    except httpx.RequestError as exc:
        raise _upstream_transport_exception(exc) from exc
    if response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Changeset not found"
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to authorize changeset",
        )
    try:
        changeset_project = response.json()["project_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid codegen response"
        ) from exc
    if changeset_project != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Changeset not found"
        )


@router.api_route(
    "/api/projects/{project_id}/{service}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE"],
)
async def proxy_service(
    project_id: str,
    service: str,
    path: str,
    request: Request,
    session: AdminSession = Depends(require_session),
):
    settings: Settings = request.app.state.settings
    if PROJECT_ID_PATTERN.fullmatch(project_id) is None or service not in SERVICE_NAMES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    upstream_path = f"/{path}"
    is_console_config_stream = (
        service,
        request.method,
        upstream_path,
    ) == _CONSOLE_CONFIG_STREAM
    roles = session.projects.get(project_id)
    if roles is None:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
                if is_console_config_stream
                else status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Project access denied"
                if is_console_config_stream
                else "Project not found"
            ),
        )

    role = required_role(service, request.method, upstream_path)
    if role == "":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Route not available"
        )
    is_tool_result_artifact_read = (
        service == "agents"
        and request.method == "GET"
        and _AGENT_TOOL_RESULT_ARTIFACT_PATH.fullmatch(upstream_path) is not None
    )
    if is_tool_result_artifact_read and not {
        "agents:read",
        "query:read",
    }.issubset(roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tool result previews require agents:read and query:read",
        )
    elevated_llm_connection_read = False
    if role in {_LLM_CONNECTION_READER, _LLM_CONNECTION_MANAGER}:
        if role == _LLM_CONNECTION_READER and "agents:read" in roles:
            pass
        else:
            has_connection_authority = await _has_llm_connection_authority(
                request,
                project_id,
                session.user_id,
            )
            if not has_connection_authority:
                detail = (
                    "Connection management requires project ownership or "
                    "delegated agents:manage and credentials:manage roles"
                    if role == _LLM_CONNECTION_MANAGER
                    else (
                        "LLM connection access requires agents:read, project ownership, "
                        "or delegated agents:manage and credentials:manage roles"
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=detail,
                )
            elevated_llm_connection_read = role == _LLM_CONNECTION_READER
    elif role is not None and role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role"
        )

    if "api_key" in request.query_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Credentials are not accepted from the browser",
        )
    last_event_id = (
        _require_path_only_stream_scope(request)
        if is_console_config_stream
        else None
    )
    if is_console_config_stream:
        upstream_query: list[tuple[str, str]] = []
    else:
        if any(_is_project_scope_alias(name) for name in request.headers):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project scope belongs only in the API path",
            )
        upstream_query = _upstream_query_items(
            request,
            service,
            upstream_path,
            project_id,
        )
    inject_project = _requires_upstream_project_body(
        service,
        request.method,
        upstream_path,
    )
    body = await _request_body(
        request,
        settings,
        project_id,
        inject_project=inject_project,
        require_json=inject_project or service in {"codegen", "llm-vault"},
    )

    ephemeral_credential_id: str | None = None
    require_human_actor = (
        service == "agents"
        and (
            request.method not in _SAFE_METHODS
            or upstream_path == "/v1/agents/setup"
        )
    ) or role == _LLM_CONNECTION_MANAGER or is_tool_result_artifact_read
    credential_roles = roles
    if role == _LLM_CONNECTION_MANAGER or elevated_llm_connection_read:
        # Grant only the upstream read capability needed by this management
        # surface. The receiving service rechecks live authority inside
        # mutation transactions.
        credential_roles = frozenset({"agents:read"})
    elif is_tool_result_artifact_read:
        credential_roles = frozenset({"agents:read", "query:read"})
    if service == "llm-vault":
        api_key = settings.llm_vault_admin_token
    else:
        api_key, ephemeral_credential_id = await _service_credential(
            request,
            project_id,
            credential_roles,
            settings,
            actor_user_id=(
                session.user_id
                if require_human_actor or elevated_llm_connection_read
                else None
            ),
            force_ephemeral=require_human_actor or elevated_llm_connection_read,
        )
    try:
        if service == "codegen":
            await _require_codegen_scope(
                request,
                project_id,
                upstream_path,
                api_key,
                settings,
            )
    except Exception:
        await _remove_ephemeral_credential(request, ephemeral_credential_id)
        raise

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_REQUEST_HEADERS
    }
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    if service == "llm-vault":
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-APDL-Project-ID"] = project_id
        headers["X-APDL-Actor-User-ID"] = session.user_id
    else:
        headers["X-API-Key"] = api_key
    headers[REQUEST_ID_HEADER] = request_id_for_request(request)

    upstream_url = f"{settings.service_urls[service].rstrip('/')}{upstream_path}"
    upstream_request = request.app.state.http_client.build_request(
        request.method,
        upstream_url,
        params=upstream_query,
        headers=headers,
        content=body,
    )
    try:
        audit_id = await _start_mutation_audit(
            request,
            session,
            project_id,
            role or "authenticated",
            service,
            upstream_path,
        )
    except Exception:
        await _remove_ephemeral_credential(request, ephemeral_credential_id)
        raise
    try:
        response = await request.app.state.http_client.send(
            upstream_request, stream=True
        )
    except httpx.RequestError as exc:
        failure = _upstream_transport_exception(exc)
        await _remove_ephemeral_credential(request, ephemeral_credential_id)
        await _finish_mutation_audit(request, audit_id, failure.status_code)
        raise failure from exc

    await _finish_mutation_audit(request, audit_id, response.status_code)

    response_headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() in _FORWARDED_RESPONSE_HEADERS
    }
    is_event_stream = response.headers.get("content-type", "").startswith(
        "text/event-stream"
    )
    if is_console_config_stream and (
        response.status_code != status.HTTP_200_OK or not is_event_stream
    ):
        await response.aclose()
        await _remove_ephemeral_credential(request, ephemeral_credential_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid upstream stream response",
        )
    if is_event_stream:
        if response.status_code >= 400:
            failure = _upstream_response_exception(response)
            await response.aclose()
            await _remove_ephemeral_credential(request, ephemeral_credential_id)
            raise failure
        if is_console_config_stream:
            response_headers.update(
                {
                    "cache-control": "no-cache, no-transform",
                    "content-type": "text/event-stream",
                    "x-accel-buffering": "no",
                }
            )
        return StreamingResponse(
            _authorized_sse(
                response,
                request,
                session,
                settings,
                project_id,
                role,
                ephemeral_credential_id,
            ),
            status_code=response.status_code,
            headers=response_headers,
        )
    try:
        content = await response.aread()
    except httpx.RequestError as exc:
        failure = _upstream_transport_exception(exc)
        await _finish_mutation_audit(request, audit_id, failure.status_code)
        raise failure from exc
    finally:
        await response.aclose()
        await _remove_ephemeral_credential(request, ephemeral_credential_id)
    if response.status_code >= 400:
        raise _upstream_response_exception(response)
    return Response(
        content=content, status_code=response.status_code, headers=response_headers
    )
