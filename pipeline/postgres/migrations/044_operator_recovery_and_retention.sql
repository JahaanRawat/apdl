-- Migration 044: bounded operator recovery for durable delivery and audit data.
--
-- Readiness probes use a covering partial index instead of scanning retained
-- terminal outbox rows. Quarantine actions are project-scoped and recorded
-- without duplicating the potentially sensitive payload. Experiment snapshots
-- remain immutable during normal operation, while an explicit database-
-- operator procedure provides a narrow, audited privacy-retention escape hatch.

DO $apdl_validate_database_roles$
DECLARE
    role_record RECORD;
BEGIN
    SELECT *
    INTO role_record
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_runtime';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'required role apdl_runtime is missing; provision database roles before migration 044';
    END IF;
    IF NOT role_record.rolcanlogin
       OR role_record.rolsuper
       OR role_record.rolinherit
       OR role_record.rolcreaterole
       OR role_record.rolcreatedb
       OR role_record.rolreplication
       OR role_record.rolbypassrls THEN
        RAISE EXCEPTION
            'apdl_runtime must be LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE member = role_record.oid
    ) THEN
        RAISE EXCEPTION
            'apdl_runtime must not be a member of any database role';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database
        WHERE datdba = role_record.oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace
        WHERE nspowner = role_record.oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_shdepend
        WHERE refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          AND refobjid = role_record.oid
          AND deptype = 'o'
    ) THEN
        RAISE EXCEPTION
            'apdl_runtime must not own a database, schema, or database object';
    END IF;

    SELECT *
    INTO role_record
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_operator';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'required role apdl_audit_operator is missing; provision database roles before migration 044';
    END IF;
    IF role_record.rolcanlogin
       OR role_record.rolsuper
       OR role_record.rolinherit
       OR role_record.rolcreaterole
       OR role_record.rolcreatedb
       OR role_record.rolreplication
       OR role_record.rolbypassrls THEN
        RAISE EXCEPTION
            'apdl_audit_operator must be NOLOGIN NOINHERIT and unprivileged';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE member = role_record.oid
    ) THEN
        RAISE EXCEPTION
            'apdl_audit_operator must not be a member of any database role';
    END IF;

    SELECT *
    INTO role_record
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_purge_definer';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'required role apdl_audit_purge_definer is missing; provision database roles before migration 044';
    END IF;
    IF role_record.rolcanlogin
       OR role_record.rolsuper
       OR role_record.rolinherit
       OR role_record.rolcreaterole
       OR role_record.rolcreatedb
       OR role_record.rolreplication
       OR role_record.rolbypassrls THEN
        RAISE EXCEPTION
            'apdl_audit_purge_definer must be NOLOGIN NOINHERIT and unprivileged';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE member = role_record.oid
    ) THEN
        RAISE EXCEPTION
            'apdl_audit_purge_definer must not be a member of any database role';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE roleid = role_record.oid
    ) THEN
        RAISE EXCEPTION
            'apdl_audit_purge_definer must not be granted to any database role';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_shdepend
        WHERE refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          AND refobjid = role_record.oid
          AND deptype = 'o'
    ) THEN
        RAISE EXCEPTION
            'apdl_audit_purge_definer must not own preexisting database objects';
    END IF;
END
$apdl_validate_database_roles$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public
    FROM apdl_runtime, apdl_audit_operator, apdl_audit_purge_definer;

DO $apdl_validate_public_schema_boundary$
BEGIN
    IF pg_catalog.has_schema_privilege(
        'apdl_runtime',
        'public',
        'CREATE'
    ) OR pg_catalog.has_schema_privilege(
        'apdl_audit_operator',
        'public',
        'CREATE'
    ) OR pg_catalog.has_schema_privilege(
        'apdl_audit_purge_definer',
        'public',
        'CREATE'
    ) THEN
        RAISE EXCEPTION
            'fixed APDL roles must not have effective CREATE on schema public';
    END IF;
END
$apdl_validate_public_schema_boundary$;

CREATE INDEX idx_config_outbox_metrics_pending
    ON config_outbox (created_at, id)
    INCLUDE (attempts)
    WHERE processed_at IS NULL AND quarantined_at IS NULL;

CREATE INDEX idx_config_outbox_quarantine_project_id
    ON config_outbox (project_id, id DESC)
    WHERE quarantined_at IS NOT NULL;

CREATE INDEX idx_experiment_audit_keyset
    ON experiment_audit_log (project_id, experiment_key, id DESC);

