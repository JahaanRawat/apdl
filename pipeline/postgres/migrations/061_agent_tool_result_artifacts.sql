-- Migration 061: bounded, tenant-scoped previews for agent tool results.
--
-- Full warehouse results remain ephemeral. This table keeps only a secret/PII-
-- redacted UTF-8 preview, an integrity hash of the full canonical result, and
-- bounded metadata for seven days. Audit metadata remains after the preview
-- expires, but no preview can be read without both agents:read and query:read.

CREATE UNIQUE INDEX agent_audit_log_id_run_identity_idx
    ON public.agent_audit_log (id, run_id);

CREATE TABLE public.agent_tool_result_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_version TEXT NOT NULL DEFAULT 'tool_result_artifact@1',
    audit_entry_id BIGINT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    preview_text TEXT NOT NULL,
    source_byte_count BIGINT NOT NULL,
    preview_byte_count INTEGER GENERATED ALWAYS AS (
        octet_length(preview_text)
    ) STORED,
    truncated BOOLEAN NOT NULL,
    redacted BOOLEAN NOT NULL,
    data_classification TEXT NOT NULL DEFAULT 'confidential',
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (
        statement_timestamp() + INTERVAL '7 days'
    ),
    CONSTRAINT agent_tool_result_artifacts_run_fk
        FOREIGN KEY (run_id, project_id)
        REFERENCES public.agent_runs (run_id, project_id)
        ON DELETE CASCADE,
    CONSTRAINT agent_tool_result_artifacts_audit_fk
        FOREIGN KEY (audit_entry_id, run_id)
        REFERENCES public.agent_audit_log (id, run_id)
        ON DELETE CASCADE,
    CONSTRAINT agent_tool_result_artifacts_schema_check CHECK (
        schema_version = 'tool_result_artifact@1'
    ),
    CONSTRAINT agent_tool_result_artifacts_project_check CHECK (
        project_id ~ '^[A-Za-z0-9]{1,64}$'
    ),
    CONSTRAINT agent_tool_result_artifacts_run_check CHECK (
        char_length(run_id) BETWEEN 1 AND 128
        AND btrim(run_id) <> ''
    ),
    CONSTRAINT agent_tool_result_artifacts_source_check CHECK (
        source_id ~ '^warehouse:[0-9a-f]{24}$'
    ),
    CONSTRAINT agent_tool_result_artifacts_tool_check CHECK (
        tool_name ~ '^[a-z][a-z0-9_]{0,127}$'
    ),
    CONSTRAINT agent_tool_result_artifacts_hash_check CHECK (
        content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT agent_tool_result_artifacts_preview_check CHECK (
        octet_length(preview_text) BETWEEN 1 AND 4096
    ),
    CONSTRAINT agent_tool_result_artifacts_source_bytes_check CHECK (
        source_byte_count >= 0
    ),
    CONSTRAINT agent_tool_result_artifacts_classification_check CHECK (
        data_classification = 'confidential'
    ),
    CONSTRAINT agent_tool_result_artifacts_expiry_check CHECK (
        expires_at > created_at
        AND expires_at <= created_at + INTERVAL '7 days'
    )
);

CREATE INDEX agent_tool_result_artifacts_run_created_idx
    ON public.agent_tool_result_artifacts (run_id, created_at, artifact_id);

CREATE INDEX agent_tool_result_artifacts_project_created_idx
    ON public.agent_tool_result_artifacts (project_id, created_at, artifact_id);

CREATE INDEX agent_tool_result_artifacts_expiry_idx
    ON public.agent_tool_result_artifacts (expires_at, artifact_id);

REVOKE ALL ON public.agent_tool_result_artifacts FROM PUBLIC, apdl_runtime;
GRANT SELECT, DELETE
    ON public.agent_tool_result_artifacts TO apdl_agents;
GRANT INSERT (
    audit_entry_id,
    run_id,
    project_id,
    source_id,
    tool_name,
    content_sha256,
    preview_text,
    source_byte_count,
    truncated,
    redacted
) ON public.agent_tool_result_artifacts TO apdl_agents;

COMMENT ON TABLE public.agent_tool_result_artifacts IS
    'Seven-day confidential redacted previews for agent warehouse tool results';
COMMENT ON COLUMN public.agent_tool_result_artifacts.content_sha256 IS
    'SHA-256 of the complete canonical result before redaction or truncation';
COMMENT ON COLUMN public.agent_tool_result_artifacts.preview_text IS
    'Inert secret/PII-redacted UTF-8 text capped at 4096 bytes';
