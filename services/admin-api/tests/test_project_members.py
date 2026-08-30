from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app import members
from app.auth import AdminSession, require_session
from app.models import PendingProjectInvitation
from app.security import token_hash
from conftest import make_settings

ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
TARGET_ID = UUID("30000000-0000-4000-8000-000000000003")
INVITATION_ID = UUID("40000000-0000-4000-8000-000000000004")
NEWEST_AUDIT_ID = UUID("70000000-0000-4000-8000-000000000007")
OLDER_AUDIT_ID = UUID("60000000-0000-4000-8000-000000000006")
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
RAW_TOKEN = "a" * 43


class MemberConnection:
    def __init__(
        self,
        *,
        actor_is_owner: bool = True,
        actor_roles: list[str] | None = None,
        invitation_available: bool = True,
        account_email: str = "invitee@example.com",
        target_roles: list[str] | None = None,
        target_is_owner: bool = False,
        pending_blocked_reason: str | None = None,
        membership_audit_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.actor_is_owner = actor_is_owner
        self.actor_roles = actor_roles or [
            "config:read",
            "config:write",
            "members:manage",
        ]
        self.invitation_available = invitation_available
        self.account_email = account_email
        self.target_roles = target_roles or ["config:read"]
        self.target_is_owner = target_is_owner
        self.pending_blocked_reason = pending_blocked_reason
        self.membership_audit_rows = (
            membership_audit_rows
            if membership_audit_rows is not None
            else []
        )
        self.membership_exists = False
        self.invitation_row: dict[str, object] | None = None
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.audit_calls: list[tuple[object, ...]] = []
        self.rate_bucket_attempts: dict[tuple[str, str], int] = {}
        self.rate_bucket_started_at: dict[tuple[str, str], datetime] = {}

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchrow(self, query: str, *args):
        self.statements.append((query, args))
        if "INSERT INTO admin_login_rate_buckets" in query:
            scope, key_hash, _, now = args
            key = (str(scope), str(key_hash))
            self.rate_bucket_attempts[key] = (
                self.rate_bucket_attempts.get(key, 0) + 1
            )
            self.rate_bucket_started_at.setdefault(key, now)
            return {
                "window_started_at": self.rate_bucket_started_at[key],
                "attempt_count": self.rate_bucket_attempts[key],
            }
        if "AS is_owner" in query and "SELECT" in query:
            if "membership.created_at AS joined_at" in query:
                return {
                    "user_id": TARGET_ID,
                    "email": "target@example.com",
                    "roles": self.target_roles,
                    "active": True,
                    "is_owner": self.target_is_owner,
                    "joined_at": NOW,
                }
            if "membership.user_id," in query:
                return {
                    "user_id": TARGET_ID,
                    "email": "target@example.com",
                    "roles": self.target_roles,
                    "is_owner": self.target_is_owner,
                }
            return {
                "roles": self.actor_roles,
                "owner_user_id": ACTOR_ID if self.actor_is_owner else TARGET_ID,
                "is_owner": self.actor_is_owner,
            }
        if "expires_at <= NOW()" in query:
            return None
        if "INSERT INTO admin_project_invitations" in query:
            self.invitation_row = {
                "invitation_id": args[0],
                "email": args[3],
                "roles": args[4],
                "expires_at": NOW + timedelta(days=7),
                "created_at": NOW,
            }
            return self.invitation_row
        if "FROM admin_project_invitations AS invitation" in query:
            if not self.invitation_available:
                return None
            return {
                "invitation_id": INVITATION_ID,
                "project_id": "demo",
                "email": "invitee@example.com",
                "roles": ["config:read"],
                "inviter_user_id": ACTOR_ID,
                "expires_at": NOW + timedelta(days=7),
                "owner_user_id": ACTOR_ID,
            }
        if "FROM admin_users" in query and "WHERE user_id = $1" in query:
            return {
                "user_id": TARGET_ID,
                "email": self.account_email,
                "active": True,
            }
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetchval(self, query: str, *args):
        self.statements.append((query, args))
        if "SELECT membership.user_id" in query:
            return None
        if "SELECT user_id FROM admin_users WHERE email" in query:
            return None
        if "SELECT count(*) FROM admin_users" in query:
            return 1
        if "INSERT INTO admin_user_projects" in query:
            if self.membership_exists:
                return None
            self.membership_exists = True
            return args[0]
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def fetch(self, query: str, *args):
        self.statements.append((query, args))
        if "membership.created_at AS joined_at" in query:
            return [
                {
                    "user_id": ACTOR_ID,
                    "email": "owner@example.com",
                    "roles": self.actor_roles,
                    "active": True,
                    "is_owner": self.actor_is_owner,
                    "joined_at": NOW,
                }
            ]
        if "FROM admin_project_invitations AS invitation" in query:
            if self.invitation_row is None:
                return []
            return [
                {
                    **self.invitation_row,
                    "inviter_email": "owner@example.com",
                    "blocked_reason": self.pending_blocked_reason,
                }
            ]
        if "FROM admin_project_membership_audit" in query:
            before_created_at, before_audit_id, limit = args[1:]
            rows = self.membership_audit_rows
            if before_created_at is not None:
                rows = [
                    row
                    for row in rows
                    if (row["created_at"], row["audit_id"])
                    < (before_created_at, before_audit_id)
                ]
            return rows[:limit]
        raise AssertionError(f"Unexpected fetch query: {query}")

    async def execute(self, query: str, *args):
        self.statements.append((query, args))
        if "INSERT INTO admin_project_membership_audit" in query:
            self.audit_calls.append(args)
        if "SET accepted_at = NOW()" in query:
            self.invitation_available = False
        if "SET roles = $3" in query:
            self.target_roles = list(args[2])
        return "OK"


class MemberPool:
    def __init__(self, connection: MemberConnection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _session(
    *,
    user_id: UUID = ACTOR_ID,
    email: str = "owner@example.com",
) -> AdminSession:
    return AdminSession(
        session_id="10000000-0000-4000-8000-000000000001",
        token_hash="b" * 64,
        deployment_id="30000000-0000-4000-8000-000000000003",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        user_id=str(user_id),
        email=email,
        projects={
            "demo": frozenset(
                {"config:read", "config:write", "members:manage"}
            )
        },
    )


def _client(
    connection: MemberConnection,
    *,
    session: AdminSession | None,
    settings_overrides: dict[str, object] | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.settings = make_settings(**(settings_overrides or {}))
    app.state.pg_pool = MemberPool(connection)
    app.include_router(members.router)
    if session is not None:
        app.dependency_overrides[require_session] = lambda: session
    return TestClient(app)


def test_invite_is_revealed_once_and_list_never_contains_secret_material() -> None:
    connection = MemberConnection()
    with _client(connection, session=_session()) as client:
        created = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={
                "email": "Invitee@Example.com",
                "roles": ["config:read", "config:write"],
            },
        )
        listed = client.get("/api/projects/demo/members")

    assert created.status_code == 201
    reveal = created.json()
    assert reveal["email"] == "invitee@example.com"
    assert len(reveal["invitation_token"]) == 43
    insert = next(
        call
        for call in connection.statements
        if "INSERT INTO admin_project_invitations" in call[0]
    )
    stored_digest = insert[1][1]
    revealed_token = reveal["invitation_token"]
    assert len(stored_digest) == 64
    assert stored_digest == token_hash(revealed_token)
    assert stored_digest != reveal["invitation_token"]

    assert listed.status_code == 200
    pending = listed.json()["pending_invitations"][0]
    assert set(pending) == {
        "invitation_id",
        "email",
        "roles",
        "inviter_email",
        "status",
        "blocked_reason",
        "expires_at",
        "created_at",
    }
    assert pending["status"] == "valid"
    assert pending["blocked_reason"] is None
    assert "invitation_token" not in pending
    assert "token" not in pending
    assert "token_hash" not in pending


def test_pending_invitation_exposes_live_blocked_authority_state() -> None:
    connection = MemberConnection(
        pending_blocked_reason="inviter_lacks_members_manage"
    )
    connection.invitation_row = {
        "invitation_id": INVITATION_ID,
        "email": "invitee@example.com",
        "roles": ["config:read"],
        "expires_at": NOW + timedelta(days=7),
        "created_at": NOW,
    }
    with _client(connection, session=_session()) as client:
        listed = client.get("/api/projects/demo/members")

    assert listed.status_code == 200
    assert listed.json()["pending_invitations"] == [
        {
            "invitation_id": str(INVITATION_ID),
            "email": "invitee@example.com",
            "roles": ["config:read"],
            "inviter_email": "owner@example.com",
            "status": "blocked",
            "blocked_reason": "inviter_lacks_members_manage",
            "expires_at": "2026-08-06T12:00:00Z",
            "created_at": "2026-07-30T12:00:00Z",
        }
    ]
    invitation_query = next(
        query
        for query, _ in connection.statements
        if "END AS blocked_reason" in query
    )
    assert "LEFT JOIN admin_user_projects AS inviter_membership" in invitation_query
    assert "WHEN NOT inviter.active" in invitation_query
    assert "ANY(inviter_membership.roles)" in invitation_query
    assert "invitation.roles <@ inviter_membership.roles" in invitation_query
    assert "project.owner_user_id IS DISTINCT FROM" in invitation_query
    for reason in (
        "inviter_inactive",
        "inviter_not_project_member",
        "inviter_lacks_members_manage",
        "roles_exceed_inviter_authority",
        "members_manage_requires_owner",
    ):
        assert reason in invitation_query


@pytest.mark.parametrize(
    "status, blocked_reason",
    [
        ("valid", "inviter_inactive"),
        ("blocked", None),
    ],
)
def test_pending_invitation_rejects_incoherent_status(
    status: str,
    blocked_reason: str | None,
) -> None:
    with pytest.raises(ValidationError, match="blocked_reason must be null"):
        PendingProjectInvitation(
            invitation_id=INVITATION_ID,
            email="invitee@example.com",
            roles=["config:read"],
            inviter_email="owner@example.com",
            status=status,
            blocked_reason=blocked_reason,
            expires_at=NOW + timedelta(days=7),
            created_at=NOW,
        )


def test_invitation_roles_are_canonical_and_bounded_by_live_authority() -> None:
    connection = MemberConnection(
        actor_is_owner=False,
        actor_roles=["config:read", "members:manage"],
    )
    with _client(connection, session=_session()) as client:
        out_of_order = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={"email": "invitee@example.com", "roles": ["members:manage", "config:read"]},
        )
        manager_grant = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={"email": "invitee@example.com", "roles": ["members:manage"]},
        )
        above_ceiling = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={"email": "invitee@example.com", "roles": ["config:write"]},
        )

    assert out_of_order.status_code == 422
    assert manager_grant.status_code == 403
    assert above_ceiling.status_code == 403
    assert not any(
        "INSERT INTO admin_project_invitations" in query
        for query, _ in connection.statements
    )