CREATE TABLE config_outbox_operator_log (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL CHECK (btrim(project_id) <> ''),
    outbox_id BIGINT NOT NULL CHECK (outbox_id >= 1),
    action TEXT NOT NULL CHECK (action IN ('replay', 'discard')),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    kind TEXT NOT NULL CHECK (btrim(kind) <> ''),
    failure_class TEXT NOT NULL CHECK (btrim(failure_class) <> ''),
    failure_code TEXT NOT NULL CHECK (btrim(failure_code) <> ''),
    payload_sha256 TEXT NOT NULL CHECK (
        payload_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_config_outbox_operator_log_project
    ON config_outbox_operator_log (project_id, created_at DESC, id DESC);

CREATE TABLE experiment_audit_purge_log (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL CHECK (btrim(project_id) <> ''),
    purge_before TIMESTAMPTZ NOT NULL,
    deleted_rows BIGINT NOT NULL CHECK (deleted_rows >= 0),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_experiment_audit_purge_log_project
    ON experiment_audit_purge_log (project_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.apdl_reject_operator_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $apdl_reject_operator_audit_mutation$
BEGIN
    RAISE EXCEPTION 'operator audit rows are immutable';
END
$apdl_reject_operator_audit_mutation$;

CREATE TRIGGER config_outbox_operator_log_no_update_delete
BEFORE UPDATE OR DELETE ON config_outbox_operator_log
FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();

CREATE TRIGGER config_outbox_operator_log_no_truncate
BEFORE TRUNCATE ON config_outbox_operator_log
FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();

CREATE TRIGGER experiment_audit_purge_log_no_update_delete
BEFORE UPDATE OR DELETE ON experiment_audit_purge_log
FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();

CREATE TRIGGER experiment_audit_purge_log_no_truncate
BEFORE TRUNCATE ON experiment_audit_purge_log
FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();

-- Keep the historical ledger immutable to every login. Only the ungranted
-- NOLOGIN owner of the constrained SECURITY DEFINER function may delete rows;
-- callers receive EXECUTE on that function, never membership in this role.
CREATE OR REPLACE FUNCTION public.apdl_reject_experiment_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $apdl_reject_experiment_audit_mutation$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_user = 'apdl_audit_purge_definer' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'experiment lifecycle audit rows are immutable';
END
$apdl_reject_experiment_audit_mutation$;

DO $apdl_require_new_canonical_purge_function$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.proname = 'apdl_purge_experiment_audit'
    ) THEN
        RAISE EXCEPTION
            'public.apdl_purge_experiment_audit must not have preexisting overloads';
    END IF;
END
$apdl_require_new_canonical_purge_function$;

CREATE FUNCTION public.apdl_purge_experiment_audit(
    p_project_id TEXT,
    p_purge_before TIMESTAMPTZ,
    p_reason TEXT,
    p_confirmation TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $apdl_purge_experiment_audit$
DECLARE
    deleted_count BIGINT;
BEGIN
    IF p_project_id IS NULL
       OR p_project_id !~ '^[A-Za-z0-9]{1,64}$' THEN
        RAISE EXCEPTION 'project_id must match the canonical identifier schema';
    END IF;
    IF p_purge_before IS NULL OR p_purge_before > now() THEN
        RAISE EXCEPTION 'purge_before must not be in the future';
    END IF;
    IF p_reason IS NULL
       OR btrim(p_reason) = ''
       OR length(p_reason) > 500 THEN
        RAISE EXCEPTION 'reason must contain 1 through 500 characters';
    END IF;
    IF p_confirmation IS DISTINCT FROM 'PURGE EXPERIMENT AUDIT' THEN
        RAISE EXCEPTION 'exact purge confirmation is required';
    END IF;

    -- Block concurrent ledger writers while retaining ordinary read access.
    -- The trigger remains enabled throughout; it recognizes only this
    -- function's isolated NOLOGIN owner.
    LOCK TABLE public.experiment_audit_log IN SHARE ROW EXCLUSIVE MODE;

    DELETE FROM public.experiment_audit_log
    WHERE project_id = p_project_id
      AND created_at < p_purge_before;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    INSERT INTO public.experiment_audit_purge_log (
        project_id,
        purge_before,
        deleted_rows,
        actor,
        reason
    )
    VALUES (
        p_project_id,
        p_purge_before,
        deleted_count,
        session_user,
        p_reason
    );

    RETURN deleted_count;
END
$apdl_purge_experiment_audit$;

REVOKE ALL ON FUNCTION public.apdl_purge_experiment_audit(
    TEXT,
    TIMESTAMPTZ,
    TEXT,
    TEXT
) FROM PUBLIC;

DO $apdl_grant_database_connect$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO apdl_runtime',
        current_database()
    );
END
$apdl_grant_database_connect$;

GRANT USAGE ON SCHEMA public TO apdl_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO apdl_runtime;
GRANT USAGE, SELECT
    ON ALL SEQUENCES IN SCHEMA public
    TO apdl_runtime;
GRANT EXECUTE
    ON ALL FUNCTIONS IN SCHEMA public
    TO apdl_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO apdl_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO apdl_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO apdl_runtime;

REVOKE UPDATE, DELETE
    ON config_outbox_operator_log
    FROM apdl_runtime;
REVOKE INSERT, UPDATE, DELETE
    ON experiment_audit_purge_log
    FROM apdl_runtime;
REVOKE UPDATE, DELETE
    ON experiment_audit_log
    FROM apdl_runtime;

GRANT USAGE ON SCHEMA public TO apdl_audit_purge_definer;
GRANT SELECT, DELETE
    ON experiment_audit_log
    TO apdl_audit_purge_definer;
GRANT INSERT
    ON experiment_audit_purge_log
    TO apdl_audit_purge_definer;
GRANT USAGE, SELECT
    ON SEQUENCE experiment_audit_purge_log_id_seq
    TO apdl_audit_purge_definer;
GRANT CREATE ON SCHEMA public TO apdl_audit_purge_definer;
ALTER FUNCTION public.apdl_purge_experiment_audit(
    TEXT,
    TIMESTAMPTZ,
    TEXT,
    TEXT
) OWNER TO apdl_audit_purge_definer;
REVOKE CREATE ON SCHEMA public FROM apdl_audit_purge_definer;

GRANT USAGE ON SCHEMA public TO apdl_audit_operator;
GRANT SELECT
    ON experiment_audit_log, experiment_audit_purge_log
    TO apdl_audit_operator;

REVOKE ALL ON FUNCTION public.apdl_purge_experiment_audit(
    TEXT,
    TIMESTAMPTZ,
    TEXT,
    TEXT
) FROM PUBLIC, apdl_runtime;
GRANT EXECUTE ON FUNCTION public.apdl_purge_experiment_audit(
    TEXT,
    TIMESTAMPTZ,
    TEXT,
    TEXT
) TO apdl_audit_operator;

COMMENT ON TABLE config_outbox_operator_log IS
    'Immutable replay/discard evidence without duplicated outbox payloads';
COMMENT ON TABLE experiment_audit_purge_log IS
    'Immutable evidence for project-scoped experiment snapshot purges';
COMMENT ON FUNCTION public.apdl_purge_experiment_audit(
    TEXT,
    TIMESTAMPTZ,
    TEXT,
    TEXT
) IS
    'Operator-only, project-scoped purge of experiment audit snapshots';

DO $apdl_validate_purge_boundary$
DECLARE
    canonical_function_oid OID;
    definer_role_oid OID;
    operator_role_oid OID;
BEGIN
    canonical_function_oid := pg_catalog.to_regprocedure(
        'public.apdl_purge_experiment_audit(text,timestamptz,text,text)'
    );
    SELECT oid
    INTO definer_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_purge_definer';
    SELECT oid
    INTO operator_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_operator';

    IF canonical_function_oid IS NULL OR (
        SELECT count(*)
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.proname = 'apdl_purge_experiment_audit'
    ) <> 1 THEN
        RAISE EXCEPTION
            'exactly one canonical public.apdl_purge_experiment_audit function is required';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_proc AS procedure
        WHERE procedure.oid = canonical_function_oid
          AND procedure.proowner = definer_role_oid
          AND procedure.prosecdef
          AND procedure.prokind = 'f'
          AND procedure.prolang = (
              SELECT oid
              FROM pg_catalog.pg_language
              WHERE lanname = 'plpgsql'
          )
          AND procedure.prorettype = 'pg_catalog.int8'::pg_catalog.regtype
          AND procedure.proconfig IS NOT DISTINCT FROM
              ARRAY['search_path=pg_catalog']::TEXT[]
    ) THEN
        RAISE EXCEPTION
            'canonical purge function owner, security, language, return type, or search_path is invalid';
    END IF;

    IF (
        SELECT
            count(*) = 2
            AND count(*) FILTER (
                WHERE acl.grantee = definer_role_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND NOT acl.is_grantable
            ) = 1
            AND count(*) FILTER (
                WHERE acl.grantee = operator_role_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND NOT acl.is_grantable
            ) = 1
        FROM pg_catalog.pg_proc AS procedure
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
                procedure.proacl,
                pg_catalog.acldefault('f', procedure.proowner)
            )
        ) AS acl
        WHERE procedure.oid = canonical_function_oid
    ) IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'canonical purge function ACL must grant EXECUTE only to its owner and apdl_audit_operator';
    END IF;

    IF pg_catalog.has_schema_privilege(
        'apdl_runtime',
        'public',
        'CREATE'
    ) OR pg_catalog.has_schema_privilege(
        'apdl_audit_operator',
        'public',
        'CREATE'
    ) OR pg_catalog.has_schema_privilege(
        'apdl_audit_purge_definer',
        'public',
        'CREATE'
    ) THEN
        RAISE EXCEPTION
            'fixed APDL roles must not have effective CREATE on schema public';
    END IF;

    IF NOT pg_catalog.has_schema_privilege(
        'apdl_audit_operator',
        'public',
        'USAGE'
    ) OR NOT pg_catalog.has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'SELECT'
    ) OR NOT pg_catalog.has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'SELECT'
    ) OR pg_catalog.has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'INSERT,UPDATE,DELETE,TRUNCATE'
    ) OR pg_catalog.has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'INSERT,UPDATE,DELETE,TRUNCATE'
    ) THEN
        RAISE EXCEPTION
            'apdl_audit_operator must have only read access to audit evidence';
    END IF;
END
$apdl_validate_purge_boundary$;
