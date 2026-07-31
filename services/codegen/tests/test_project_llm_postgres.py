"""Live PostgreSQL checks for Codegen project-scoped LLM authority.

Set ``CODEGEN_TEST_POSTGRES_URL`` to a disposable, fully migrated database
owned by an operator role. The regular unit suite ignores this module; CI runs
it explicitly and refuses to skip it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import asyncpg
import pytest

from app.store.llm_credentials import (
    CredentialCipher,
    CredentialConflictError,
    CredentialDecryptionError,
    CredentialNotFoundError,
    CredentialMetadata,
    CredentialStoreError,
    ProjectCredentialStore,
    rotate_active_credentials,
)


POSTGRES_URL = os.getenv("CODEGEN_TEST_POSTGRES_URL", "").strip() or None

if os.getenv("GITHUB_ACTIONS") == "true" and POSTGRES_URL is None:
    raise RuntimeError(
        "CODEGEN_TEST_POSTGRES_URL is required in GitHub Actions; "
        "the Codegen project LLM PostgreSQL suite must not be skipped"
    )

pytestmark = pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="CODEGEN_TEST_POSTGRES_URL is not configured",
)

CIPHER = CredentialCipher(bytes(range(32)))
ROTATED_CIPHER = CredentialCipher(b"n" * 32)
MAINTENANCE_INHIBITOR_LOCK_ID = 4_158_044_083
MAINTENANCE_GUARD_LOCK_ID = 4_158_044_084


class KnownRotationSourceCipher(CredentialCipher):
    """Test-only source able to re-run against rows from a prior live run."""

    def __init__(self) -> None:
        super().__init__(bytes(range(32)))

    def decrypt(self, **kwargs: Any) -> str:
        key_id = kwargs.get("encryption_key_id")
        source = ROTATED_CIPHER if key_id == ROTATED_CIPHER.key_id else CIPHER
        return source.decrypt(**kwargs)


def _project_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


async def _create_operator_project(
    conn: asyncpg.Connection,
    project_id: str,
    *,
    with_owner: bool = False,
) -> uuid.UUID | None:
    await conn.execute(
        "INSERT INTO admin_projects (project_id) VALUES ($1)",
        project_id,
    )
    if not with_owner:
        return None
    owner_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO admin_users (
            user_id, email, password_hash, active
        ) VALUES ($1, $2, '$argon2id$codegen-postgres-fixture', TRUE)
        """,
        owner_id,
        f"{owner_id.hex}@codegen.test",
    )
    await conn.execute(
        """
        INSERT INTO admin_user_projects (user_id, project_id, roles)
        VALUES (
            $1, $2,
            ARRAY[
                'agents:read', 'agents:manage', 'credentials:manage',
                'members:manage'
            ]::TEXT[]
        )
        """,
        owner_id,
        project_id,
    )
    await conn.execute(
        "UPDATE admin_projects SET owner_user_id = $2 WHERE project_id = $1",
        project_id,
        owner_id,
    )
    return owner_id