def test_existing_matching_account_accepts_once_with_audited_membership() -> None:
    session = _session(
        user_id=TARGET_ID,
        email="invitee@example.com",
    )
    connection = MemberConnection()
    with _client(connection, session=session) as client:
        accepted = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )
        replayed = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )

    assert accepted.status_code == 200
    assert accepted.json()["schema_version"] == "console_identity@1"
    assert accepted.json()["projects"] == [
        {"project_id": "demo", "roles": ["config:read"]}
    ]
    assert replayed.status_code == 404
    assert len(connection.audit_calls) == 1
    assert connection.audit_calls[0][2] == "invitation_accept"
    assert any(
        "SET accepted_at = NOW()" in query
        for query, _ in connection.statements
    )


def test_wrong_email_and_invalid_lifecycle_use_same_unavailable_response() -> None:
    wrong_email = MemberConnection(account_email="other@example.com")
    session = _session(user_id=TARGET_ID, email="other@example.com")
    with _client(wrong_email, session=session) as client:
        mismatched = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )

    unavailable = MemberConnection(invitation_available=False)
    with _client(unavailable, session=session) as client:
        invalid = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )

    assert mismatched.status_code == invalid.status_code == 404
    assert mismatched.json() == invalid.json() == {
        "detail": "Invitation is unavailable"
    }


