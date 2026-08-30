"""Deployment-bound opaque bearer sessions for the direct console."""

from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import Settings
from app.login_security import (
    GENERIC_LOGIN_ERROR,
    THROTTLED_LOGIN_ERROR,
    build_login_source,
    clear_login_source_risk,
    preflight_auth_rate_limit,
    preflight_login,
    record_failed_login,
)
from app.models import (
    ConsoleIdentity,
    ConsoleSession,
    LoginRequest,
    ProjectAccess,
    RegistrationRequest,
    canonical_human_roles,
    SecurityNotification,
)
from app.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    new_token,
    token_hash,
    verify_password,
)

router = APIRouter(tags=["authentication"])
BEARER_PATTERN = re.compile(r"^Bearer ([A-Za-z0-9_-]{43,128})$")
ACCOUNT_REGISTRATION_LOCK_ID = 4_704_656_378_673_808_212


@dataclass(frozen=True)
class AdminSession:
    session_id: str
    token_hash: str
    deployment_id: str
    expires_at: datetime
    user_id: str
    email: str
    projects: dict[str, frozenset[str]]

    def identity(self) -> ConsoleIdentity:
        return ConsoleIdentity(
            user_id=self.user_id,
            email=self.email,
            projects=[
                ProjectAccess(
                    project_id=project_id,
                    roles=canonical_human_roles(roles),
                )
                for project_id, roles in sorted(self.projects.items())
            ],
        )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def _project_access(conn, user_id: str) -> dict[str, frozenset[str]]:
    rows = await conn.fetch(
        """
        SELECT project_id, roles
        FROM admin_user_projects
        WHERE user_id = $1
        ORDER BY project_id
        """,
        uuid.UUID(user_id),
    )
    return {
        str(row["project_id"]): frozenset(str(role) for role in row["roles"])
        for row in rows
    }


async def _start_session(
    conn,
    user_id: uuid.UUID,
    settings: Settings,
    now: datetime,
) -> tuple[str, datetime]:
    await conn.execute(
        """
        DELETE FROM admin_sessions
        WHERE expires_at <= NOW()
           OR revoked_at IS NOT NULL
        """
    )
    session_token = new_token()
    expires_at = now + timedelta(seconds=settings.session_ttl_seconds)
    await conn.execute(
        """
        INSERT INTO admin_sessions (
            session_id, user_id, token_hash, deployment_id, expires_at
        ) VALUES ($1, $2, $3, $4, $5)
        """,
        uuid.uuid4(),
        user_id,
        token_hash(session_token),
        uuid.UUID(str(settings.deployment_id)),
        expires_at,
    )
    return session_token, expires_at


def _bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login required",
        )
    match = BEARER_PATTERN.fullmatch(values[0])
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer session",
        )
    return match.group(1)


