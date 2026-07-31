#!/usr/bin/env bash
# Exact PostgreSQL proof for the canonical operator boundary.
#
# Run only against a disposable database after the canonical migrations. The
# script creates and drops one temporary login role and leaves uniquely named
# audit evidence in the disposable database.

set -Eeuo pipefail

: "${PGHOST:?PGHOST is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${APDL_OWNER_POSTGRES_USER:?APDL_OWNER_POSTGRES_USER is required}"
: "${APDL_OWNER_POSTGRES_PASSWORD:?APDL_OWNER_POSTGRES_PASSWORD is required}"
: "${APDL_RUNTIME_POSTGRES_PASSWORD:?APDL_RUNTIME_POSTGRES_PASSWORD is required}"
: "${APDL_RUNTIME_TEST_POSTGRES_URL:?APDL_RUNTIME_TEST_POSTGRES_URL is required}"

PGPORT="${PGPORT:-5432}"
operator_role="apdl_audit_operator"
definer_role="apdl_audit_purge_definer"
test_actor="apdl_audit_privilege_test"
test_actor_password="audit_test_only_$(date -u +%s)_$$"
test_suffix="$(date -u +%s)$$"
project_a="roleproofa${test_suffix}"
project_b="roleproofb${test_suffix}"
reason="automated privilege boundary proof"
confirmation="PURGE EXPERIMENT AUDIT"

owner_psql() {
    PGPASSWORD="$APDL_OWNER_POSTGRES_PASSWORD" psql \
        --no-psqlrc \
        --set=ON_ERROR_STOP=1 \
        --quiet \
        --tuples-only \
        --no-align \
        --host "$PGHOST" \
        --port "$PGPORT" \
        --username "$APDL_OWNER_POSTGRES_USER" \
        --dbname "$PGDATABASE" \
        "$@"
}

runtime_psql() {
    PGPASSWORD="$APDL_RUNTIME_POSTGRES_PASSWORD" psql \
        --no-psqlrc \
        --set=ON_ERROR_STOP=1 \
        --quiet \
        --tuples-only \
        --no-align \
        --dbname "$APDL_RUNTIME_TEST_POSTGRES_URL" \
        "$@"
}

operator_psql() {
    PGPASSWORD="$test_actor_password" psql \
        --no-psqlrc \
        --set=ON_ERROR_STOP=1 \
        --quiet \
        --tuples-only \
        --no-align \
        --host "$PGHOST" \
        --port "$PGPORT" \
        --username "$test_actor" \
        --dbname "$PGDATABASE" \
        "$@"
}

cleanup() {
    owner_psql -c "DROP ROLE IF EXISTS $test_actor" \
        >/dev/null 2>&1 || true
}
trap cleanup EXIT

expect_failure() {
    local principal="$1"
    local sql="$2"
    local expected_diagnostic="${3:-}"
    local output
    local -a command

    case "$principal" in
        owner) command=(owner_psql) ;;
        runtime) command=(runtime_psql) ;;
        operator) command=(operator_psql) ;;
        *)
            echo "Unknown PostgreSQL test principal: $principal" >&2
            return 1
            ;;
    esac

    if output="$("${command[@]}" -c "$sql" 2>&1)"; then
        echo "Expected $principal statement to fail: $sql" >&2
        return 1
    fi
    [ -n "$output" ] || {
        echo "Failed $principal statement returned no PostgreSQL diagnostic" >&2
        return 1
    }
    if [ -n "$expected_diagnostic" ] \
        && [[ "$output" != *"$expected_diagnostic"* ]]; then
        echo "Unexpected $principal failure diagnostic: $output" >&2
        return 1
    fi
}

owner_psql \
    --set=test_actor_password="$test_actor_password" <<'SQL'