def test_invitation_rate_limit_does_not_consume_login_buckets() -> None:
    connection = MemberConnection()
    with _client(
        connection,
        session=_session(
            user_id=TARGET_ID,
            email="invitee@example.com",
        ),
        settings_overrides={
            "invitation_global_rate_limit": 100,
            "invitation_network_rate_limit": 100,
            "invitation_token_rate_limit": 1,
        },
    ) as client:
        accepted = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )
        throttled = client.post(
            "/api/invitations/accept",
            headers={"Origin": "http://admin.test"},
            json={"invitation_token": RAW_TOKEN},
        )

    assert accepted.status_code == 200
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"] == "60"
    assert {scope for scope, _ in connection.rate_bucket_attempts} == {
        "invitation_global",
        "invitation_network",
        "invitation_token",
    }


def test_invitation_url_token_and_registration_aliases_are_absent() -> None:
    connection = MemberConnection()
    with _client(connection, session=_session()) as client:
        inspection = client.get(f"/api/invitations/{RAW_TOKEN}")
        acceptance = client.post(
            f"/api/invitations/{RAW_TOKEN}/accept",
            headers={"Origin": "http://admin.test"},
        )
        registration = client.post(
            f"/api/invitations/{RAW_TOKEN}/register",
            headers={"Origin": "http://admin.test"},
            json={"password": "invited-password"},
        )

    assert inspection.status_code == 404
    assert acceptance.status_code == 404
    assert registration.status_code == 404
    assert connection.statements == []