async def require_session(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AdminSession:
    digest = token_hash(_bearer_token(request))
    async with request.app.state.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                s.session_id,
                s.token_hash,
                s.deployment_id,
                s.expires_at,
                s.user_id,
                u.email
            FROM admin_sessions AS s
            JOIN admin_users AS u ON u.user_id = s.user_id
            WHERE s.token_hash = $1
              AND s.deployment_id = $2
              AND s.revoked_at IS NULL
              AND s.expires_at > NOW()
              AND u.active
            """,
            digest,
            uuid.UUID(str(settings.deployment_id)),
        )
        if row is None or not secrets.compare_digest(digest, str(row["token_hash"])):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
            )
        projects = await _project_access(conn, str(row["user_id"]))

    return AdminSession(
        session_id=str(row["session_id"]),
        token_hash=str(row["token_hash"]),
        deployment_id=str(row["deployment_id"]),
        expires_at=row["expires_at"],
        user_id=str(row["user_id"]),
        email=str(row["email"]),
        projects=projects,
    )


def _failed_login_response(
    retry_after_seconds: int,
) -> None:
    if retry_after_seconds > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=THROTTLED_LOGIN_ERROR,
            headers={"Retry-After": str(retry_after_seconds)},
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=GENERIC_LOGIN_ERROR,
    )


@router.post(
    "/api/console/v1/sessions",
    response_model=ConsoleSession,
)
async def create_session(
    body: LoginRequest,
    request: Request,
) -> ConsoleSession:
    settings: Settings = request.app.state.settings
    email = str(body.email).strip().lower()
    now = datetime.now(timezone.utc)
    source = build_login_source(request, email, settings)

    async with request.app.state.pg_pool.acquire() as conn:
        async with conn.transaction():
            retry_after = await preflight_login(conn, source, settings, now)
            candidate = await conn.fetchrow(
                """
                SELECT user_id, email, password_hash, active
                FROM admin_users
                WHERE email = $1
                """,
                email,
            )
    if retry_after > 0:
        _failed_login_response(retry_after)

    candidate_hash = (
        str(candidate["password_hash"])
        if candidate is not None
        else DUMMY_PASSWORD_HASH
    )
    password_valid = await asyncio.to_thread(
        verify_password,
        candidate_hash,
        body.password,
    )
    login_result: tuple[str, datetime] | None = None
    failure_delay = 0

    async with request.app.state.pg_pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                """
                SELECT user_id, email, password_hash, active
                FROM admin_users
                WHERE email = $1
                FOR UPDATE
                """,
                email,
            )
            candidate_is_current = bool(
                candidate is not None
                and user is not None
                and secrets.compare_digest(
                    str(candidate["password_hash"]),
                    str(user["password_hash"]),
                )
            )
            valid = bool(
                user is not None
                and user["active"]
                and candidate_is_current
                and password_valid
            )
            if valid:
                await clear_login_source_risk(conn, source)
                login_result = await _start_session(
                    conn,
                    user["user_id"],
                    settings,
                    now,
                )
            else:
                failure_delay = await record_failed_login(
                    conn,
                    source=source,
                    user_id=(
                        user["user_id"]
                        if user is not None and user["active"]
                        else None
                    ),
                    settings=settings,
                    now=now,
                )

    if login_result is None:
        _failed_login_response(failure_delay)

    access_token, expires_at = login_result
    return ConsoleSession(access_token=access_token, expires_at=expires_at)


@router.post(
    "/api/console/v1/registrations",
    response_model=ConsoleSession,
    status_code=status.HTTP_201_CREATED,
)
async def create_registration(
    body: RegistrationRequest,
    request: Request,
) -> ConsoleSession:
    """Create one zero-project account and its first bearer session."""
    settings: Settings = request.app.state.settings
    if not settings.registration_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled",
        )

    email = str(body.email).strip().lower()
    now = datetime.now(timezone.utc)
    source = build_login_source(request, email, settings)

    async with request.app.state.pg_pool.acquire() as conn:
        async with conn.transaction():
            retry_after = await preflight_auth_rate_limit(
                conn,
                source,
                settings,
                now,
            )
            account_count = int(
                await conn.fetchval("SELECT count(*) FROM admin_users")
            )

    if retry_after > 0:
        _failed_login_response(retry_after)
    if account_count >= settings.max_accounts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account capacity reached",
        )

    password_hash = await asyncio.to_thread(hash_password, body.password)
    async with request.app.state.pg_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                ACCOUNT_REGISTRATION_LOCK_ID,
            )
            locked_account_count = int(
                await conn.fetchval("SELECT count(*) FROM admin_users")
            )
            if locked_account_count >= settings.max_accounts:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Account capacity reached",
                )
            user_id = await conn.fetchval(
                """
                INSERT INTO admin_users (user_id, email, password_hash)
                VALUES ($1, $2, $3)
                ON CONFLICT (email) DO NOTHING
                RETURNING user_id
                """,
                uuid.uuid4(),
                email,
                password_hash,
            )
            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Account already exists",
                )
            access_token, expires_at = await _start_session(
                conn,
                user_id,
                settings,
                now,
            )

    return ConsoleSession(access_token=access_token, expires_at=expires_at)


@router.get(
    "/api/console/v1/session",
    response_model=ConsoleIdentity,
)
async def get_session(
    session: AdminSession = Depends(require_session),
) -> ConsoleIdentity:
    return session.identity()


@router.delete(
    "/api/console/v1/session",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session(
    request: Request,
    session: AdminSession = Depends(require_session),
) -> Response:
    async with request.app.state.pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE admin_sessions SET revoked_at = NOW() WHERE session_id = $1",
            uuid.UUID(session.session_id),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/auth/security-notifications",
    response_model=list[SecurityNotification],
)
async def list_security_notifications(
    request: Request,
    session: AdminSession = Depends(require_session),
) -> list[SecurityNotification]:
    async with request.app.state.pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                notification_id,
                kind,
                status,
                observed_failures,
                window_started_at,
                last_detected_at,
                created_at
            FROM admin_security_notifications
            WHERE user_id = $1
              AND status = 'unread'
            ORDER BY created_at DESC, notification_id DESC
            """,
            uuid.UUID(session.user_id),
        )
    return [SecurityNotification(**dict(row)) for row in rows]


@router.post(
    "/api/auth/security-notifications/{notification_id}/acknowledge",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def acknowledge_security_notification(
    notification_id: uuid.UUID,
    request: Request,
    session: AdminSession = Depends(require_session),
) -> Response:
    async with request.app.state.pg_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE admin_security_notifications
            SET status = 'acknowledged',
                acknowledged_at = NOW()
            WHERE notification_id = $1
              AND user_id = $2
              AND status = 'unread'
            """,
            notification_id,
            uuid.UUID(session.user_id),
        )
    if result != "UPDATE 1":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Security notification not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