DO $assert_roles$
DECLARE
    canonical_function_oid OID;
    current_database_oid OID;
    definer_role_oid OID;
    operator_role_oid OID;
    runtime_role_oid OID;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'apdl_runtime'
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND NOT rolinherit
          AND rolcanlogin
    ) THEN
        RAISE EXCEPTION 'apdl_runtime is missing or privileged';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'apdl_audit_operator'
          AND NOT rolcanlogin
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND NOT rolinherit
    ) THEN
        RAISE EXCEPTION 'apdl_audit_operator is missing or privileged';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'apdl_audit_purge_definer'
          AND NOT rolcanlogin
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND NOT rolinherit
    ) THEN
        RAISE EXCEPTION 'apdl_audit_purge_definer is missing or privileged';
    END IF;

    SELECT oid
    INTO runtime_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_runtime';
    SELECT oid
    INTO operator_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_operator';
    SELECT oid
    INTO definer_role_oid
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_purge_definer';
    SELECT oid
    INTO current_database_oid
    FROM pg_catalog.pg_database
    WHERE datname = current_database();
    canonical_function_oid := to_regprocedure(
        'public.apdl_purge_experiment_audit(text,timestamptz,text,text)'
    );

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE member IN (
            runtime_role_oid,
            operator_role_oid,
            definer_role_oid
        )
    ) THEN
        RAISE EXCEPTION 'a fixed APDL role is a member of another role';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_auth_members
        WHERE roleid = definer_role_oid
    ) THEN
        RAISE EXCEPTION 'the SECURITY DEFINER role has members';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_database
        WHERE datdba = runtime_role_oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_namespace
        WHERE nspowner = runtime_role_oid
    ) OR EXISTS (
        SELECT 1
        FROM pg_catalog.pg_shdepend
        WHERE refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
          AND refobjid = runtime_role_oid
          AND deptype = 'o'
    ) THEN
        RAISE EXCEPTION 'apdl_runtime owns a database, schema, or object';
    END IF;

    IF canonical_function_oid IS NULL OR (
        SELECT count(*)
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = procedure.pronamespace
        WHERE namespace.nspname = 'public'
          AND procedure.proname = 'apdl_purge_experiment_audit'
    ) <> 1 THEN
        RAISE EXCEPTION 'canonical purge function is missing or overloaded';
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
        RAISE EXCEPTION 'canonical purge function definition is invalid';
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
        RAISE EXCEPTION 'canonical purge function ACL is not exact';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_shdepend AS dependency
        WHERE dependency.refclassid =
              'pg_catalog.pg_authid'::pg_catalog.regclass
          AND dependency.refobjid = definer_role_oid
          AND dependency.deptype = 'o'
          AND NOT (
              dependency.dbid = current_database_oid
              AND dependency.classid =
                  'pg_catalog.pg_proc'::pg_catalog.regclass
              AND dependency.objid = canonical_function_oid
              AND dependency.objsubid = 0
          )
    ) THEN
        RAISE EXCEPTION 'purge definer owns an object other than its function';
    END IF;

    IF has_schema_privilege(
        'apdl_runtime',
        'public',
        'CREATE'
    ) OR has_schema_privilege(
        'apdl_audit_operator',
        'public',
        'CREATE'
    ) OR has_schema_privilege(
        'apdl_audit_purge_definer',
        'public',
        'CREATE'
    ) THEN
        RAISE EXCEPTION 'a fixed APDL role can create objects in public';
    END IF;
    IF NOT has_schema_privilege(
        'apdl_audit_operator',
        'public',
        'USAGE'
    ) OR NOT has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'SELECT'
    ) OR NOT has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'operator caller cannot preview and verify a purge';
    END IF;
    IF has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'INSERT'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'UPDATE'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'DELETE'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'TRUNCATE'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'INSERT'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'UPDATE'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'DELETE'
    ) OR has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_purge_log',
        'TRUNCATE'
    ) THEN
        RAISE EXCEPTION 'operator caller has direct audit mutation privileges';
    END IF;
    IF (
        SELECT pg_get_userbyid(proowner)
        FROM pg_catalog.pg_proc
        WHERE oid = canonical_function_oid
    ) <> 'apdl_audit_purge_definer' THEN
        RAISE EXCEPTION 'purge function is not owned by the NOLOGIN definer';
    END IF;
    IF has_table_privilege(
        'apdl_audit_operator',
        'public.experiment_audit_log',
        'DELETE'
    ) THEN
        RAISE EXCEPTION 'operator caller has direct audit DELETE';
    END IF;
    IF NOT has_table_privilege(
        'apdl_audit_purge_definer',
        'public.experiment_audit_log',
        'DELETE'
    ) THEN
        RAISE EXCEPTION 'purge definer lacks its narrow DELETE privilege';
    END IF;
    IF NOT has_column_privilege(
        'apdl_audit_purge_definer',
        'public.experiment_audit_log',
        'project_id',
        'SELECT'
    ) OR NOT has_column_privilege(
        'apdl_audit_purge_definer',
        'public.experiment_audit_log',
        'created_at',
        'SELECT'
    ) THEN
        RAISE EXCEPTION 'purge definer cannot read its bounded DELETE predicate';
    END IF;
END
$assert_roles$;

SELECT 'DROP ROLE apdl_audit_privilege_test'
WHERE EXISTS (
    SELECT 1
    FROM pg_catalog.pg_roles
    WHERE rolname = 'apdl_audit_privilege_test'
)
\gexec
CREATE ROLE apdl_audit_privilege_test WITH
    LOGIN
    INHERIT
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS
    PASSWORD :'test_actor_password';
GRANT apdl_audit_operator TO apdl_audit_privilege_test;
SQL

runtime_posture="$(
    runtime_psql -c \
        "SELECT session_user,
                current_user,
                rolsuper,
                pg_has_role(session_user, '$operator_role', 'MEMBER'),
                pg_has_role(session_user, '$definer_role', 'MEMBER'),
                (
                    SELECT pg_get_userbyid(datdba) = session_user
                    FROM pg_catalog.pg_database
                    WHERE datname = current_database()
                ),
                (
                    SELECT pg_get_userbyid(relowner) = session_user
                    FROM pg_catalog.pg_class
                    WHERE oid = 'public.experiment_audit_log'::regclass
                )
         FROM pg_catalog.pg_roles
         WHERE rolname = session_user"
)"
if [ "$runtime_posture" != "apdl_runtime|apdl_runtime|f|f|f|f|f" ]; then
    echo "Unexpected apdl_runtime privilege posture: $runtime_posture" >&2
    exit 1