def test_role_replacement_and_removal_cannot_mutate_owner_or_delegated_manager() -> None:
    owner_target = MemberConnection(target_is_owner=True)
    with _client(owner_target, session=_session()) as client:
        replace_owner = client.put(
            f"/api/projects/demo/members/{TARGET_ID}/roles",
            headers={"Origin": "http://admin.test"},
            json={"roles": ["config:read", "config:write"]},
        )
        remove_owner = client.delete(
            f"/api/projects/demo/members/{TARGET_ID}",
            headers={"Origin": "http://admin.test"},
        )
    assert replace_owner.status_code == remove_owner.status_code == 409

    delegated = MemberConnection(
        actor_is_owner=False,
        actor_roles=["config:read", "members:manage"],
        target_roles=["config:read", "members:manage"],
    )
    with _client(delegated, session=_session()) as client:
        replace_manager = client.put(
            f"/api/projects/demo/members/{TARGET_ID}/roles",
            headers={"Origin": "http://admin.test"},
            json={"roles": ["config:read"]},
        )
        remove_manager = client.delete(
            f"/api/projects/demo/members/{TARGET_ID}",
            headers={"Origin": "http://admin.test"},
        )
    assert replace_manager.status_code == remove_manager.status_code == 403


def test_membership_audit_uses_keyset_pagination() -> None:
    newest_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    older_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
    rows = [
        {
            "audit_id": NEWEST_AUDIT_ID,
            "project_id": "demo",
            "action": "roles_replace",
            "actor_user_id": ACTOR_ID,
            "actor_email": "owner@example.com",
            "subject_user_id": TARGET_ID,
            "subject_email": "target@example.com",
            "invitation_id": None,
            "previous_roles": ["config:read"],
            "new_roles": ["config:read", "config:write"],
            "created_at": newest_at,
        },
        {
            "audit_id": OLDER_AUDIT_ID,
            "project_id": "demo",
            "action": "invitation_create",
            "actor_user_id": ACTOR_ID,
            "actor_email": "owner@example.com",
            "subject_user_id": None,
            "subject_email": "invitee@example.com",
            "invitation_id": INVITATION_ID,
            "previous_roles": None,
            "new_roles": ["config:read"],
            "created_at": older_at,
        },
    ]
    connection = MemberConnection(membership_audit_rows=rows)
    with _client(connection, session=_session()) as client:
        first = client.get("/api/projects/demo/members/audit?limit=1")
        cursor = first.json()["next_cursor"]
        second = client.get(
            "/api/projects/demo/members/audit",
            params={
                "limit": 1,
                "before_created_at": cursor["created_at"],
                "before_audit_id": cursor["audit_id"],
            },
        )
        partial_cursor = client.get(
            "/api/projects/demo/members/audit",
            params={"before_created_at": "2026-08-01T00:00:00Z"},
        )

    assert [entry["action"] for entry in first.json()["entries"]] == [
        "roles_replace"
    ]
    assert cursor == {
        "created_at": "2026-08-01T00:00:00Z",
        "audit_id": str(NEWEST_AUDIT_ID),
    }
    assert [entry["action"] for entry in second.json()["entries"]] == [
        "invitation_create"
    ]
    assert second.json()["next_cursor"] is None
    assert partial_cursor.status_code == 422
    audit_queries = [
        (query, args)
        for query, args in connection.statements
        if "FROM admin_project_membership_audit" in query
    ]
    assert all(
        "(audit.created_at, audit.audit_id)" in query
        for query, _ in audit_queries
    )
    assert audit_queries[0][1] == ("demo", None, None, 2)
    assert audit_queries[1][1] == (
        "demo",
        newest_at,
        NEWEST_AUDIT_ID,
        2,
    )


def test_mutations_use_bearer_authority_and_strict_request_shapes() -> None:
    connection = MemberConnection()
    with _client(connection, session=_session()) as client:
        accepted = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={"email": "invitee@example.com", "roles": ["config:read"]},
        )
        unknown = client.post(
            "/api/projects/demo/invitations",
            headers={"Origin": "http://admin.test"},
            json={
                "email": "invitee@example.com",
                "roles": ["config:read"],
                "expires_in_days": 30,
            },
        )

    assert accepted.status_code == 201
    assert unknown.status_code == 422
