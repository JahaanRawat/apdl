-- Direct-console sessions are non-ambient, fixed-expiration bearer tokens.
-- Existing cookie sessions cannot be migrated safely because their raw tokens
-- are deliberately unavailable, so revoke them during the breaking cutover.
TRUNCATE TABLE admin_sessions;

DROP INDEX IF EXISTS admin_sessions_active_idx;

ALTER TABLE admin_sessions
    DROP COLUMN csrf_hash,
    DROP COLUMN last_seen_at,
    ADD COLUMN deployment_id UUID NOT NULL;

CREATE INDEX admin_sessions_active_idx
    ON admin_sessions (deployment_id, token_hash, expires_at)
    WHERE revoked_at IS NULL;

COMMENT ON COLUMN admin_sessions.deployment_id IS
    'Stable deployment identity that issued and is allowed to accept this token.';