@pytest.mark.asyncio
async def test_credential_lifecycle_serializes_and_never_stores_plaintext() -> None:
    assert POSTGRES_URL is not None
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=4)
    project_id = _project_id("cred")
    other_project_id = _project_id("other")
    first_secret = f"first-{uuid.uuid4().hex}"
    second_secret = f"second-{uuid.uuid4().hex}"
    store = ProjectCredentialStore(pool, CIPHER)
    try:
        async with pool.acquire() as conn:
            await _create_operator_project(conn, project_id)
            await _create_operator_project(conn, other_project_id)

        results = await asyncio.gather(
            store.create(
                project_id,
                "openai",
                first_secret,
                actor="test:credential-create-a",
            ),
            store.create(
                project_id,
                "openai",
                second_secret,
                actor="test:credential-create-b",
            ),
            return_exceptions=True,
        )
        created = [
            value for value in results if isinstance(value, CredentialMetadata)
        ]
        conflicts = [
            value
            for value in results
            if isinstance(value, CredentialConflictError)
        ]
        assert len(created) == 1
        assert len(conflicts) == 1
        active = created[0]
        winning_secret = (
            first_secret
            if (await store.load_active(project_id, "openai")).api_key
            == first_secret
            else second_secret
        )

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT ciphertext, nonce
                FROM codegen_project_provider_credentials
                WHERE credential_id = $1
                """,
                active.credential_id,
            )
        assert row is not None
        ciphertext = bytes(row["ciphertext"])
        assert winning_secret.encode() not in ciphertext
        assert first_secret.encode() not in ciphertext
        assert second_secret.encode() not in ciphertext
        assert len(bytes(row["nonce"])) == 12
        async with pool.acquire() as conn:
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="successor_not_self",
            ):
                await conn.execute(
                    """
                    UPDATE codegen_project_provider_credentials
                    SET state = 'replaced', ciphertext = NULL, nonce = NULL,
                        successor_credential_id = credential_id,
                        retired_by_actor = 'test:self-loop',
                        retirement_reason = 'provider_connection_replaced',
                        retired_at = NOW()
                    WHERE credential_id = $1
                    """,
                    active.credential_id,
                )

        replacement = await store.replace(
            project_id,
            "openai",
            f"replacement-{uuid.uuid4().hex}",
            expected_credential_id=active.credential_id,
            actor="test:credential-replace",
        )
        historical = await store.metadata(
            project_id,
            "openai",
            credential_id=active.credential_id,
            credential_version=active.credential_version,
        )
        assert historical is not None
        assert historical.state == "replaced"
        assert replacement.credential_version == active.credential_version + 1
        with pytest.raises(CredentialNotFoundError):
            await store.load_active(other_project_id, "openai")
        with pytest.raises(CredentialNotFoundError):
            await store.load_active(
                project_id,
                "openai",
                credential_id=active.credential_id,
            )

        revoked = await store.revoke(
            project_id,
            "openai",
            expected_credential_id=replacement.credential_id,
            actor="test:credential-revoke",
        )
        assert revoked.state == "revoked"
        with pytest.raises(CredentialNotFoundError):
            await store.load_active(project_id, "openai")

        async with pool.acquire() as conn:
            lifecycle = await conn.fetch(
                """
                SELECT state, ciphertext, nonce, retirement_reason
                FROM codegen_project_provider_credentials
                WHERE project_id = $1 AND provider = 'openai'
                ORDER BY credential_version
                """,
                project_id,
            )
            audit = await conn.fetch(
                """
                SELECT action
                FROM codegen_project_provider_credential_audit
                WHERE project_id = $1
                ORDER BY created_at, audit_id
                """,
                project_id,
            )
        assert [
            (
                row["state"],
                row["ciphertext"],
                row["nonce"],
                row["retirement_reason"],
            )
            for row in lifecycle
        ] == [
            ("replaced", None, None, "provider_connection_replaced"),
            ("revoked", None, None, "provider_connection_revoked"),
        ]
        assert [row["action"] for row in audit] == [
            "create",
            "replace",
            "revoke",
        ]
        async with pool.acquire() as conn:
            for state, wrong_reason in (
                ("replaced", "provider_connection_revoked"),
                ("revoked", "provider_connection_replaced"),
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(
                        """
                        UPDATE codegen_project_provider_credentials
                        SET retirement_reason = $3
                        WHERE project_id = $1
                          AND provider = 'openai'
                          AND state = $2
                        """,
                        project_id,
                        state,
                        wrong_reason,
                    )
            with pytest.raises(
                asyncpg.CheckViolationError,
                match="audit rows are immutable",
            ):
                await conn.execute(
                    """
                    UPDATE codegen_project_provider_credential_audit
                    SET actor = 'test:mutated-audit'
                    WHERE project_id = $1
                    """,
                    project_id,
                )
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_key_rotation_requires_barriers_and_reencrypts_atomically() -> None:
    assert POSTGRES_URL is not None
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    new_cipher = ROTATED_CIPHER
    source_cipher = KnownRotationSourceCipher()
    old_store = ProjectCredentialStore(pool, CIPHER)
    projects = (_project_id("rotatea"), _project_id("rotateb"))
    secrets = (
        f"rotation-secret-a-{uuid.uuid4().hex}",
        f"rotation-secret-b-{uuid.uuid4().hex}",
    )
    try:
        async with pool.acquire() as setup:
            for project_id in projects:
                await _create_operator_project(setup, project_id)
        first = await old_store.create(
            projects[0],
            "anthropic",
            secrets[0],
            actor="test:rotation-setup",
        )
        second = await old_store.create(
            projects[1],
            "google",
            secrets[1],
            actor="test:rotation-setup",
        )

        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            with pytest.raises(
                CredentialStoreError,
                match="Active PostgreSQL transaction",
            ):
                await rotate_active_credentials(
                    conn,
                    old_cipher=source_cipher,
                    new_cipher=new_cipher,
                    actor="test:key-rotation",
                )
            with pytest.raises(
                CredentialStoreError,
                match="maintenance barrier",
            ):
                async with conn.transaction():
                    await rotate_active_credentials(
                        conn,
                        old_cipher=source_cipher,
                        new_cipher=new_cipher,
                        actor="test:key-rotation",
                    )
            await conn.execute(
                "SELECT pg_advisory_lock($1::BIGINT)",
                MAINTENANCE_INHIBITOR_LOCK_ID,
            )
            with pytest.raises(
                CredentialStoreError,
                match="maintenance barrier",
            ):
                async with conn.transaction():
                    await rotate_active_credentials(
                        conn,
                        old_cipher=CIPHER,
                        new_cipher=new_cipher,
                        actor="test:key-rotation",
                    )
            await conn.execute(
                "SELECT pg_advisory_lock($1::BIGINT)",
                MAINTENANCE_GUARD_LOCK_ID,
            )
            async with conn.transaction():
                count, audit_ids = await rotate_active_credentials(
                    conn,
                    old_cipher=source_cipher,
                    new_cipher=new_cipher,
                    actor="test:key-rotation",
                )
        finally:
            await conn.close()

        assert count >= 2
        assert len(audit_ids) == count
        new_store = ProjectCredentialStore(pool, new_cipher)
        assert (
            await new_store.load_active(projects[0], "anthropic")
        ).api_key == secrets[0]
        assert (
            await new_store.load_active(projects[1], "google")
        ).api_key == secrets[1]
        with pytest.raises(CredentialDecryptionError):
            await old_store.load_active(projects[0], "anthropic")

        async with pool.acquire() as verify:
            rows = await verify.fetch(
                """
                SELECT credential_id, encryption_key_id
                FROM codegen_project_provider_credentials
                WHERE credential_id = ANY($1::UUID[])
                ORDER BY credential_id
                """,
                [first.credential_id, second.credential_id],
            )
            audited = await verify.fetchval(
                """
                SELECT count(*)
                FROM codegen_project_provider_credential_audit
                WHERE audit_id = ANY($1::UUID[])
                  AND action = 'reencrypt'
                  AND encryption_key_id = $2
                """,
                list(audit_ids),
                new_cipher.key_id,
            )
        assert len(rows) == 2
        assert {
            row["encryption_key_id"] for row in rows
        } == {new_cipher.key_id}
        assert audited == count
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_key_rotation_rolls_back_after_mid_write_failure() -> None:
    assert POSTGRES_URL is not None
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=1, max_size=3)
    old_store = ProjectCredentialStore(pool, CIPHER)
    projects = (_project_id("rollbacka"), _project_id("rollbackb"))
    credentials: list[CredentialMetadata] = []

    class FailingCipher(CredentialCipher):
        def __init__(self) -> None:
            super().__init__(b"f" * 32)
            self.encryptions = 0

        def encrypt(self, *args: Any, **kwargs: Any):
            self.encryptions += 1
            if self.encryptions == 2:
                raise RuntimeError("injected rotation failure")
            return super().encrypt(*args, **kwargs)

    try:
        async with pool.acquire() as setup:
            for project_id in projects:
                await _create_operator_project(setup, project_id)
        for index, project_id in enumerate(projects):
            credentials.append(
                await old_store.create(
                    project_id,
                    "xai",
                    f"rollback-secret-{index}-{uuid.uuid4().hex}",
                    actor="test:rotation-rollback-setup",
                )
            )

        conn = await asyncpg.connect(POSTGRES_URL)
        failing_cipher = FailingCipher()
        try:
            await conn.execute(
                "SELECT pg_advisory_lock($1::BIGINT)",
                MAINTENANCE_INHIBITOR_LOCK_ID,
            )
            await conn.execute(
                "SELECT pg_advisory_lock($1::BIGINT)",
                MAINTENANCE_GUARD_LOCK_ID,
            )
            with pytest.raises(RuntimeError, match="injected rotation failure"):
                async with conn.transaction():
                    await rotate_active_credentials(
                        conn,
                        old_cipher=KnownRotationSourceCipher(),
                        new_cipher=failing_cipher,
                        actor="test:key-rotation-rollback",
                    )
        finally:
            await conn.close()

        async with pool.acquire() as verify:
            rows = await verify.fetch(
                """
                SELECT credential_id, encryption_key_id
                FROM codegen_project_provider_credentials
                WHERE credential_id = ANY($1::UUID[])
                ORDER BY credential_id
                """,
                [item.credential_id for item in credentials],
            )
            audit_count = await verify.fetchval(
                """
                SELECT count(*)
                FROM codegen_project_provider_credential_audit
                WHERE credential_id = ANY($1::UUID[])
                  AND action = 'reencrypt'
                """,
                [item.credential_id for item in credentials],
            )
        assert len(rows) == 2
        assert {row["encryption_key_id"] for row in rows} == {CIPHER.key_id}
        assert audit_count == 0
        for project_id in projects:
            assert (
                await old_store.load_active(project_id, "xai")
            ).credential_version == 1
    finally:
        await pool.close()