fi

runtime_psql \
    --set=project_a="$project_a" \
    --set=project_b="$project_b" <<'SQL'
INSERT INTO public.experiment_audit_log (
    project_id,
    experiment_key,
    action,
    actor,
    after,
    created_at
)
VALUES
    (
        :'project_a',
        'operator-proof-a',
        'experiment_created',
        'system:operator-boundary-proof',
        '{}'::jsonb,
        now() - interval '2 days'
    ),
    (
        :'project_b',
        'operator-proof-b',
        'experiment_created',
        'system:operator-boundary-proof',
        '{}'::jsonb,
        now() - interval '2 days'
    );
SQL

preview_state="$(
    operator_psql -c \
        "SELECT
             count(*) FILTER (WHERE project_id = '$project_a'),
             count(*) FILTER (WHERE project_id = '$project_b'),
             (
                 SELECT count(*)
                 FROM public.experiment_audit_purge_log
                 WHERE project_id IN ('$project_a', '$project_b')
             )
         FROM public.experiment_audit_log
         WHERE project_id IN ('$project_a', '$project_b')"
)"
if [ "$preview_state" != "1|1|0" ]; then
    echo "Operator could not preview the bounded purge: $preview_state" >&2
    exit 1
fi

expect_failure runtime \
    "DELETE FROM public.experiment_audit_log WHERE project_id = '$project_a'"
expect_failure runtime \
    "SELECT public.apdl_purge_experiment_audit(
        '$project_a',
        now(),
        '$reason',
        '$confirmation'
    )"
expect_failure operator \
    "DELETE FROM public.experiment_audit_log WHERE project_id = '$project_a'"
expect_failure owner \
    "DELETE FROM public.experiment_audit_log WHERE project_id = '$project_b'" \
    "experiment lifecycle audit rows are immutable"
expect_failure operator \
    "SELECT public.apdl_purge_experiment_audit(
        '$project_a',
        now(),
        '$reason',
        'PURGE EXPERIMENT AUDIT NOW'
    )" \
    "exact purge confirmation is required"

# A successful SECURITY DEFINER call remains fully transactional. Force a
# later statement to abort the connection's transaction; disconnect rollback
# must restore both the project row and its purge evidence.
expect_failure operator \
    "BEGIN;
     SELECT public.apdl_purge_experiment_audit(
         '$project_a',
         now(),
         '$reason',
         '$confirmation'
     );
     DO \$forced_failure\$
     BEGIN
         RAISE EXCEPTION 'forced post-purge transaction failure';
     END
     \$forced_failure\$;
     COMMIT;" \
    "forced post-purge transaction failure"

rollback_state="$(
    owner_psql -c \
        "SELECT
             count(*) FILTER (WHERE project_id = '$project_a'),
             count(*) FILTER (WHERE project_id = '$project_b'),
             (
                 SELECT count(*)
                 FROM public.experiment_audit_purge_log
                 WHERE project_id IN ('$project_a', '$project_b')
             ),
             (
                 SELECT tgenabled = 'O'
                 FROM pg_catalog.pg_trigger
                 WHERE tgrelid = 'public.experiment_audit_log'::regclass
                   AND tgname = 'experiment_audit_log_no_update_delete'
                   AND NOT tgisinternal
             )
         FROM public.experiment_audit_log
         WHERE project_id IN ('$project_a', '$project_b')"
)"
if [ "$rollback_state" != "1|1|0|t" ]; then
    echo "Operator purge did not roll back atomically: $rollback_state" >&2
    exit 1
fi

deleted_rows="$(
    operator_psql -c \
        "SELECT public.apdl_purge_experiment_audit(
            '$project_a',
            now(),
            '$reason',
            '$confirmation'
        )"
)"
if [ "$deleted_rows" != "1" ]; then
    echo "Operator purge deleted an unexpected row count: $deleted_rows" >&2
    exit 1
fi

purge_state="$(
    operator_psql -c \
        "SELECT
             count(*) FILTER (WHERE project_id = '$project_a'),
             count(*) FILTER (WHERE project_id = '$project_b'),
             (
                 SELECT count(*)
                 FROM public.experiment_audit_purge_log
                 WHERE project_id = '$project_a'
                   AND deleted_rows = 1
                   AND actor = '$test_actor'
                   AND reason = '$reason'
             )
         FROM public.experiment_audit_log
         WHERE project_id IN ('$project_a', '$project_b')"
)"
if [ "$purge_state" != "0|1|1" ]; then
    echo "Operator purge violated project/audit boundaries: $purge_state" >&2
    exit 1
fi

expect_failure owner \
    "UPDATE public.experiment_audit_purge_log
     SET reason = 'tampered'
     WHERE project_id = '$project_a'"
expect_failure owner \
    "DELETE FROM public.experiment_audit_purge_log
     WHERE project_id = '$project_a'"
expect_failure owner "TRUNCATE public.experiment_audit_purge_log"

echo "PostgreSQL runtime/operator privilege boundary passed"
