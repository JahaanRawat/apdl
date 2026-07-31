-- Canonical PostgreSQL schema baseline for fresh APDL installations.

-- Upgrade and data-repair operations are intentionally absent from this baseline.

--
-- PostgreSQL database dump
--

\restrict VdSGORKEltZNGKU9SKTRyNQeUqdsqzUYYcO2pyQDUKO0cT9ayksKMpq7EUIFsfO

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg12+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: apdl_assert_agents_project_active(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_assert_agents_project_active(candidate_project_id text, authority_context text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM 1
    FROM llm_project_policies AS policy
    WHERE policy.project_id = candidate_project_id
      AND policy.state = 'active'
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = authority_context
                || ' requires active Agents project setup';
    END IF;
END
$$;


--
-- Name: apdl_assert_analysis_table_registry(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_assert_analysis_table_registry() RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    registered RECORD;
    relation_oid REGCLASS;
BEGIN
    FOR registered IN
        SELECT registry.table_name
        FROM apdl_analysis_table_registry AS registry
        ORDER BY registry.table_name
    LOOP
        relation_oid := to_regclass(registered.table_name);
        IF relation_oid IS NULL THEN
            RAISE EXCEPTION
                'registered analysis-bearing table % is missing',
                registered.table_name;
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM pg_trigger AS trigger_record
            WHERE trigger_record.tgrelid = relation_oid
              AND trigger_record.tgname = 'apdl_analysis_project_active'
              AND NOT trigger_record.tgisinternal
              AND trigger_record.tgenabled <> 'D'
        ) THEN
            RAISE EXCEPTION
                'registered analysis-bearing table % is not fenced',
                registered.table_name;
        END IF;
    END LOOP;
END
$$;


--
-- Name: apdl_assert_codegen_model_assignments_current(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_assert_codegen_model_assignments_current(expected_project_id text, expected_provider text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM codegen_project_provider_connections AS connection
        JOIN codegen_project_provider_models AS model
          ON model.project_id = connection.project_id
         AND model.provider = connection.provider
         AND model.connection_version = connection.version
         AND model.inventory_version = connection.inventory_version
         AND model.catalog_version = connection.catalog_version
        WHERE connection.project_id = expected_project_id
          AND connection.provider = expected_provider
          AND connection.state = 'revoked'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Revoked Codegen provider connection must not retain model inventory';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM codegen_project_model_assignments AS assignment
        WHERE assignment.project_id = expected_project_id
          AND assignment.provider = expected_provider
          AND NOT EXISTS (
              SELECT 1
              FROM codegen_project_provider_connections AS connection
              JOIN codegen_project_provider_models AS model
                ON model.project_id = connection.project_id
               AND model.provider = connection.provider
               AND model.connection_version = connection.version
               AND model.inventory_version = connection.inventory_version
               AND model.catalog_version = connection.catalog_version
              WHERE connection.project_id = assignment.project_id
                AND connection.provider = assignment.provider
                AND connection.state = 'active'
                AND model.model_id = assignment.model_id
                AND assignment.role = ANY(model.supported_roles)
                AND assignment.connection_version = connection.version
                AND assignment.inventory_version = connection.inventory_version
                AND assignment.catalog_version = connection.catalog_version
                AND assignment.catalog_version = model.catalog_version
          )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Codegen provider mutation would leave a stale model assignment';
    END IF;
END
$$;


--
-- Name: apdl_assert_execution_project_authorized(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_assert_execution_project_authorized(candidate_project_id text, authority_context text) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM 1
    FROM admin_projects AS project
    WHERE project.project_id = candidate_project_id
    FOR KEY SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = authority_context || ' requires an existing project';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM admin_project_execution_authorizations AS execution_authority
        WHERE execution_authority.project_id = candidate_project_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = authority_context
                || ' requires an operator-provisioned or explicitly authorized project';
    END IF;
END
$$;


--
-- Name: apdl_assert_execution_table_registry(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_assert_execution_table_registry() RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    registered RECORD;
    relation_oid REGCLASS;
BEGIN
    FOR registered IN
        SELECT registry.table_name
        FROM apdl_execution_table_registry AS registry
        ORDER BY registry.table_name
    LOOP
        relation_oid := to_regclass(registered.table_name);
        IF relation_oid IS NULL THEN
            RAISE EXCEPTION
                'registered execution-bearing table % is missing',
                registered.table_name;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_trigger AS trigger_record
            WHERE trigger_record.tgrelid = relation_oid
              AND trigger_record.tgname = 'apdl_execution_project_authorized'
              AND NOT trigger_record.tgisinternal
              AND trigger_record.tgenabled <> 'D'
        ) THEN
            RAISE EXCEPTION
                'registered execution-bearing table % is not fenced',
                registered.table_name;
        END IF;
    END LOOP;
END
$$;


--
-- Name: apdl_authorize_operator_project(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_authorize_operator_project() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO admin_project_execution_authorizations (
        project_id,
        authorization_source,
        actor,
        reason
    )
    VALUES (
        NEW.project_id,
        'operator_provisioned',
        'system:admin_project_insert',
        'Operator-provisioned project'
    )
    ON CONFLICT (project_id) DO NOTHING;
    RETURN NEW;
END
$$;


--
-- Name: apdl_canonical_admin_roles(text[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_canonical_admin_roles(selected_roles text[]) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT
    AS $$
    SELECT COALESCE(
        ARRAY_AGG(allowed.role ORDER BY allowed.position),
        ARRAY[]::TEXT[]
    )
    FROM UNNEST(
        ARRAY[
            'events:write',
            'config:read',
            'config:write',
            'config:evaluate',
            'query:read',
            'agents:read',
            'agents:run',
            'agents:manage',
            'agents:approve',
            'credentials:manage',
            'members:manage'
        ]::TEXT[]
    ) WITH ORDINALITY AS allowed(role, position)
    WHERE allowed.role = ANY(selected_roles)
$$;


--
-- Name: apdl_check_active_agents_setup_trigger(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_check_active_agents_setup_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM apdl_validate_active_agents_setup(
        COALESCE(NEW.project_id, OLD.project_id)
    );
    RETURN NULL;
END
$$;


--
-- Name: apdl_check_llm_vault_connection_authority_trigger(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    candidate_connection_id UUID;
    candidate_credential_id UUID;
BEGIN
    IF TG_TABLE_NAME = 'llm_vault_provider_secrets' THEN
        candidate_credential_id := CASE
            WHEN TG_OP = 'DELETE' THEN OLD.credential_id
            ELSE NEW.credential_id
        END;
        SELECT connection_id
        INTO candidate_connection_id
        FROM llm_vault_provider_credentials
        WHERE credential_id = candidate_credential_id;
    ELSE
        candidate_connection_id := CASE
            WHEN TG_OP = 'DELETE' THEN OLD.connection_id
            ELSE NEW.connection_id
        END;
    END IF;
    IF candidate_connection_id IS NOT NULL THEN
        PERFORM apdl_validate_llm_vault_connection_authority(
            candidate_connection_id
        );
    END IF;
    RETURN NULL;
END
$$;


--
-- Name: apdl_enforce_analysis_table_project(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_analysis_table_project() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM apdl_assert_agents_project_active(NEW.project_id, TG_TABLE_NAME);
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_codegen_llm_attempt_project(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_codegen_llm_attempt_project() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status = 'blocked' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.status IN ('prepared', 'in_flight') THEN
        PERFORM apdl_assert_execution_project_authorized(
            NEW.project_id,
            TG_TABLE_NAME
        );
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_codegen_llm_attempt_transition(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_codegen_llm_attempt_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.status = 'prepared'
       AND NEW.status NOT IN ('in_flight', 'blocked', 'cancelled') THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Invalid transition from prepared Codegen LLM attempt';
    END IF;
    IF OLD.status = 'in_flight'
       AND NEW.status NOT IN ('succeeded', 'failed', 'cancelled') THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Invalid transition from in-flight Codegen LLM attempt';
    END IF;
    IF OLD.status IN ('succeeded', 'failed', 'blocked', 'cancelled') THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Terminal Codegen LLM attempts are immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_execution_roles(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_execution_roles() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF 'agents:approve' = ANY(NEW.roles) THEN
        PERFORM apdl_assert_execution_project_authorized(
            NEW.project_id,
            TG_TABLE_NAME || ' effect role'
        );
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_execution_table_project(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_execution_table_project() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM apdl_assert_execution_project_authorized(
        NEW.project_id,
        TG_TABLE_NAME
    );
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_experiment_archive_lifecycle(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_experiment_archive_lifecycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION
                'only draft experiments may be physically deleted';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.archived_at IS NOT NULL THEN
        RAISE EXCEPTION 'archived experiments are immutable';
    END IF;

    IF NEW.archived_at IS NOT NULL THEN
        IF OLD.status = 'draft' THEN
            RAISE EXCEPTION
                'draft experiments must be deleted instead of archived';
        END IF;
        IF NEW.archived_by IS NULL OR btrim(NEW.archived_by) = '' THEN
            RAISE EXCEPTION 'experiment archive actor is required';
        END IF;
        IF (to_jsonb(NEW) - ARRAY[
                'status', 'end_date', 'archived_at', 'archived_by',
                'version', 'updated_at'
            ]) IS DISTINCT FROM (
                to_jsonb(OLD) - ARRAY[
                    'status', 'end_date', 'archived_at', 'archived_by',
                    'version', 'updated_at'
                ]
            )
           OR NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION
                'experiment archive must preserve the launched contract';
        END IF;
        IF OLD.status IN ('scheduled', 'running') THEN
            IF NEW.status <> 'stopped' THEN
                RAISE EXCEPTION
                    'archiving an open experiment must stop it';
            END IF;
            IF OLD.status = 'scheduled' AND NEW.end_date IS NOT NULL THEN
                RAISE EXCEPTION
                    'archived scheduled experiment must have no analysis window';
            END IF;
            IF OLD.status = 'running'
               AND (
                   NEW.end_date IS NULL
                   OR NEW.end_date <= OLD.start_date
                   OR NEW.end_date > OLD.end_date
               ) THEN
                RAISE EXCEPTION
                    'archived running experiment requires a bounded actual end';
            END IF;
        ELSIF NEW.status IS DISTINCT FROM OLD.status
              OR NEW.end_date IS DISTINCT FROM OLD.end_date THEN
            RAISE EXCEPTION
                'archiving a terminal experiment cannot rewrite its lifecycle';
        END IF;
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_enforce_experiment_enrollment_immutability(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_experiment_enrollment_immutability() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.status <> 'draft'
       AND (
           NEW.bucket_by IS DISTINCT FROM OLD.bucket_by
           OR NEW.traffic_percentage IS DISTINCT FROM OLD.traffic_percentage
           OR NEW.targeting_rules_json IS DISTINCT FROM OLD.targeting_rules_json
           OR NEW.minimum_exposure_config_version IS DISTINCT FROM
              OLD.minimum_exposure_config_version
       ) THEN
        RAISE EXCEPTION
            'experiment enrollment is immutable after draft';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: FUNCTION apdl_enforce_experiment_enrollment_immutability(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.apdl_enforce_experiment_enrollment_immutability() IS 'Rejects enrollment-field changes after draft; paired with experiments_minimum_exposure_version_check, which blocks status-only draft downgrades';


--
-- Name: apdl_enforce_experiment_statistical_plan(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_enforce_experiment_statistical_plan() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.status <> 'draft'
       AND NEW.statistical_plan IS DISTINCT FROM OLD.statistical_plan THEN
        RAISE EXCEPTION 'experiment statistical_plan is immutable after draft';
    END IF;
    IF NEW.status IN ('scheduled', 'running')
       AND public.apdl_experiment_statistical_plan_is_canonical(
            NEW.statistical_plan
       ) IS NOT TRUE THEN
        RAISE EXCEPTION 'scheduled/running experiment requires canonical statistical_plan';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_experiment_condition_is_canonical(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_experiment_condition_is_canonical(condition jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
DECLARE
    operator_value TEXT;
    member JSONB;
BEGIN
    IF jsonb_typeof(condition) IS DISTINCT FROM 'object'
       OR NOT (condition ? 'attribute')
       OR NOT (condition ? 'operator')
       OR jsonb_typeof(condition->'attribute') IS DISTINCT FROM 'string'
       OR char_length(condition->>'attribute') NOT BETWEEN 1 AND 128
       OR jsonb_typeof(condition->'operator') IS DISTINCT FROM 'string' THEN
        RETURN false;
    END IF;

    operator_value := condition->>'operator';
    IF operator_value IN ('exists', 'not_exists') THEN
        RETURN (condition - 'attribute' - 'operator') = '{}'::JSONB;
    END IF;
    IF operator_value NOT IN (
        'equals', 'not_equals', 'gt', 'gte', 'lt', 'lte', 'contains',
        'not_contains', 'starts_with', 'ends_with', 'in', 'not_in'
    )
       OR NOT (condition ? 'value')
       OR (condition - 'attribute' - 'operator' - 'value') <> '{}'::JSONB THEN
        RETURN false;
    END IF;

    IF operator_value IN ('equals', 'not_equals') THEN
        RETURN jsonb_typeof(condition->'value') IN ('boolean', 'number')
            OR (
                jsonb_typeof(condition->'value') = 'string'
                AND char_length(condition->>'value') <= 256
            );
    END IF;
    IF operator_value IN (
        'contains', 'not_contains', 'starts_with', 'ends_with'
    ) THEN
        RETURN jsonb_typeof(condition->'value') = 'string'
            AND char_length(condition->>'value') <= 256;
    END IF;
    IF operator_value IN ('gt', 'gte', 'lt', 'lte') THEN
        IF jsonb_typeof(condition->'value') = 'number' THEN
            RETURN true;
        END IF;
        RETURN jsonb_typeof(condition->'value') = 'string'
            AND char_length(condition->>'value') <= 256
            AND condition->>'value' ~
                '^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$'
            AND (condition->>'value')::DOUBLE PRECISION NOT IN (
                'Infinity'::DOUBLE PRECISION,
                '-Infinity'::DOUBLE PRECISION
            );
    END IF;

    IF jsonb_typeof(condition->'value') IS DISTINCT FROM 'array'
       OR jsonb_array_length(condition->'value') NOT BETWEEN 1 AND 100 THEN
        RETURN false;
    END IF;
    FOR member IN
        SELECT value
        FROM jsonb_array_elements(condition->'value') AS members(value)
    LOOP
        IF jsonb_typeof(member) NOT IN ('boolean', 'number')
           AND NOT (
               jsonb_typeof(member) = 'string'
               AND char_length(member #>> '{}') <= 256
           ) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
END
$_$;


--
-- Name: apdl_experiment_rules_are_canonical(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_experiment_rules_are_canonical(value text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    parsed JSONB;
    rule JSONB;
    condition JSONB;
BEGIN
    parsed := value::JSONB;
    IF jsonb_typeof(parsed) IS DISTINCT FROM 'array'
       OR jsonb_array_length(parsed) > 50 THEN
        RETURN false;
    END IF;
    FOR rule IN SELECT item FROM jsonb_array_elements(parsed) AS rules(item)
    LOOP
        IF jsonb_typeof(rule) IS DISTINCT FROM 'object'
           OR NOT (rule ? 'id')
           OR NOT (rule ? 'name')
           OR NOT (rule ? 'conditions')
           OR (rule - 'id' - 'name' - 'conditions') <> '{}'::JSONB
           OR jsonb_typeof(rule->'id') IS DISTINCT FROM 'string'
           OR char_length(rule->>'id') NOT BETWEEN 1 AND 128
           OR jsonb_typeof(rule->'name') IS DISTINCT FROM 'string'
           OR char_length(rule->>'name') > 256
           OR jsonb_typeof(rule->'conditions') IS DISTINCT FROM 'array'
           OR jsonb_array_length(rule->'conditions') > 20 THEN
            RETURN false;
        END IF;
        FOR condition IN
            SELECT item
            FROM jsonb_array_elements(rule->'conditions') AS conditions(item)
        LOOP
            IF public.apdl_experiment_condition_is_canonical(condition) IS NOT TRUE THEN
                RETURN false;
            END IF;
        END LOOP;
    END LOOP;
    RETURN true;
EXCEPTION
    WHEN invalid_text_representation OR invalid_parameter_value THEN
        RETURN false;
END
$$;


--
-- Name: FUNCTION apdl_experiment_rules_are_canonical(value text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.apdl_experiment_rules_are_canonical(value text) IS 'Validates eligibility-only experiment targeting rules with no rollout field';


--
-- Name: apdl_experiment_statistical_plan_is_canonical(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_experiment_statistical_plan_is_canonical(value jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    baseline NUMERIC;
    effect NUMERIC;
    alpha NUMERIC;
    nominal NUMERIC;
    required_per_arm NUMERIC;
    settlement_seconds NUMERIC;
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'object'
       OR (value
            - 'protocol'
            - 'baseline_conversion_rate'
            - 'minimum_detectable_effect'
            - 'significance_level'
            - 'nominal_power'
            - 'required_sample_size_per_arm'
            - 'data_settlement_seconds') <> '{}'::JSONB
       OR NOT value ?& ARRAY[
            'protocol',
            'baseline_conversion_rate',
            'minimum_detectable_effect',
            'significance_level',
            'nominal_power',
            'required_sample_size_per_arm',
            'data_settlement_seconds'
       ] THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(value->'protocol') IS DISTINCT FROM 'string'
       OR value->>'protocol' <> 'fixed_horizon_fisher_newcombe_cc_plan_v1' THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(value->'baseline_conversion_rate') IS DISTINCT FROM 'number'
       OR jsonb_typeof(value->'minimum_detectable_effect') IS DISTINCT FROM 'number'
       OR jsonb_typeof(value->'significance_level') IS DISTINCT FROM 'number'
       OR jsonb_typeof(value->'nominal_power') IS DISTINCT FROM 'number'
       OR jsonb_typeof(value->'required_sample_size_per_arm') IS DISTINCT FROM 'number'
       OR jsonb_typeof(value->'data_settlement_seconds') IS DISTINCT FROM 'number' THEN
        RETURN false;
    END IF;

    baseline := (value->>'baseline_conversion_rate')::NUMERIC;
    effect := (value->>'minimum_detectable_effect')::NUMERIC;
    alpha := (value->>'significance_level')::NUMERIC;
    nominal := (value->>'nominal_power')::NUMERIC;
    required_per_arm := (value->>'required_sample_size_per_arm')::NUMERIC;
    settlement_seconds := (value->>'data_settlement_seconds')::NUMERIC;
    RETURN baseline BETWEEN 0 AND 1
       AND effect BETWEEN 0.000001 AND 1
       AND alpha BETWEEN 0.000001 AND 0.5
       AND nominal > 0.5 AND nominal <= 0.9999
       AND required_per_arm BETWEEN 2 AND 10000000
       AND required_per_arm = trunc(required_per_arm)
       AND settlement_seconds BETWEEN 1 AND 86400
       AND settlement_seconds = trunc(settlement_seconds);
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
END
$$;


--
-- Name: apdl_experiment_variants_are_canonical(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_experiment_variants_are_canonical(variants_value text, default_key text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
DECLARE
    parsed JSONB;
    variant JSONB;
    variant_key TEXT;
    variant_weight NUMERIC;
    total_weight NUMERIC := 0;
    observed_keys TEXT[] := ARRAY[]::TEXT[];
    default_observed BOOLEAN := false;
BEGIN
    parsed := variants_value::JSONB;
    IF jsonb_typeof(parsed) IS DISTINCT FROM 'array'
       OR jsonb_array_length(parsed) < 2
       OR jsonb_array_length(parsed) > 10 THEN
        RETURN false;
    END IF;

    FOR variant IN
        SELECT item
        FROM jsonb_array_elements(parsed) AS items(item)
    LOOP
        IF jsonb_typeof(variant) IS DISTINCT FROM 'object'
           OR NOT (variant ? 'key')
           OR NOT (variant ? 'weight')
           OR (variant - 'key' - 'weight' - 'description') <> '{}'::JSONB
           OR jsonb_typeof(variant->'key') IS DISTINCT FROM 'string'
           OR char_length(variant->>'key') NOT BETWEEN 1 AND 128
           OR jsonb_typeof(variant->'weight') IS DISTINCT FROM 'number'
           OR (variant->>'weight') !~ '^[1-9][0-9]*$'
           OR (
               variant ? 'description'
               AND jsonb_typeof(variant->'description')
                   IS DISTINCT FROM 'string'
           ) THEN
            RETURN false;
        END IF;

        variant_key := variant->>'key';
        IF variant_key = ANY(observed_keys) THEN
            RETURN false;
        END IF;
        observed_keys := array_append(observed_keys, variant_key);
        default_observed := default_observed OR variant_key = default_key;

        variant_weight := (variant->>'weight')::NUMERIC;
        IF variant_weight > 9007199254740991 THEN
            RETURN false;
        END IF;
        total_weight := total_weight + variant_weight;
        IF total_weight > 9007199254740991 THEN
            RETURN false;
        END IF;
    END LOOP;

    RETURN total_weight > 0 AND default_observed;
EXCEPTION
    WHEN invalid_parameter_value
       OR invalid_text_representation
       OR numeric_value_out_of_range THEN
        RETURN false;
END
$_$;


--
-- Name: FUNCTION apdl_experiment_variants_are_canonical(variants_value text, default_key text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.apdl_experiment_variants_are_canonical(variants_value text, default_key text) IS 'Enforces the authored experiment variant shape and exact safe-integer bounds';


--
-- Name: apdl_flag_rollouts_are_canonical(jsonb, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_flag_rollouts_are_canonical(rules_value jsonb, fallthrough_value jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
BEGIN
    IF public.apdl_rules_rollouts_are_canonical(rules_value) IS NOT TRUE THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(fallthrough_value) IS DISTINCT FROM 'object'
       OR NOT (fallthrough_value ? 'rollout')
       OR (fallthrough_value - 'rollout') <> '{}'::JSONB THEN
        RETURN false;
    END IF;
    RETURN public.apdl_rollout_is_canonical(
        fallthrough_value->'rollout'
    ) IS TRUE;
EXCEPTION
    WHEN invalid_parameter_value THEN
        RETURN false;
END
$$;


--
-- Name: apdl_flag_variants_are_canonical(jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_flag_variants_are_canonical(variants_value jsonb, default_key text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
DECLARE
    variant JSONB;
    variant_key TEXT;
    variant_weight NUMERIC;
    total_weight NUMERIC := 0;
    observed_keys TEXT[] := ARRAY[]::TEXT[];
    default_observed BOOLEAN := false;
BEGIN
    IF jsonb_typeof(variants_value) IS DISTINCT FROM 'array'
       OR jsonb_array_length(variants_value) < 1
       OR jsonb_array_length(variants_value) > 10 THEN
        RETURN false;
    END IF;

    FOR variant IN
        SELECT item
        FROM jsonb_array_elements(variants_value) AS items(item)
    LOOP
        IF jsonb_typeof(variant) IS DISTINCT FROM 'object'
           OR NOT (variant ? 'key')
           OR NOT (variant ? 'weight')
           OR (variant - 'key' - 'weight') <> '{}'::JSONB
           OR jsonb_typeof(variant->'key') IS DISTINCT FROM 'string'
           OR char_length(variant->>'key') NOT BETWEEN 1 AND 128
           OR jsonb_typeof(variant->'weight') IS DISTINCT FROM 'number'
           OR (variant->>'weight') !~ '^(0|[1-9][0-9]*)$' THEN
            RETURN false;
        END IF;

        variant_key := variant->>'key';
        IF variant_key = ANY(observed_keys) THEN
            RETURN false;
        END IF;
        observed_keys := array_append(observed_keys, variant_key);
        default_observed := default_observed OR variant_key = default_key;

        variant_weight := (variant->>'weight')::NUMERIC;
        IF variant_weight > 9007199254740991 THEN
            RETURN false;
        END IF;
        total_weight := total_weight + variant_weight;
        IF total_weight > 9007199254740991 THEN
            RETURN false;
        END IF;
    END LOOP;

    RETURN total_weight > 0 AND default_observed;
EXCEPTION
    WHEN invalid_parameter_value
       OR invalid_text_representation
       OR numeric_value_out_of_range THEN
        RETURN false;
END
$_$;


--
-- Name: FUNCTION apdl_flag_variants_are_canonical(variants_value jsonb, default_key text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.apdl_flag_variants_are_canonical(variants_value jsonb, default_key text) IS 'Enforces at most ten unique variants with exact safe-integer weights and total';


--
-- Name: apdl_guard_agent_execution_lane_release(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_guard_agent_execution_lane_release() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.status IN (
        'completed',
        'completed_with_errors',
        'failed',
        'cancelled',
        'manual_intervention'
    ) AND EXISTS (
        SELECT 1
        FROM agent_approval_effects AS effect
        WHERE effect.run_id = OLD.run_id
          AND effect.project_id = OLD.project_id
          AND effect.status IN ('queued', 'processing', 'retryable_failed')
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'agent run %s cannot release project %s execution lane while approval effects are live',
                OLD.run_id,
                OLD.project_id
            ),
            HINT = 'Drain or reconcile every approval effect before terminalizing the run.';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_guard_agent_live_effect_lane(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_guard_agent_live_effect_lane() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    lane_project_id TEXT;
BEGIN
    SELECT run.execution_lane_project_id
    INTO lane_project_id
    FROM agent_runs AS run
    WHERE run.run_id = NEW.run_id
      AND run.project_id = NEW.project_id
    FOR UPDATE;

    IF NOT FOUND OR lane_project_id IS DISTINCT FROM NEW.project_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = format(
                'approval effect %s cannot become live without an active project execution lane',
                NEW.effect_id
            ),
            HINT = 'Create or restore the owning run lane before queuing approval work.';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_is_codegen_llm_assignment_snapshot(jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_codegen_llm_assignment_snapshot(assignment jsonb, expected_role text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(assignment) = 'object'
        AND assignment ?& ARRAY[
            'schema_version', 'role', 'provider', 'model_id',
            'assignment_version', 'connection_version', 'inventory_version',
            'catalog_version', 'context_window_tokens',
            'supports_tool_calling', 'supports_structured_output',
            'input_cost_per_million_tokens_usd_micros',
            'output_cost_per_million_tokens_usd_micros'
        ]
        AND assignment - ARRAY[
            'schema_version', 'role', 'provider', 'model_id',
            'assignment_version', 'connection_version', 'inventory_version',
            'catalog_version', 'context_window_tokens',
            'supports_tool_calling', 'supports_structured_output',
            'input_cost_per_million_tokens_usd_micros',
            'output_cost_per_million_tokens_usd_micros'
        ] = '{}'::JSONB
        AND jsonb_typeof(assignment->'schema_version') = 'string'
        AND assignment->>'schema_version'
            = 'codegen_llm_assignment_snapshot@1'
        AND jsonb_typeof(assignment->'role') = 'string'
        AND assignment->>'role' = expected_role
        AND jsonb_typeof(assignment->'provider') = 'string'
        AND assignment->>'provider'
            IN ('anthropic', 'openai', 'google', 'xai')
        AND jsonb_typeof(assignment->'model_id') = 'string'
        AND assignment->>'model_id'
            ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        AND jsonb_typeof(assignment->'assignment_version') = 'number'
        AND assignment->>'assignment_version' ~ '^[1-9][0-9]*$'
        AND (assignment->>'assignment_version')::BIGINT > 0
        AND jsonb_typeof(assignment->'connection_version') = 'number'
        AND assignment->>'connection_version' ~ '^[1-9][0-9]*$'
        AND (assignment->>'connection_version')::BIGINT > 0
        AND jsonb_typeof(assignment->'inventory_version') = 'number'
        AND assignment->>'inventory_version' ~ '^[1-9][0-9]*$'
        AND (assignment->>'inventory_version')::BIGINT > 0
        AND jsonb_typeof(assignment->'catalog_version') = 'string'
        AND assignment->>'catalog_version'
            ~ '^codegen-provider-catalog@[1-9][0-9]*$'
        AND jsonb_typeof(assignment->'context_window_tokens') = 'number'
        AND assignment->>'context_window_tokens' ~ '^[1-9][0-9]*$'
        AND (assignment->>'context_window_tokens')::BIGINT >= 16000
        AND jsonb_typeof(assignment->'supports_tool_calling') = 'boolean'
        AND jsonb_typeof(assignment->'supports_structured_output') = 'boolean'
        AND jsonb_typeof(
            assignment->'input_cost_per_million_tokens_usd_micros'
        ) = 'number'
        AND (
            assignment->>'input_cost_per_million_tokens_usd_micros'
        ) ~ '^(0|[1-9][0-9]*)$'
        AND (
            assignment->>'input_cost_per_million_tokens_usd_micros'
        )::BIGINT >= 0
        AND jsonb_typeof(
            assignment->'output_cost_per_million_tokens_usd_micros'
        ) = 'number'
        AND (
            assignment->>'output_cost_per_million_tokens_usd_micros'
        ) ~ '^(0|[1-9][0-9]*)$'
        AND (
            assignment->>'output_cost_per_million_tokens_usd_micros'
        )::BIGINT >= 0
    ) IS TRUE
$_$;


--
-- Name: apdl_is_codegen_llm_execution_snapshot(jsonb, text, text, bigint, bigint, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_codegen_llm_execution_snapshot(snapshot jsonb, expected_project_id text, expected_grant_id text, expected_repository_id bigint, expected_installation_id bigint, expected_repository_full_name text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(snapshot) = 'object'
        AND snapshot ?& ARRAY[
            'schema_version', 'project_id', 'repository_grant_id',
            'repository_id', 'repository_installation_id',
            'repository_full_name', 'codegen_revision',
            'behavior_configuration_sha256', 'rollout_stage', 'assignments'
        ]::TEXT[]
        AND snapshot - ARRAY[
            'schema_version', 'project_id', 'repository_grant_id',
            'repository_id', 'repository_installation_id',
            'repository_full_name', 'codegen_revision',
            'behavior_configuration_sha256', 'rollout_stage', 'assignments'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(snapshot->'schema_version') = 'string'
        AND snapshot->>'schema_version'
            = 'codegen_llm_execution_snapshot@2'
        AND jsonb_typeof(snapshot->'project_id') = 'string'
        AND snapshot->>'project_id' = expected_project_id
        AND jsonb_typeof(snapshot->'repository_grant_id') = 'string'
        AND LENGTH(snapshot->>'repository_grant_id') BETWEEN 5 AND 132
        AND snapshot->>'repository_grant_id' ~ '^ghg_[A-Za-z0-9_-]+$'
        AND snapshot->>'repository_grant_id' = expected_grant_id
        AND jsonb_typeof(snapshot->'repository_id') = 'number'
        AND snapshot->>'repository_id' ~ '^[1-9][0-9]*$'
        AND (snapshot->>'repository_id')::BIGINT = expected_repository_id
        AND jsonb_typeof(snapshot->'repository_installation_id') = 'number'
        AND snapshot->>'repository_installation_id' ~ '^[1-9][0-9]*$'
        AND (snapshot->>'repository_installation_id')::BIGINT
            = expected_installation_id
        AND jsonb_typeof(snapshot->'repository_full_name') = 'string'
        AND LENGTH(snapshot->>'repository_full_name') BETWEEN 3 AND 201
        AND snapshot->>'repository_full_name' = expected_repository_full_name
        AND jsonb_typeof(snapshot->'codegen_revision') = 'string'
        AND LENGTH(snapshot->>'codegen_revision') BETWEEN 1 AND 200
        AND snapshot->>'codegen_revision'
            = BTRIM(snapshot->>'codegen_revision')
        AND jsonb_typeof(snapshot->'behavior_configuration_sha256') = 'string'
        AND snapshot->>'behavior_configuration_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(snapshot->'rollout_stage') = 'string'
        AND snapshot->>'rollout_stage' IN (
            'offline', 'development_pr', 'tenant_draft_pr'
        )
        AND jsonb_typeof(snapshot->'assignments') = 'array'
        AND jsonb_array_length(snapshot->'assignments') = 2
        AND apdl_is_codegen_llm_assignment_snapshot(
            snapshot->'assignments'->0, 'editor'
        )
        AND apdl_is_codegen_llm_assignment_snapshot(
            snapshot->'assignments'->1, 'helper'
        )
    ) IS TRUE
$_$;


--
-- Name: apdl_is_development_publication_authorization(jsonb, jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_development_publication_authorization(document jsonb, expected_snapshot jsonb, expected_risk text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(document) = 'object'
        AND document ?& ARRAY[
            'schema_version', 'authority', 'request', 'decision',
            'draft_only', 'authorization_sha256'
        ]::TEXT[]
        AND document - ARRAY[
            'schema_version', 'authority', 'request', 'decision',
            'draft_only', 'authorization_sha256'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(document->'schema_version') = 'string'
        AND document->>'schema_version'
            = 'development_publication_authorization@1'
        AND jsonb_typeof(document->'authority') = 'string'
        AND document->>'authority' = 'local_development'
        AND document->'draft_only' = 'true'::JSONB
        AND jsonb_typeof(document->'authorization_sha256') = 'string'
        AND document->>'authorization_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(expected_snapshot) = 'object'
        AND expected_snapshot->>'schema_version'
            = 'codegen_llm_execution_snapshot@2'
        AND expected_snapshot->>'rollout_stage' = 'development_pr'
        AND expected_snapshot->>'codegen_revision' = 'local-development'
        AND jsonb_typeof(document->'request') = 'object'
        AND document->'request' ?& ARRAY[
            'schema_version', 'requested_stage', 'risk', 'model',
            'codegen_revision'
        ]::TEXT[]
        AND (document->'request') - ARRAY[
            'schema_version', 'requested_stage', 'risk', 'model',
            'codegen_revision'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(document->'request'->'schema_version') = 'string'
        AND document->'request'->>'schema_version'
            = 'development_publication_request@1'
        AND jsonb_typeof(document->'request'->'requested_stage') = 'string'
        AND document->'request'->>'requested_stage' = 'development_pr'
        AND jsonb_typeof(document->'request'->'risk') = 'string'
        AND document->'request'->>'risk' = expected_risk
        AND jsonb_typeof(document->'request'->'codegen_revision') = 'string'
        AND document->'request'->>'codegen_revision' = 'local-development'
        AND document->'request'->>'codegen_revision'
            = expected_snapshot->>'codegen_revision'
        AND jsonb_typeof(document->'request'->'model') = 'string'
        AND LENGTH(document->'request'->>'model') > 0
        AND document->'request'->>'model' = (
            CASE (expected_snapshot->'assignments'->0->>'provider')
                WHEN 'anthropic' THEN 'anthropic/'
                WHEN 'openai' THEN 'openai/'
                WHEN 'google' THEN 'gemini/'
                WHEN 'xai' THEN 'xai/'
                ELSE NULL
            END
            || (expected_snapshot->'assignments'->0->>'model_id')
        )
        AND jsonb_typeof(document->'decision') = 'object'
        AND document->'decision' ?& ARRAY[
            'schema_version', 'requested_stage', 'risk', 'allowed',
            'publish_branch', 'create_pull_request', 'ready_for_review',
            'reasons', 'decision_sha256'
        ]::TEXT[]
        AND (document->'decision') - ARRAY[
            'schema_version', 'requested_stage', 'risk', 'allowed',
            'publish_branch', 'create_pull_request', 'ready_for_review',
            'reasons', 'decision_sha256'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(document->'decision'->'schema_version') = 'string'
        AND document->'decision'->>'schema_version'
            = 'development_publication_decision@1'
        AND jsonb_typeof(document->'decision'->'requested_stage') = 'string'
        AND document->'decision'->>'requested_stage' = 'development_pr'
        AND jsonb_typeof(document->'decision'->'risk') = 'string'
        AND document->'decision'->>'risk' = expected_risk
        AND document->'decision'->'allowed' = 'true'::JSONB
        AND document->'decision'->'publish_branch' = 'true'::JSONB
        AND document->'decision'->'create_pull_request' = 'true'::JSONB
        AND document->'decision'->'ready_for_review' = 'false'::JSONB
        AND document->'decision'->'reasons' = '[]'::JSONB
        AND jsonb_typeof(document->'decision'->'decision_sha256') = 'string'
        AND document->'decision'->>'decision_sha256' ~ '^[0-9a-f]{64}$'
    ) IS TRUE
$_$;


--
-- Name: apdl_is_tenant_publication_decision(jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_tenant_publication_decision(decision jsonb, expected_risk text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(decision) = 'object'
        AND decision ?& ARRAY[
            'schema_version', 'requested_stage', 'risk', 'allowed',
            'publish_branch', 'create_pull_request', 'ready_for_review',
            'reasons', 'decision_sha256'
        ]::TEXT[]
        AND decision - ARRAY[
            'schema_version', 'requested_stage', 'risk', 'allowed',
            'publish_branch', 'create_pull_request', 'ready_for_review',
            'reasons', 'decision_sha256'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(decision->'schema_version') = 'string'
        AND decision->>'schema_version' = 'tenant_publication_decision@1'
        AND jsonb_typeof(decision->'requested_stage') = 'string'
        AND decision->>'requested_stage' = 'tenant_draft_pr'
        AND jsonb_typeof(decision->'risk') = 'string'
        AND decision->>'risk' = expected_risk
        AND decision->'allowed' = 'true'::JSONB
        AND decision->'publish_branch' = 'true'::JSONB
        AND decision->'create_pull_request' = 'true'::JSONB
        AND decision->'ready_for_review' = 'false'::JSONB
        AND decision->'reasons' = '[]'::JSONB
        AND jsonb_typeof(decision->'decision_sha256') = 'string'
        AND decision->>'decision_sha256' ~ '^[0-9a-f]{64}$'
    ) IS TRUE
$_$;


--
-- Name: apdl_is_tenant_publication_request(jsonb, jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_tenant_publication_request(request jsonb, expected_snapshot jsonb, expected_risk text) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(request) = 'object'
        AND request ?& ARRAY[
            'schema_version', 'requested_stage', 'risk',
            'execution_snapshot', 'execution_snapshot_sha256',
            'runtime_identity'
        ]::TEXT[]
        AND request - ARRAY[
            'schema_version', 'requested_stage', 'risk',
            'execution_snapshot', 'execution_snapshot_sha256',
            'runtime_identity'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(request->'schema_version') = 'string'
        AND request->>'schema_version' = 'tenant_publication_request@1'
        AND jsonb_typeof(request->'requested_stage') = 'string'
        AND request->>'requested_stage' = 'tenant_draft_pr'
        AND jsonb_typeof(request->'risk') = 'string'
        AND request->>'risk' = expected_risk
        AND request->'execution_snapshot' = expected_snapshot
        AND request->'execution_snapshot'->>'rollout_stage' = 'tenant_draft_pr'
        AND jsonb_typeof(request->'execution_snapshot_sha256') = 'string'
        AND request->>'execution_snapshot_sha256' ~ '^[0-9a-f]{64}$'
        AND apdl_is_tenant_publication_runtime_identity(
            request->'runtime_identity'
        )
        AND request->'runtime_identity'->>'codegen_revision'
            = expected_snapshot->>'codegen_revision'
        AND request->'runtime_identity'->>'behavior_configuration_sha256'
            = expected_snapshot->>'behavior_configuration_sha256'
    ) IS TRUE
$_$;


--
-- Name: apdl_is_tenant_publication_runtime_identity(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_is_tenant_publication_runtime_identity(identity jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (
        jsonb_typeof(identity) = 'object'
        AND identity ?& ARRAY[
            'schema_version', 'controller_image_id', 'worker_image_id',
            'codegen_revision', 'behavior_configuration_sha256',
            'egress_policy_sha256', 'egress_proxy_image_id',
            'egress_transport', 'max_concurrent_jobs', 'identity_sha256'
        ]::TEXT[]
        AND identity - ARRAY[
            'schema_version', 'controller_image_id', 'worker_image_id',
            'codegen_revision', 'behavior_configuration_sha256',
            'egress_policy_sha256', 'egress_proxy_image_id',
            'egress_transport', 'max_concurrent_jobs', 'identity_sha256'
        ]::TEXT[] = '{}'::JSONB
        AND jsonb_typeof(identity->'schema_version') = 'string'
        AND identity->>'schema_version'
            = 'tenant_publication_runtime_identity@1'
        AND jsonb_typeof(identity->'controller_image_id') = 'string'
        AND identity->>'controller_image_id' ~ '^sha256:[0-9a-f]{64}$'
        AND jsonb_typeof(identity->'worker_image_id') = 'string'
        AND identity->>'worker_image_id' ~ '^sha256:[0-9a-f]{64}$'
        AND jsonb_typeof(identity->'codegen_revision') = 'string'
        AND LENGTH(identity->>'codegen_revision') BETWEEN 1 AND 200
        AND identity->>'codegen_revision'
            = BTRIM(identity->>'codegen_revision')
        AND jsonb_typeof(identity->'behavior_configuration_sha256') = 'string'
        AND identity->>'behavior_configuration_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(identity->'egress_policy_sha256') = 'string'
        AND identity->>'egress_policy_sha256' ~ '^[0-9a-f]{64}$'
        AND jsonb_typeof(identity->'egress_proxy_image_id') = 'string'
        AND identity->>'egress_proxy_image_id' ~ '^sha256:[0-9a-f]{64}$'
        AND jsonb_typeof(identity->'egress_transport') = 'string'
        AND identity->>'egress_transport' = 'network_none_unix_socket@1'
        AND identity->'max_concurrent_jobs' = '1'::JSONB
        AND jsonb_typeof(identity->'identity_sha256') = 'string'
        AND identity->>'identity_sha256' ~ '^[0-9a-f]{64}$'
    ) IS TRUE
$_$;


--
-- Name: apdl_llm_vault_has_management_authority(text, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_llm_vault_has_management_authority(candidate_project_id text, candidate_actor_user_id uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    project_owner_user_id UUID;
    actor_active BOOLEAN;
    actor_roles TEXT[];
BEGIN
    SELECT project.owner_user_id, account.active
    INTO project_owner_user_id, actor_active
    FROM public.admin_projects AS project
    JOIN public.admin_users AS account
      ON account.user_id = candidate_actor_user_id
    WHERE project.project_id = candidate_project_id
    FOR KEY SHARE OF project, account;

    IF NOT FOUND OR actor_active IS NOT TRUE THEN
        RETURN FALSE;
    END IF;
    IF project_owner_user_id = candidate_actor_user_id THEN
        RETURN TRUE;
    END IF;

    SELECT membership.roles
    INTO actor_roles
    FROM public.admin_user_projects AS membership
    WHERE membership.project_id = candidate_project_id
      AND membership.user_id = candidate_actor_user_id
    FOR KEY SHARE;

    RETURN COALESCE(
        actor_roles @> ARRAY['agents:manage', 'credentials:manage']::TEXT[],
        FALSE
    );
END
$$;


--
-- Name: apdl_protect_active_project_owner(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_active_project_owner() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.active AND NOT NEW.active AND EXISTS (
        SELECT 1
        FROM admin_projects
        WHERE owner_user_id = OLD.user_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project owner account must remain active';
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: apdl_protect_codegen_llm_attempt_identity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_codegen_llm_attempt_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (
        NEW.project_id,
        NEW.changeset_id,
        NEW.phase,
        NEW.role,
        NEW.attempt_sequence,
        NEW.provider,
        NEW.model_id,
        NEW.assignment_version,
        NEW.credential_id,
        NEW.credential_version
    ) IS DISTINCT FROM (
        OLD.project_id,
        OLD.changeset_id,
        OLD.phase,
        OLD.role,
        OLD.attempt_sequence,
        OLD.provider,
        OLD.model_id,
        OLD.assignment_version,
        OLD.credential_id,
        OLD.credential_version
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Codegen LLM attempt identity is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_protect_codegen_llm_snapshot(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_codegen_llm_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.llm_execution_snapshot IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'New Codegen changesets require an LLM execution snapshot';
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.llm_execution_snapshot IS DISTINCT FROM OLD.llm_execution_snapshot THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Codegen LLM execution snapshot is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_protect_llm_attempt_credential_binding(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_llm_attempt_credential_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.credential_id IS DISTINCT FROM OLD.credential_id
        OR NEW.credential_version IS DISTINCT FROM OLD.credential_version
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'LLM attempt credential binding is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_protect_llm_attempt_setup_binding(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_llm_attempt_setup_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.setup_version IS DISTINCT FROM OLD.setup_version
        OR NEW.model_tier IS DISTINCT FROM OLD.model_tier
        OR NEW.connection_version IS DISTINCT FROM OLD.connection_version
        OR NEW.inventory_version IS DISTINCT FROM OLD.inventory_version
        OR NEW.model_catalog_version IS DISTINCT FROM OLD.model_catalog_version
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'LLM attempt setup binding is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_protect_project_owner_membership(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_protect_project_owner_membership() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (
            SELECT 1
            FROM admin_projects
            WHERE project_id = OLD.project_id
              AND owner_user_id = OLD.user_id
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'project owner membership and members:manage role are required';
        END IF;
        RETURN OLD;
    END IF;

    IF (
        NEW.project_id <> OLD.project_id
        OR NEW.user_id <> OLD.user_id
        OR NOT ('members:manage' = ANY(NEW.roles))
    ) AND EXISTS (
        SELECT 1
        FROM admin_projects
        WHERE project_id = OLD.project_id
          AND owner_user_id = OLD.user_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project owner membership and members:manage role are required';
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: apdl_purge_experiment_audit(text, timestamp with time zone, text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_purge_experiment_audit(p_project_id text, p_purge_before timestamp with time zone, p_reason text, p_confirmation text) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
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
$_$;


--
-- Name: FUNCTION apdl_purge_experiment_audit(p_project_id text, p_purge_before timestamp with time zone, p_reason text, p_confirmation text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.apdl_purge_experiment_audit(p_project_id text, p_purge_before timestamp with time zone, p_reason text, p_confirmation text) IS 'Operator-only, project-scoped purge of experiment audit snapshots';


--
-- Name: apdl_register_analysis_table(regclass); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_register_analysis_table(target_table regclass) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_schema TEXT;
    target_name TEXT;
    project_column_valid BOOLEAN;
    qualified_name TEXT;
BEGIN
    SELECT namespace.nspname, relation.relname
    INTO target_schema, target_name
    FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE relation.oid = target_table
      AND relation.relkind IN ('r', 'p');

    IF NOT FOUND OR target_schema <> 'public' THEN
        RAISE EXCEPTION
            'analysis-bearing table must be a public base or partitioned table';
    END IF;

    SELECT attribute.attnotnull
           AND attribute.atttypid = 'text'::regtype
    INTO project_column_valid
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = target_table
      AND attribute.attname = 'project_id'
      AND NOT attribute.attisdropped;

    IF project_column_valid IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'analysis-bearing table %.% requires a non-null TEXT project_id',
            target_schema,
            target_name;
    END IF;

    qualified_name := format('%I.%I', target_schema, target_name);
    EXECUTE format(
        'DROP TRIGGER IF EXISTS apdl_analysis_project_active ON %s',
        qualified_name
    );
    EXECUTE format(
        'CREATE TRIGGER apdl_analysis_project_active '
        'BEFORE INSERT OR UPDATE OF project_id ON %s '
        'FOR EACH ROW EXECUTE FUNCTION '
        'apdl_enforce_analysis_table_project()',
        qualified_name
    );

    INSERT INTO apdl_analysis_table_registry (table_name)
    VALUES ('public.' || target_name)
    ON CONFLICT (table_name) DO NOTHING;
END
$$;


--
-- Name: apdl_register_execution_table(regclass); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_register_execution_table(target_table regclass) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_schema TEXT;
    target_name TEXT;
    project_column_valid BOOLEAN;
    qualified_name TEXT;
BEGIN
    SELECT namespace.nspname, relation.relname
    INTO target_schema, target_name
    FROM pg_class AS relation
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE relation.oid = target_table
      AND relation.relkind IN ('r', 'p');

    IF NOT FOUND OR target_schema <> 'public' THEN
        RAISE EXCEPTION
            'execution-bearing table must be a public base or partitioned table';
    END IF;

    SELECT attribute.attnotnull
           AND attribute.atttypid = 'text'::regtype
    INTO project_column_valid
    FROM pg_attribute AS attribute
    WHERE attribute.attrelid = target_table
      AND attribute.attname = 'project_id'
      AND NOT attribute.attisdropped;

    IF project_column_valid IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'execution-bearing table %.% requires a non-null TEXT project_id',
            target_schema,
            target_name;
    END IF;

    qualified_name := format('%I.%I', target_schema, target_name);
    EXECUTE format(
        'DROP TRIGGER IF EXISTS apdl_execution_project_authorized ON %s',
        qualified_name
    );
    EXECUTE format(
        'CREATE TRIGGER apdl_execution_project_authorized '
        'BEFORE INSERT OR UPDATE OF project_id ON %s '
        'FOR EACH ROW EXECUTE FUNCTION '
        'apdl_enforce_execution_table_project()',
        qualified_name
    );

    INSERT INTO apdl_execution_table_registry (table_name)
    VALUES ('public.' || target_name)
    ON CONFLICT (table_name) DO NOTHING;
END
$$;


--
-- Name: apdl_reject_codegen_connection_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_codegen_connection_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'Codegen provider connection audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_codegen_credential_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_codegen_credential_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'Codegen provider credential audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_execution_authorization_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_execution_authorization_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'project execution authorizations are immutable';
END
$$;


--
-- Name: apdl_reject_experiment_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_experiment_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_user = 'apdl_audit_purge_definer' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'experiment lifecycle audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_llm_connection_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_llm_connection_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'LLM provider connection audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_llm_credential_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_llm_credential_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'LLM provider credential audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_llm_policy_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_llm_policy_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'LLM project policy audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_llm_setup_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_llm_setup_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'Agents project setup audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_llm_vault_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_llm_vault_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'LLM vault audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_managed_credential_history_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_managed_credential_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = TG_TABLE_NAME || ' history is immutable';
END
$$;


--
-- Name: apdl_reject_operator_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_operator_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'operator audit rows are immutable';
END
$$;


--
-- Name: apdl_reject_project_membership_history_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_project_membership_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = TG_TABLE_NAME || ' history is immutable';
END
$$;


--
-- Name: apdl_reject_project_ownership_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_reject_project_ownership_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'project ownership audit is immutable';
END
$$;


--
-- Name: apdl_require_self_created_project_owner(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_require_self_created_project_owner() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    current_created_by UUID;
    current_owner_user_id UUID;
BEGIN
    SELECT created_by, owner_user_id
    INTO current_created_by, current_owner_user_id
    FROM admin_projects
    WHERE project_id = NEW.project_id;

    IF current_created_by IS NOT NULL AND current_owner_user_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'self-created projects require a human owner';
    END IF;

    RETURN NULL;
END
$$;


--
-- Name: apdl_revalidate_codegen_model_assignments(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_revalidate_codegen_model_assignments() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM apdl_assert_codegen_model_assignments_current(
            NEW.project_id,
            NEW.provider
        );
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM apdl_assert_codegen_model_assignments_current(
            OLD.project_id,
            OLD.provider
        );
    ELSE
        PERFORM apdl_assert_codegen_model_assignments_current(
            OLD.project_id,
            OLD.provider
        );
        IF (NEW.project_id, NEW.provider)
           IS DISTINCT FROM (OLD.project_id, OLD.provider) THEN
            PERFORM apdl_assert_codegen_model_assignments_current(
                NEW.project_id,
                NEW.provider
            );
        END IF;
    END IF;
    RETURN NULL;
END
$$;


--
-- Name: apdl_rollout_is_canonical(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_rollout_is_canonical(value jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    percentage NUMERIC;
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF NOT (value ? 'percentage')
       OR NOT (value ? 'bucket_by')
       OR (value - 'percentage' - 'bucket_by') <> '{}'::JSONB THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(value->'percentage') IS DISTINCT FROM 'number' THEN
        RETURN false;
    END IF;

    percentage := (value->>'percentage')::NUMERIC;
    IF percentage < 0 OR percentage > 100 THEN
        RETURN false;
    END IF;

    IF jsonb_typeof(value->'bucket_by') IS DISTINCT FROM 'string' THEN
        RETURN false;
    END IF;
    RETURN char_length(value->>'bucket_by') BETWEEN 1 AND 128;
EXCEPTION
    WHEN invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
END
$$;


--
-- Name: apdl_rules_rollouts_are_canonical(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_rules_rollouts_are_canonical(value jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    rule JSONB;
BEGIN
    IF jsonb_typeof(value) IS DISTINCT FROM 'array'
       OR jsonb_array_length(value) > 50 THEN
        RETURN false;
    END IF;

    FOR rule IN SELECT item FROM jsonb_array_elements(value) AS items(item)
    LOOP
        IF jsonb_typeof(rule) IS DISTINCT FROM 'object'
           OR NOT (rule ? 'rollout')
           OR public.apdl_rollout_is_canonical(rule->'rollout') IS NOT TRUE THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
EXCEPTION
    WHEN invalid_parameter_value THEN
        RETURN false;
END
$$;


--
-- Name: apdl_validate_active_agents_setup(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_active_agents_setup(candidate_project_id text) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    current_policy RECORD;
    valid_assignment_count INTEGER;
BEGIN
    SELECT state, required_data_residency, allow_cross_vendor_retry,
           project_daily_cost_limit_usd_micros,
           run_cost_limit_usd_micros
    INTO current_policy
    FROM llm_project_policies
    WHERE project_id = candidate_project_id;

    IF NOT FOUND OR current_policy.state <> 'active' THEN
        RETURN;
    END IF;
    IF current_policy.project_daily_cost_limit_usd_micros <= 0
       OR current_policy.run_cost_limit_usd_micros <= 0
       OR current_policy.run_cost_limit_usd_micros >
          current_policy.project_daily_cost_limit_usd_micros THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'active Agents setup requires positive bounded budgets';
    END IF;
    IF current_policy.allow_cross_vendor_retry THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'active Agents setup forbids implicit cross-vendor retry';
    END IF;

    SELECT COUNT(*)
    INTO valid_assignment_count
    FROM llm_project_model_assignments AS assignment
    JOIN llm_project_provider_connections AS connection
      ON connection.project_id = assignment.project_id
     AND connection.provider = assignment.provider
     AND connection.state = 'active'
     AND connection.catalog_version = assignment.model_catalog_version
    JOIN llm_project_provider_models AS model
      ON model.project_id = assignment.project_id
     AND model.provider = assignment.provider
     AND model.connection_version = connection.version
     AND model.inventory_version = connection.inventory_version
     AND model.model_id = assignment.model
     AND model.catalog_version = assignment.model_catalog_version
     AND assignment.tier = ANY(model.supported_tiers)
    JOIN llm_vault_provider_credentials AS credential
      ON credential.credential_id = connection.credential_id
     AND credential.project_id = connection.project_id
     AND credential.provider = connection.provider
     AND credential.state = 'active'
    JOIN llm_vault_connection_consumers AS consumer
      ON consumer.connection_id = credential.connection_id
     AND consumer.project_id = credential.project_id
     AND consumer.provider = credential.provider
     AND consumer.consumer = 'agents'
    JOIN llm_project_provider_policies AS provider_policy
      ON provider_policy.project_id = assignment.project_id
     AND provider_policy.provider = assignment.provider
     AND provider_policy.model = assignment.model
     AND provider_policy.data_residency =
         current_policy.required_data_residency
     AND provider_policy.data_residency = model.data_residency
     AND provider_policy.allowed_data_classifications =
         model.allowed_data_classifications
     AND provider_policy.enabled
    WHERE assignment.project_id = candidate_project_id;

    IF valid_assignment_count <> 2
       OR NOT EXISTS (
           SELECT 1 FROM llm_project_model_assignments
           WHERE project_id = candidate_project_id AND tier = 'fast'
       )
       OR NOT EXISTS (
           SELECT 1 FROM llm_project_model_assignments
           WHERE project_id = candidate_project_id AND tier = 'reasoning'
       )
       OR EXISTS (
           SELECT 1
           FROM llm_project_provider_policies AS provider_policy
           WHERE provider_policy.project_id = candidate_project_id
             AND provider_policy.enabled
             AND NOT EXISTS (
                 SELECT 1
                 FROM llm_project_model_assignments AS assignment
                 WHERE assignment.project_id = provider_policy.project_id
                   AND assignment.provider = provider_policy.provider
                   AND assignment.model = provider_policy.model
             )
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'active Agents setup requires current fast and reasoning assignments';
    END IF;
END
$$;


--
-- Name: apdl_validate_codegen_llm_attempt_snapshot(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_codegen_llm_attempt_snapshot() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    snapshot_assignment JSONB;
BEGIN
    SELECT CASE NEW.role
        WHEN 'editor' THEN changeset.llm_execution_snapshot->'assignments'->0
        WHEN 'helper' THEN changeset.llm_execution_snapshot->'assignments'->1
        ELSE NULL
    END
    INTO snapshot_assignment
    FROM codegen_changesets AS changeset
    WHERE changeset.changeset_id = NEW.changeset_id
      AND changeset.project_id = NEW.project_id;

    IF snapshot_assignment IS NULL
       OR snapshot_assignment->>'role' <> NEW.role
       OR snapshot_assignment->>'provider' <> NEW.provider
       OR snapshot_assignment->>'model_id' <> NEW.model_id
       OR snapshot_assignment->>'assignment_version'
            <> NEW.assignment_version::TEXT THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Codegen LLM attempt must match its immutable execution snapshot';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_validate_codegen_model_assignment(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_codegen_model_assignment() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM codegen_project_provider_connections AS connection
        JOIN codegen_project_provider_models AS model
          ON model.project_id = connection.project_id
         AND model.provider = connection.provider
         AND model.connection_version = connection.version
         AND model.inventory_version = connection.inventory_version
         AND model.catalog_version = connection.catalog_version
        WHERE connection.project_id = NEW.project_id
          AND connection.provider = NEW.provider
          AND connection.state = 'active'
          AND model.model_id = NEW.model_id
          AND NEW.role = ANY(model.supported_roles)
          AND NEW.connection_version = connection.version
          AND NEW.inventory_version = connection.inventory_version
          AND NEW.catalog_version = connection.catalog_version
          AND NEW.catalog_version = model.catalog_version
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Codegen model assignment requires an eligible current inventory model';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: apdl_validate_execution_authorization_provenance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_execution_authorization_provenance() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    project_creator UUID;
BEGIN
    SELECT project.created_by
    INTO project_creator
    FROM admin_projects AS project
    WHERE project.project_id = NEW.project_id
    FOR KEY SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'execution authorization requires an existing project';
    END IF;

    IF NEW.authorization_source = 'operator_provisioned'
       AND project_creator IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'self-registered projects require an explicit execution override';
    END IF;

    IF NEW.authorization_source = 'self_registered_override'
       AND project_creator IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operator-provisioned projects cannot use a self-registered override';
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: apdl_validate_llm_vault_connection_authority(uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_llm_vault_connection_authority(candidate_connection_id uuid) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    current_connection RECORD;
    active_credential_count INTEGER;
    secret_count INTEGER;
    consumer_count INTEGER;
    model_count INTEGER;
    current_model_count INTEGER;
BEGIN
    SELECT state, version, inventory_version
    INTO current_connection
    FROM llm_vault_connections
    WHERE connection_id = candidate_connection_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT
        COUNT(*) FILTER (WHERE credential.state = 'active'),
        COUNT(secret.credential_id)
    INTO active_credential_count, secret_count
    FROM llm_vault_provider_credentials AS credential
    LEFT JOIN llm_vault_provider_secrets AS secret
      ON secret.credential_id = credential.credential_id
    WHERE credential.connection_id = candidate_connection_id;

    SELECT COUNT(*)
    INTO consumer_count
    FROM llm_vault_connection_consumers
    WHERE connection_id = candidate_connection_id;

    SELECT
        COUNT(*),
        COUNT(*) FILTER (
            WHERE connection_version = current_connection.version
              AND inventory_version = current_connection.inventory_version
        )
    INTO model_count, current_model_count
    FROM llm_vault_provider_models
    WHERE connection_id = candidate_connection_id;

    IF current_connection.state = 'active' THEN
        IF active_credential_count <> 1
           OR secret_count <> 1
           OR consumer_count NOT BETWEEN 1 AND 2
           OR model_count = 0
           OR current_model_count <> model_count THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'active LLM vault connection requires one secret, explicit consumers, and current models';
        END IF;
    ELSIF active_credential_count <> 0
       OR secret_count <> 0
       OR consumer_count <> 0
       OR model_count <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'revoked LLM vault connection cannot retain live authority';
    END IF;
END
$$;


--
-- Name: apdl_validate_managed_credential(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_managed_credential() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    credential auth_credentials%ROWTYPE;
    membership_roles TEXT[];
    canonical_roles TEXT[];
    predecessor auth_credentials%ROWTYPE;
BEGIN
    SELECT stored.*
    INTO credential
    FROM auth_credentials AS stored
    WHERE stored.credential_id = NEW.credential_id
      AND stored.project_id = NEW.project_id
    FOR KEY SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'managed credential requires an existing credential';
    END IF;

    IF credential.expires_at IS NOT NULL
       OR NOT credential.active
       OR credential.revoked_at IS NOT NULL
       OR credential.actor_user_id IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'managed SDK credentials must be durable, active, and non-delegated';
    END IF;

    IF credential.credential_kind = 'browser' THEN
        canonical_roles := ARRAY['events:write', 'config:read']::TEXT[];
    ELSE
        canonical_roles := ARRAY(
            SELECT allowed_role
            FROM unnest(
                ARRAY[
                    'events:write',
                    'config:read',
                    'config:evaluate',
                    'query:read'
                ]::TEXT[]
            ) AS allowed_role
            WHERE allowed_role = ANY(credential.roles)
        );
    END IF;

    IF cardinality(canonical_roles) = 0
       OR credential.roles IS DISTINCT FROM canonical_roles THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'managed credential roles are not canonical';
    END IF;

    SELECT membership.roles
    INTO membership_roles
    FROM admin_user_projects AS membership
    JOIN admin_users AS account
      ON account.user_id = membership.user_id
    WHERE membership.user_id = NEW.created_by_user_id
      AND membership.project_id = NEW.project_id
      AND account.active
    FOR KEY SHARE OF membership, account;

    IF NOT FOUND
       OR NOT ('credentials:manage' = ANY(membership_roles))
       OR NOT (credential.roles <@ membership_roles) THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'managed credential exceeds current human membership';
    END IF;

    IF NEW.rotated_from_credential_id IS NOT NULL THEN
        SELECT stored.*
        INTO predecessor
        FROM admin_managed_credentials AS managed
        JOIN auth_credentials AS stored
          ON stored.credential_id = managed.credential_id
         AND stored.project_id = managed.project_id
        WHERE managed.credential_id = NEW.rotated_from_credential_id
          AND managed.project_id = NEW.project_id
        FOR KEY SHARE OF managed, stored;

        IF NOT FOUND
           OR predecessor.credential_kind <> credential.credential_kind
           OR predecessor.roles IS DISTINCT FROM credential.roles THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'credential rotation must preserve kind and roles';
        END IF;
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: apdl_validate_project_invitation_update(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_project_invitation_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.token_hash <> OLD.token_hash
       OR NEW.project_id <> OLD.project_id
       OR NEW.email <> OLD.email
       OR NEW.roles <> OLD.roles
       OR NEW.inviter_user_id <> OLD.inviter_user_id
       OR NEW.expires_at <> OLD.expires_at
       OR NEW.created_at <> OLD.created_at
       OR OLD.accepted_at IS NOT NULL
       OR OLD.revoked_at IS NOT NULL
       OR (
           (NEW.accepted_at IS NULL AND NEW.revoked_at IS NULL)
           OR (NEW.accepted_at IS NOT NULL AND NEW.revoked_at IS NOT NULL)
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project invitation lifecycle transition is invalid';
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: apdl_validate_project_owner_assignment(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.apdl_validate_project_owner_assignment() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    owner_roles TEXT[];
BEGIN
    IF NEW.owner_user_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT membership.roles
    INTO owner_roles
    FROM admin_users AS account
    JOIN admin_user_projects AS membership
      ON membership.user_id = account.user_id
     AND membership.project_id = NEW.project_id
    WHERE account.user_id = NEW.owner_user_id
      AND account.active
    FOR KEY SHARE OF account, membership;

    IF NOT FOUND OR NOT ('members:manage' = ANY(owner_roles)) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project owner must be an active project member with members:manage';
    END IF;

    RETURN NEW;
END
$$;


--
-- Name: enforce_codegen_changeset_repository_target(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_codegen_changeset_repository_target() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (OLD.repository_grant_id, OLD.repository_id,
            OLD.repository_installation_id, OLD.repository_full_name,
            OLD.repository_target_quarantined)
           IS DISTINCT FROM
           (NEW.repository_grant_id, NEW.repository_id,
            NEW.repository_installation_id, NEW.repository_full_name,
            NEW.repository_target_quarantined)
        THEN
            RAISE EXCEPTION
                'A changeset repository target is immutable after creation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.repository_target_quarantined THEN
        -- New rows must always reference a verified repository target.
        RAISE EXCEPTION
            'New changesets require a verified repository target';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM github_repository_grants AS grant_record
        WHERE grant_record.project_id = NEW.project_id
          AND grant_record.grant_id = NEW.repository_grant_id
          AND grant_record.installation_id = NEW.repository_installation_id
          AND grant_record.repository_id = NEW.repository_id
          AND grant_record.repository_full_name = NEW.repository_full_name
          AND grant_record.status = 'active'
          AND grant_record.verified_at IS NOT NULL
          AND grant_record.revoked_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'Changeset repository target does not match an active grant';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: enforce_codegen_pr_publication_intent_link(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_codegen_pr_publication_intent_link() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.event_type <> 'intent_recorded'
       AND NOT EXISTS (
           SELECT 1
           FROM codegen_pull_request_publication_events AS intent
           WHERE intent.event_id = NEW.intent_event_id
             AND intent.changeset_id = NEW.changeset_id
             AND intent.event_type = 'intent_recorded'
       ) THEN
        RAISE EXCEPTION
            'codegen PR publication event requires its same-changeset intent';
    END IF;
    IF NEW.event_type = 'cleanup_confirmed'
       AND NOT EXISTS (
           SELECT 1
           FROM codegen_pull_request_publication_events AS request
           WHERE request.event_id = NEW.cleanup_request_event_id
             AND request.changeset_id = NEW.changeset_id
             AND request.intent_event_id = NEW.intent_event_id
             AND request.event_type = 'cleanup_requested'
             AND request.pr_number IS NOT DISTINCT FROM NEW.pr_number
             AND request.github_url IS NOT DISTINCT FROM NEW.github_url
             AND request.payload->>'next_action'
                 IS NOT DISTINCT FROM NEW.payload->>'next_action'
             AND request.payload->>'reason'
                 IS NOT DISTINCT FROM NEW.payload->>'reason'
       ) THEN
        RAISE EXCEPTION
            'codegen PR cleanup confirmation requires its exact request';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: enforce_event_pipeline_watermark_monotonicity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_event_pipeline_watermark_monotonicity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'event pipeline watermarks cannot be deleted';
    END IF;

    IF NEW.project_id <> OLD.project_id
       OR NEW.stream_key <> OLD.stream_key
       OR NEW.provenance_start_stream_id <> OLD.provenance_start_stream_id
    THEN
        RAISE EXCEPTION 'event pipeline watermark identity is immutable';
    END IF;

    IF split_part(NEW.contiguous_stream_id, '-', 1)::numeric
           < split_part(OLD.contiguous_stream_id, '-', 1)::numeric
       OR (
           split_part(NEW.contiguous_stream_id, '-', 1)::numeric
               = split_part(OLD.contiguous_stream_id, '-', 1)::numeric
           AND split_part(NEW.contiguous_stream_id, '-', 2)::numeric
               < split_part(OLD.contiguous_stream_id, '-', 2)::numeric
       )
    THEN
        RAISE EXCEPTION 'event pipeline watermark cannot move backwards';
    END IF;

    IF NEW.consumer_group_entries_read < OLD.consumer_group_entries_read THEN
        RAISE EXCEPTION 'consumer group delivery count cannot move backwards';
    END IF;

    IF OLD.status = 'degraded'
       AND (
           NEW.status <> OLD.status
           OR NEW.failure_reason IS DISTINCT FROM OLD.failure_reason
       )
    THEN
        RAISE EXCEPTION 'event pipeline degradation is irreversible';
    END IF;

    IF NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'event pipeline watermark timestamp cannot move backwards';
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: enforce_experiment_analysis_boundary_immutability(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_experiment_analysis_boundary_immutability() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'experiment analysis boundaries cannot be deleted';
    END IF;

    IF NEW.project_id <> OLD.project_id
       OR NEW.experiment_key <> OLD.experiment_key
       OR NEW.config_version <> OLD.config_version
       OR NEW.stream_key <> OLD.stream_key
       OR NEW.window_start <> OLD.window_start
       OR NEW.window_end <> OLD.window_end
       OR NEW.marker_token <> OLD.marker_token
       OR NEW.requested_at <> OLD.requested_at
       OR (
           OLD.marker_publish_observed_stream_id IS NOT NULL
           AND NEW.marker_publish_observed_stream_id
               IS DISTINCT FROM OLD.marker_publish_observed_stream_id
       )
    THEN
        RAISE EXCEPTION 'experiment analysis boundary identity is immutable';
    END IF;

    IF OLD.marker_publish_state IN ('published', 'quarantined') THEN
        RAISE EXCEPTION 'experiment analysis boundary publication is terminal';
    END IF;

    IF OLD.marker_publish_state <> 'pending' THEN
        RAISE EXCEPTION 'experiment analysis boundary publication state is invalid';
    END IF;

    IF NEW.marker_publish_state = 'pending' THEN
        IF NEW.marker_publish_attempts
            <> OLD.marker_publish_attempts + 1
        THEN
            RAISE EXCEPTION 'boundary marker retry attempt must advance once';
        END IF;
    ELSIF NEW.marker_publish_state = 'published' THEN
        IF NEW.marker_publish_attempts <> OLD.marker_publish_attempts THEN
            RAISE EXCEPTION 'boundary marker success cannot change attempts';
        END IF;
    ELSIF NEW.marker_publish_state = 'quarantined' THEN
        IF NEW.marker_publish_attempts
            <> OLD.marker_publish_attempts + 1
        THEN
            RAISE EXCEPTION 'boundary marker quarantine must advance once';
        END IF;
    ELSE
        RAISE EXCEPTION 'experiment analysis boundary publication state is invalid';
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: enforce_github_repository_grant_lifecycle(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_github_repository_grant_lifecycle() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (OLD.grant_id, OLD.project_id, OLD.installation_id, OLD.repository_id,
        OLD.authorization_source, OLD.authorization_subject, OLD.created_at)
       IS DISTINCT FROM
       (NEW.grant_id, NEW.project_id, NEW.installation_id, NEW.repository_id,
        NEW.authorization_source, NEW.authorization_subject, NEW.created_at)
    THEN
        RAISE EXCEPTION
            'GitHub repository grant identity and evidence are immutable';
    END IF;

    IF OLD.status = 'revoked' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Revoked GitHub repository grants are immutable';
    END IF;

    IF NOT (
        NEW.status = OLD.status
        OR (OLD.status = 'pending_reauthorization'
            AND NEW.status IN ('active', 'revoked'))
        OR (OLD.status = 'active' AND NEW.status = 'revoked')
    ) THEN
        RAISE EXCEPTION 'Invalid GitHub repository grant transition: % -> %',
            OLD.status, NEW.status;
    END IF;

    IF NEW.status = OLD.status AND (
        NEW.verified_at IS DISTINCT FROM OLD.verified_at
        OR NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
    ) THEN
        RAISE EXCEPTION
            'GitHub repository grant lifecycle timestamps are immutable';
    END IF;

    -- The slug is display / routing metadata, never repository identity.  It
    -- may follow a GitHub rename while the numeric repository id stays fixed.
    NEW.updated_at := now();
    RETURN NEW;
END
$$;


--
-- Name: ensure_admin_project_exists(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ensure_admin_project_exists() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO admin_projects (project_id)
    VALUES (NEW.project_id)
    ON CONFLICT (project_id) DO NOTHING;
    RETURN NEW;
END;
$$;


--
-- Name: ensure_llm_project_policy_defaults(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ensure_llm_project_policy_defaults() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO llm_project_policies (project_id)
    VALUES (NEW.project_id)
    ON CONFLICT (project_id) DO NOTHING;
    RETURN NEW;
END
$$;


--
-- Name: prevent_analytics_data_deletion_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prevent_analytics_data_deletion_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'analytics data deletion audit records are immutable';
END;
$$;


--
-- Name: prevent_github_repository_grant_deletion(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prevent_github_repository_grant_deletion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'GitHub repository grants are immutable audit records and cannot be deleted';
END
$$;


--
-- Name: reject_admin_project_creator_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_admin_project_creator_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'admin_projects creator provenance is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: reject_codegen_pr_publication_event_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_codegen_pr_publication_event_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'codegen pull-request publication events are append-only';
END;
$$;


--
-- Name: reject_experiment_analysis_snapshot_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_experiment_analysis_snapshot_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'experiment analysis snapshots are immutable';
END;
$$;


--
-- Name: reject_experiment_completeness_truncate(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_experiment_completeness_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'experiment completeness authorities cannot be truncated';
END;
$$;


--
-- Name: reject_experiment_flag_ownership_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.reject_experiment_flag_ownership_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.flag_key IS DISTINCT FROM OLD.flag_key THEN
        RAISE EXCEPTION
            'Experiment backing-flag ownership is immutable';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: require_active_codegen_repository_grant(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.require_active_codegen_repository_grant() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM github_repository_grants AS grant_record
        WHERE grant_record.project_id = NEW.project_id
          AND grant_record.grant_id = NEW.grant_id
          AND grant_record.status = 'active'
          AND grant_record.verified_at IS NOT NULL
          AND grant_record.revoked_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'Codegen connection requires an active same-project repository grant';
    END IF;
    RETURN NEW;
END
$$;


--
-- Name: validate_analytics_data_deletion_audit_insert(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_analytics_data_deletion_audit_insert() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    requested analytics_data_deletion_audit%ROWTYPE;
BEGIN
    NEW.recorded_at := clock_timestamp();

    IF NEW.event_type = 'requested' THEN
        RETURN NEW;
    END IF;

    SELECT *
    INTO requested
    FROM analytics_data_deletion_audit
    WHERE request_id = NEW.request_id
      AND event_type = 'requested';

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'analytics deletion completion requires a requested event';
    END IF;

    IF requested.scope <> NEW.scope
       OR requested.project_id <> NEW.project_id
       OR requested.target_sha256 <> NEW.target_sha256
       OR requested.request_sha256 <> NEW.request_sha256
       OR requested.actor <> NEW.actor
       OR requested.reason <> NEW.reason THEN
        RAISE EXCEPTION
            'analytics deletion completion does not match its request';
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: validate_codegen_changeset_private_controls(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.validate_codegen_changeset_private_controls() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    revert_control JSONB;
    source_project_id TEXT;
    source_status TEXT;
    source_merge_sha TEXT;
    target_merge_sha TEXT;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.control_metadata IS DISTINCT FROM OLD.control_metadata THEN
        RAISE EXCEPTION 'codegen changeset control metadata is immutable';
    END IF;

    revert_control := NEW.control_metadata->'revert';
    IF revert_control IS NULL OR revert_control = 'null'::jsonb THEN
        RETURN NEW;
    END IF;

    SELECT project_id, status, merge_sha
    INTO source_project_id, source_status, source_merge_sha
    FROM codegen_changesets
    WHERE changeset_id = revert_control->>'source_changeset_id';

    IF NOT FOUND
       OR source_project_id IS DISTINCT FROM NEW.project_id
       OR source_status IS DISTINCT FROM 'merged' THEN
        RAISE EXCEPTION
            'private revert control requires a merged source in the same project';
    END IF;

    target_merge_sha := revert_control->>'merge_sha';
    IF source_merge_sha IS DISTINCT FROM target_merge_sha THEN
        RAISE EXCEPTION
            'private revert target must equal the recorded source merge SHA';
    END IF;

    RETURN NEW;
END
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_credential_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_credential_audit (
    audit_id uuid NOT NULL,
    project_id text NOT NULL,
    credential_id text NOT NULL,
    action text NOT NULL,
    actor_user_id uuid NOT NULL,
    actor_email text NOT NULL,
    credential_kind text NOT NULL,
    roles text[] NOT NULL,
    successor_credential_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_credential_audit_action_check CHECK ((action = ANY (ARRAY['create'::text, 'rotate'::text, 'revoke'::text]))),
    CONSTRAINT admin_credential_audit_actor_email_check CHECK (((actor_email = lower(actor_email)) AND (actor_email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'::text))),
    CONSTRAINT admin_credential_audit_credential_kind_check CHECK ((credential_kind = ANY (ARRAY['confidential'::text, 'browser'::text]))),
    CONSTRAINT admin_credential_audit_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT admin_credential_audit_roles_check CHECK (((cardinality(roles) > 0) AND (array_position(roles, NULL::text) IS NULL) AND (roles <@ ARRAY['events:write'::text, 'config:read'::text, 'config:evaluate'::text, 'query:read'::text]))),
    CONSTRAINT admin_credential_audit_rotation_shape CHECK ((((action = 'rotate'::text) AND (successor_credential_id IS NOT NULL) AND (successor_credential_id <> credential_id)) OR ((action = ANY (ARRAY['create'::text, 'revoke'::text])) AND (successor_credential_id IS NULL))))
);


--
-- Name: admin_login_account_risk; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_login_account_risk (
    user_id uuid NOT NULL,
    email_hash character(64) NOT NULL,
    window_started_at timestamp with time zone NOT NULL,
    failure_count integer NOT NULL,
    last_failed_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_login_account_risk_email_hash_check CHECK ((email_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT admin_login_account_risk_failure_count_check CHECK ((failure_count > 0))
);


--
-- Name: admin_login_rate_buckets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_login_rate_buckets (
    scope text NOT NULL,
    key_hash character(64) NOT NULL,
    window_started_at timestamp with time zone NOT NULL,
    attempt_count integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_login_rate_buckets_attempt_count_check CHECK ((attempt_count > 0)),
    CONSTRAINT admin_login_rate_buckets_key_hash_check CHECK ((key_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT admin_login_rate_buckets_scope_check CHECK ((scope = ANY (ARRAY['global'::text, 'network'::text, 'device'::text])))
);


--
-- Name: admin_login_source_risk; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_login_source_risk (
    scope text NOT NULL,
    source_hash character(64) NOT NULL,
    email_hash character(64) NOT NULL,
    failure_count integer NOT NULL,
    next_allowed_at timestamp with time zone NOT NULL,
    last_failed_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_login_source_risk_email_hash_check CHECK ((email_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT admin_login_source_risk_failure_count_check CHECK ((failure_count > 0)),
    CONSTRAINT admin_login_source_risk_scope_check CHECK ((scope = ANY (ARRAY['network'::text, 'device'::text]))),
    CONSTRAINT admin_login_source_risk_source_hash_check CHECK ((source_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: admin_managed_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_managed_credentials (
    credential_id text NOT NULL,
    project_id text NOT NULL,
    created_by_user_id uuid NOT NULL,
    created_by_email text NOT NULL,
    rotated_from_credential_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_managed_credentials_created_by_email_check CHECK (((created_by_email = lower(created_by_email)) AND (created_by_email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'::text))),
    CONSTRAINT admin_managed_credentials_credential_id_check CHECK ((credential_id ~ '^managed-[0-9a-f]{32}$'::text)),
    CONSTRAINT admin_managed_credentials_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT admin_managed_credentials_rotation_not_self CHECK (((rotated_from_credential_id IS NULL) OR (rotated_from_credential_id <> credential_id)))
);


--
-- Name: admin_project_execution_authorizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_project_execution_authorizations (
    project_id text NOT NULL,
    authorization_source text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    authorized_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_project_execution_authorizatio_authorization_source_check CHECK ((authorization_source = ANY (ARRAY['operator_provisioned'::text, 'self_registered_override'::text]))),
    CONSTRAINT admin_project_execution_authorizations_actor_check CHECK (((char_length(actor) >= 1) AND (char_length(actor) <= 512) AND (actor = btrim(actor)) AND (POSITION((chr(10)) IN (actor)) = 0) AND (POSITION((chr(13)) IN (actor)) = 0))),
    CONSTRAINT admin_project_execution_authorizations_reason_check CHECK (((char_length(reason) >= 1) AND (char_length(reason) <= 2000) AND (reason = btrim(reason)) AND (POSITION((chr(10)) IN (reason)) = 0) AND (POSITION((chr(13)) IN (reason)) = 0)))
);


--
-- Name: TABLE admin_project_execution_authorizations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.admin_project_execution_authorizations IS 'Immutable operator authority for approvals, Codegen, and external effects.';


--
-- Name: admin_project_invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_project_invitations (
    invitation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_hash character(64) NOT NULL,
    project_id text NOT NULL,
    email text NOT NULL,
    roles text[] NOT NULL,
    inviter_user_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    accepted_by_user_id uuid,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_project_invitations_check CHECK ((expires_at = (created_at + '7 days'::interval))),
    CONSTRAINT admin_project_invitations_check1 CHECK (((accepted_at IS NULL) OR (revoked_at IS NULL))),
    CONSTRAINT admin_project_invitations_check2 CHECK (((accepted_at IS NULL) = (accepted_by_user_id IS NULL))),
    CONSTRAINT admin_project_invitations_email_check CHECK (((email = lower(email)) AND (email = btrim(email)) AND (length(email) <= 320) AND (email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'::text))),
    CONSTRAINT admin_project_invitations_roles_check CHECK (((cardinality(roles) > 0) AND (roles = public.apdl_canonical_admin_roles(roles)))),
    CONSTRAINT admin_project_invitations_token_hash_check CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: admin_project_membership_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_project_membership_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    action text NOT NULL,
    actor_user_id uuid NOT NULL,
    subject_user_id uuid,
    subject_email text NOT NULL,
    invitation_id uuid,
    previous_roles text[],
    new_roles text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_project_membership_audit_action_check CHECK ((action = ANY (ARRAY['invitation_create'::text, 'invitation_revoke'::text, 'invitation_accept'::text, 'roles_replace'::text, 'member_remove'::text]))),
    CONSTRAINT admin_project_membership_audit_check CHECK ((((action = 'invitation_create'::text) AND (invitation_id IS NOT NULL) AND (subject_user_id IS NULL) AND (previous_roles IS NULL) AND (new_roles IS NOT NULL)) OR ((action = 'invitation_revoke'::text) AND (invitation_id IS NOT NULL) AND (subject_user_id IS NULL) AND (previous_roles IS NOT NULL) AND (new_roles IS NULL)) OR ((action = 'invitation_accept'::text) AND (invitation_id IS NOT NULL) AND (subject_user_id IS NOT NULL) AND (previous_roles IS NULL) AND (new_roles IS NOT NULL)) OR ((action = 'roles_replace'::text) AND (invitation_id IS NULL) AND (subject_user_id IS NOT NULL) AND (previous_roles IS NOT NULL) AND (new_roles IS NOT NULL) AND (previous_roles <> new_roles)) OR ((action = 'member_remove'::text) AND (invitation_id IS NULL) AND (subject_user_id IS NOT NULL) AND (previous_roles IS NOT NULL) AND (new_roles IS NULL)))),
    CONSTRAINT admin_project_membership_audit_new_roles_check CHECK (((new_roles IS NULL) OR ((cardinality(new_roles) > 0) AND (new_roles = public.apdl_canonical_admin_roles(new_roles))))),
    CONSTRAINT admin_project_membership_audit_previous_roles_check CHECK (((previous_roles IS NULL) OR ((cardinality(previous_roles) > 0) AND (previous_roles = public.apdl_canonical_admin_roles(previous_roles))))),
    CONSTRAINT admin_project_membership_audit_subject_email_check CHECK (((subject_email = lower(subject_email)) AND (subject_email = btrim(subject_email)) AND (length(subject_email) <= 320)))
);


--
-- Name: admin_project_ownership_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_project_ownership_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    previous_owner_user_id uuid,
    new_owner_user_id uuid NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_project_ownership_audit_actor_check CHECK (((actor = btrim(actor)) AND ((length(actor) >= 1) AND (length(actor) <= 512)) AND (POSITION((chr(10)) IN (actor)) = 0) AND (POSITION((chr(13)) IN (actor)) = 0))),
    CONSTRAINT admin_project_ownership_audit_check CHECK (((previous_owner_user_id IS NULL) OR (previous_owner_user_id <> new_owner_user_id))),
    CONSTRAINT admin_project_ownership_audit_reason_check CHECK (((reason = btrim(reason)) AND ((length(reason) >= 1) AND (length(reason) <= 2000)) AND (POSITION((chr(10)) IN (reason)) = 0) AND (POSITION((chr(13)) IN (reason)) = 0)))
);


--
-- Name: admin_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_projects (
    project_id text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_user_id uuid,
    CONSTRAINT admin_projects_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text))
);


--
-- Name: admin_proxy_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_proxy_audit (
    audit_id uuid NOT NULL,
    user_id uuid,
    actor_email text NOT NULL,
    project_id text NOT NULL,
    required_role text NOT NULL,
    service text NOT NULL,
    method text NOT NULL,
    path text NOT NULL,
    status_code integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT admin_proxy_audit_method_check CHECK ((method = ANY (ARRAY['POST'::text, 'PUT'::text, 'DELETE'::text]))),
    CONSTRAINT admin_proxy_audit_path_check CHECK ((path ~~ '/%'::text)),
    CONSTRAINT admin_proxy_audit_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT admin_proxy_audit_service_check CHECK ((service = ANY (ARRAY['ingestion'::text, 'config'::text, 'query'::text, 'agents'::text, 'codegen'::text, 'llm-vault'::text]))),
    CONSTRAINT admin_proxy_audit_status_code_check CHECK (((status_code >= 100) AND (status_code <= 599)))
);


--
-- Name: admin_security_notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_security_notifications (
    notification_id uuid NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    status text DEFAULT 'unread'::text NOT NULL,
    observed_failures integer NOT NULL,
    window_started_at timestamp with time zone NOT NULL,
    last_detected_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    acknowledged_at timestamp with time zone,
    CONSTRAINT admin_security_notifications_check CHECK ((((status = 'unread'::text) AND (acknowledged_at IS NULL)) OR ((status = 'acknowledged'::text) AND (acknowledged_at IS NOT NULL)))),
    CONSTRAINT admin_security_notifications_kind_check CHECK ((kind = 'suspicious_login_activity'::text)),
    CONSTRAINT admin_security_notifications_observed_failures_check CHECK ((observed_failures > 0)),
    CONSTRAINT admin_security_notifications_status_check CHECK ((status = ANY (ARRAY['unread'::text, 'acknowledged'::text])))
);


--
-- Name: admin_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_sessions (
    session_id uuid NOT NULL,
    user_id uuid NOT NULL,
    token_hash character(64) NOT NULL,
    csrf_hash character(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT admin_sessions_check CHECK ((expires_at > created_at)),
    CONSTRAINT admin_sessions_csrf_hash_check CHECK ((csrf_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT admin_sessions_token_hash_check CHECK ((token_hash ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: admin_user_projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_user_projects (
    user_id uuid NOT NULL,
    project_id text NOT NULL,
    roles text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_user_projects_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT admin_user_projects_roles_check CHECK (((cardinality(roles) > 0) AND (roles = public.apdl_canonical_admin_roles(roles))))
);


--
-- Name: admin_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_users (
    user_id uuid NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT admin_users_email_check CHECK (((email = lower(email)) AND (email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'::text))),
    CONSTRAINT admin_users_password_hash_check CHECK ((password_hash ~~ '$argon2id$%'::text))
);


--
-- Name: agent_approval_commands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_approval_commands (
    command_id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id text NOT NULL,
    project_id text NOT NULL,
    actor_credential_id text NOT NULL,
    actor_user_id uuid,
    request_sha256 character(64) NOT NULL,
    gate_id text NOT NULL,
    gate_agent text NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    resume_status text NOT NULL,
    approved_count integer NOT NULL,
    rejected_count integer NOT NULL,
    comment text,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT agent_approval_commands_actor_credential_id_check CHECK ((actor_credential_id ~ '^[A-Za-z0-9_-]{8,64}$'::text)),
    CONSTRAINT agent_approval_commands_admin_actor_check CHECK (((actor_credential_id !~ '^adminproxy-'::text) OR (actor_user_id IS NOT NULL))),
    CONSTRAINT agent_approval_commands_approved_count_check CHECK ((approved_count >= 0)),
    CONSTRAINT agent_approval_commands_comment_check CHECK (((comment IS NULL) OR (char_length(comment) <= 2000))),
    CONSTRAINT agent_approval_commands_decision_count_check CHECK ((((approved_count + rejected_count) >= 1) AND ((approved_count + rejected_count) <= 100))),
    CONSTRAINT agent_approval_commands_gate_agent_check CHECK ((gate_agent = ANY (ARRAY['experiment_design'::text, 'feature_proposal'::text, 'code_implementation'::text]))),
    CONSTRAINT agent_approval_commands_rejected_count_check CHECK ((rejected_count >= 0)),
    CONSTRAINT agent_approval_commands_request_sha256_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT agent_approval_commands_resume_status_check CHECK ((resume_status = ANY (ARRAY['approved'::text, 'rejected'::text]))),
    CONSTRAINT agent_approval_commands_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'processing'::text, 'succeeded'::text, 'manual_intervention'::text])))
);


--
-- Name: agent_approval_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_approval_decisions (
    command_id uuid NOT NULL,
    item_id text NOT NULL,
    approved boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_approval_decisions_item_id_check CHECK (((char_length(item_id) >= 1) AND (char_length(item_id) <= 128) AND (item_id = btrim(item_id))))
);


--
-- Name: agent_approval_effects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_approval_effects (
    effect_id uuid DEFAULT gen_random_uuid() NOT NULL,
    command_id uuid NOT NULL,
    run_id text NOT NULL,
    project_id text NOT NULL,
    item_id text NOT NULL,
    effect_type text NOT NULL,
    effect_order integer NOT NULL,
    depends_on_effect_id uuid,
    payload jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    idempotency_key text NOT NULL,
    quota_action_type text,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 8 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner_id text,
    lease_expires_at timestamp with time zone,
    result jsonb,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT agent_approval_effects_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT agent_approval_effects_effect_order_check CHECK ((effect_order >= 0)),
    CONSTRAINT agent_approval_effects_effect_type_check CHECK ((effect_type = ANY (ARRAY['stage_experiment_draft'::text, 'open_treatment_changeset'::text, 'open_code_changeset'::text, 'record_experiment_rejection'::text, 'record_proposal_rejection'::text, 'quarantine_feature_proposal'::text]))),
    CONSTRAINT agent_approval_effects_idempotency_key_check CHECK ((idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$'::text)),
    CONSTRAINT agent_approval_effects_item_id_check CHECK (((char_length(item_id) >= 1) AND (char_length(item_id) <= 128) AND (item_id = btrim(item_id)))),
    CONSTRAINT agent_approval_effects_lease_check CHECK (((lease_owner_id IS NULL) = (lease_expires_at IS NULL))),
    CONSTRAINT agent_approval_effects_max_attempts_check CHECK (((max_attempts >= 1) AND (max_attempts <= 100))),
    CONSTRAINT agent_approval_effects_quota_action_type_check CHECK (((quota_action_type IS NULL) OR (quota_action_type = ANY (ARRAY['create_experiment'::text, 'open_pull_request'::text])))),
    CONSTRAINT agent_approval_effects_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'processing'::text, 'retryable_failed'::text, 'succeeded'::text, 'failed'::text, 'manual_intervention'::text])))
);


--
-- Name: agent_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_audit_log (
    id bigint NOT NULL,
    run_id text NOT NULL,
    action_type text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb,
    safety_result jsonb DEFAULT '{}'::jsonb,
    approval_status text,
    created_at timestamp with time zone DEFAULT now(),
    schema_version text DEFAULT 'agent_action@1'::text NOT NULL,
    idempotency_key text,
    correlation_id uuid,
    source text DEFAULT 'agents-service@1'::text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_audit_log_id_seq OWNED BY public.agent_audit_log.id;


--
-- Name: agent_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_memory (
    id bigint NOT NULL,
    project_id text NOT NULL,
    content text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    embedding public.vector(384),
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT chk_agent_memory_envelope CHECK ((((NOT (metadata ? '_schema'::text)) OR (jsonb_typeof((metadata -> '_schema'::text)) = 'string'::text)) AND ((NOT (metadata ? '_correlation_id'::text)) OR (jsonb_typeof((metadata -> '_correlation_id'::text)) = 'string'::text)) AND ((NOT (metadata ? '_source'::text)) OR (jsonb_typeof((metadata -> '_source'::text)) = 'string'::text))))
);


--
-- Name: agent_memory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agent_memory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agent_memory_id_seq OWNED BY public.agent_memory.id;


--
-- Name: agent_mutation_quota_reservations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_mutation_quota_reservations (
    project_id text NOT NULL,
    action_type text NOT NULL,
    idempotency_key text NOT NULL,
    policy_version text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_mutation_quota_action_type_check CHECK ((action_type = ANY (ARRAY['create_experiment'::text, 'update_flag'::text, 'update_ui_config'::text, 'feature_proposal'::text, 'open_pull_request'::text]))),
    CONSTRAINT agent_mutation_quota_idempotency_key_check CHECK (((char_length(idempotency_key) >= 1) AND (char_length(idempotency_key) <= 256) AND (btrim(idempotency_key) <> ''::text))),
    CONSTRAINT agent_mutation_quota_policy_version_check CHECK ((policy_version = 'rolling_hour@1'::text)),
    CONSTRAINT agent_mutation_quota_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text))
);


--
-- Name: agent_run_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_run_results (
    run_id text NOT NULL,
    agent_name text NOT NULL,
    produces text NOT NULL,
    output jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    run_id text NOT NULL,
    project_id text NOT NULL,
    trigger_type text NOT NULL,
    autonomy_level integer DEFAULT 2 NOT NULL,
    status text DEFAULT 'started'::text NOT NULL,
    phase text DEFAULT 'initializing'::text,
    insights_count integer DEFAULT 0,
    experiments_count integer DEFAULT 0,
    config jsonb DEFAULT '{}'::jsonb,
    started_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    lease_owner_id text,
    lease_expires_at timestamp with time zone,
    execution_lane_project_id text GENERATED ALWAYS AS (
CASE
    WHEN (status = ANY (ARRAY['completed'::text, 'completed_with_errors'::text, 'failed'::text, 'cancelled'::text, 'manual_intervention'::text])) THEN NULL::text
    ELSE project_id
END) STORED,
    CONSTRAINT agent_runs_status_check CHECK ((status = ANY (ARRAY['started'::text, 'running'::text, 'waiting_approval'::text, 'approval_queued'::text, 'cancelling'::text, 'approved'::text, 'rejected'::text, 'completed'::text, 'completed_with_errors'::text, 'failed'::text, 'cancelled'::text, 'manual_intervention'::text])))
);


--
-- Name: COLUMN agent_runs.execution_lane_project_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.agent_runs.execution_lane_project_id IS 'Database-generated per-project execution lane; NULL only for terminal runs.';


--
-- Name: analytics_data_deletion_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analytics_data_deletion_audit (
    request_id uuid NOT NULL,
    event_type text NOT NULL,
    scope text NOT NULL,
    project_id text NOT NULL,
    target_sha256 text NOT NULL,
    request_sha256 text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT analytics_data_deletion_actor_check CHECK (((char_length(actor) >= 1) AND (char_length(actor) <= 512) AND (btrim(actor) <> ''::text))),
    CONSTRAINT analytics_data_deletion_details_check CHECK (((jsonb_typeof(details) = 'object'::text) AND (((event_type = 'requested'::text) AND (details = '{}'::jsonb)) OR ((event_type = 'completed'::text) AND (details ?& ARRAY['matched_rows'::text, 'anonymous_id_count'::text]) AND ((details - ARRAY['matched_rows'::text, 'anonymous_id_count'::text]) = '{}'::jsonb) AND (jsonb_typeof((details -> 'matched_rows'::text)) = 'object'::text) AND (jsonb_typeof((details -> 'anonymous_id_count'::text)) = 'number'::text) AND ((details ->> 'anonymous_id_count'::text) ~ '^(0|[1-9][0-9]*)$'::text) AND ((details -> 'matched_rows'::text) ?& ARRAY['events'::text, 'experiment_event_deliveries'::text, 'feature_flag_exposures'::text, 'frontend_health_events'::text, 'sessions'::text, 'identity_alias_assertions'::text]) AND (((details -> 'matched_rows'::text) - ARRAY['events'::text, 'experiment_event_deliveries'::text, 'feature_flag_exposures'::text, 'frontend_health_events'::text, 'sessions'::text, 'identity_alias_assertions'::text]) = '{}'::jsonb) AND (jsonb_typeof((details #> '{matched_rows,events}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,events}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text) AND (jsonb_typeof((details #> '{matched_rows,experiment_event_deliveries}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,experiment_event_deliveries}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text) AND (jsonb_typeof((details #> '{matched_rows,feature_flag_exposures}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,feature_flag_exposures}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text) AND (jsonb_typeof((details #> '{matched_rows,frontend_health_events}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,frontend_health_events}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text) AND (jsonb_typeof((details #> '{matched_rows,sessions}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,sessions}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text) AND (jsonb_typeof((details #> '{matched_rows,identity_alias_assertions}'::text[])) = 'number'::text) AND ((details #>> '{matched_rows,identity_alias_assertions}'::text[]) ~ '^(0|[1-9][0-9]*)$'::text))))),
    CONSTRAINT analytics_data_deletion_event_type_check CHECK ((event_type = ANY (ARRAY['requested'::text, 'completed'::text]))),
    CONSTRAINT analytics_data_deletion_project_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT analytics_data_deletion_reason_check CHECK (((char_length(reason) >= 1) AND (char_length(reason) <= 2000) AND (btrim(reason) <> ''::text))),
    CONSTRAINT analytics_data_deletion_request_hash_check CHECK ((request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT analytics_data_deletion_scope_check CHECK ((scope = ANY (ARRAY['project'::text, 'user'::text]))),
    CONSTRAINT analytics_data_deletion_target_hash_check CHECK ((target_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: TABLE analytics_data_deletion_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.analytics_data_deletion_audit IS 'Append-only requested/completed evidence for maintenance-fenced analytics deletion';


--
-- Name: COLUMN analytics_data_deletion_audit.target_sha256; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.analytics_data_deletion_audit.target_sha256 IS 'Canonical SHA-256 target digest; raw user identifiers are never retained here';


--
-- Name: apdl_analysis_table_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.apdl_analysis_table_registry (
    table_name text NOT NULL,
    project_column text DEFAULT 'project_id'::text NOT NULL,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT apdl_analysis_table_registry_project_column_check CHECK ((project_column = 'project_id'::text)),
    CONSTRAINT apdl_analysis_table_registry_table_name_check CHECK ((table_name ~ '^public\.[a-z][a-z0-9_]*$'::text))
);


--
-- Name: TABLE apdl_analysis_table_registry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.apdl_analysis_table_registry IS 'Canonical registry of project-scoped tables admitted by active Agents setup.';


--
-- Name: apdl_execution_table_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.apdl_execution_table_registry (
    table_name text NOT NULL,
    project_column text DEFAULT 'project_id'::text NOT NULL,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT apdl_execution_table_registry_project_column_check CHECK ((project_column = 'project_id'::text)),
    CONSTRAINT apdl_execution_table_registry_table_name_check CHECK ((table_name ~ '^public\.[a-z][a-z0-9_]*$'::text))
);


--
-- Name: TABLE apdl_execution_table_registry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.apdl_execution_table_registry IS 'Canonical registry of project-scoped tables that admit or queue execution.';


--
-- Name: auth_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.auth_credentials (
    credential_id text NOT NULL,
    project_id text NOT NULL,
    credential_kind text NOT NULL,
    key_prefix text NOT NULL,
    key_hash character(64) NOT NULL,
    roles text[] NOT NULL,
    active boolean DEFAULT true NOT NULL,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    actor_user_id uuid,
    CONSTRAINT auth_credentials_check CHECK ((((credential_kind = 'confidential'::text) AND (key_prefix = (('proj_'::text || project_id) || '_'::text))) OR ((credential_kind = 'browser'::text) AND (key_prefix = (('client_'::text || project_id) || '_'::text))))),
    CONSTRAINT auth_credentials_check1 CHECK (((cardinality(roles) > 0) AND (array_position(roles, NULL::text) IS NULL) AND (roles <@ ARRAY['events:write'::text, 'config:read'::text, 'config:write'::text, 'config:evaluate'::text, 'query:read'::text, 'agents:read'::text, 'agents:run'::text, 'agents:manage'::text, 'agents:approve'::text]) AND (cardinality(roles) = (((((((((('events:write'::text = ANY (roles)))::integer + (('config:read'::text = ANY (roles)))::integer) + (('config:write'::text = ANY (roles)))::integer) + (('config:evaluate'::text = ANY (roles)))::integer) + (('query:read'::text = ANY (roles)))::integer) + (('agents:read'::text = ANY (roles)))::integer) + (('agents:run'::text = ANY (roles)))::integer) + (('agents:manage'::text = ANY (roles)))::integer) + (('agents:approve'::text = ANY (roles)))::integer)) AND ((credential_kind = 'confidential'::text) OR ((credential_kind = 'browser'::text) AND (cardinality(roles) = 2) AND (roles @> ARRAY['events:write'::text, 'config:read'::text]) AND (roles <@ ARRAY['events:write'::text, 'config:read'::text]))))),
    CONSTRAINT auth_credentials_check2 CHECK (((active AND (revoked_at IS NULL)) OR (NOT active))),
    CONSTRAINT auth_credentials_credential_id_check CHECK ((credential_id ~ '^[A-Za-z0-9_-]{8,64}$'::text)),
    CONSTRAINT auth_credentials_credential_kind_check CHECK ((credential_kind = ANY (ARRAY['confidential'::text, 'browser'::text]))),
    CONSTRAINT auth_credentials_key_hash_check CHECK ((key_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT auth_credentials_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text))
);


--
-- Name: codegen_changesets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_changesets (
    changeset_id text NOT NULL,
    project_id text NOT NULL,
    run_id text,
    status text DEFAULT 'queued'::text NOT NULL,
    base_branch text,
    branch text,
    pr_url text,
    pr_number integer,
    head_sha text,
    github_pr_status text,
    external_ci_status text,
    merge_sha text,
    task jsonb DEFAULT '{}'::jsonb NOT NULL,
    diff_stat jsonb DEFAULT '{}'::jsonb NOT NULL,
    prompts jsonb DEFAULT '[]'::jsonb NOT NULL,
    contract_bundle jsonb,
    requirement_ledger jsonb,
    inspection_snapshot jsonb,
    dependency_slice jsonb,
    verification_plan jsonb,
    verification_coverage jsonb,
    runtime_acceptance_plan jsonb,
    runtime_evidence_assessment jsonb,
    review_verdict jsonb,
    publication_authorization jsonb,
    external_ci_awaiting_since timestamp with time zone,
    ci_retry_count integer DEFAULT 0 NOT NULL,
    ci_remediation_status text DEFAULT 'idle'::text NOT NULL,
    ci_failure_key text,
    ci_failure_summary text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_policy_snapshot jsonb,
    effective_safety_policy_sha256 text,
    repository_grant_id text,
    repository_id bigint,
    repository_installation_id bigint,
    repository_full_name text,
    repository_target_quarantined boolean DEFAULT false NOT NULL,
    retry_of_changeset_id text,
    idempotency_key text NOT NULL,
    idempotency_request_sha256 character(64) NOT NULL,
    control_metadata jsonb DEFAULT '{"revert": null, "risk_level": "high", "schema_version": "changeset_controls@1"}'::jsonb NOT NULL,
    llm_execution_snapshot jsonb,
    CONSTRAINT codegen_changesets_ci_remediation_status_check CHECK ((ci_remediation_status = ANY (ARRAY['idle'::text, 'diagnosing'::text, 'repairing'::text, 'awaiting_ci'::text, 'resolved'::text, 'exhausted'::text]))),
    CONSTRAINT codegen_changesets_control_metadata_check CHECK ((((jsonb_typeof(control_metadata) = 'object'::text) AND (control_metadata ?& ARRAY['schema_version'::text, 'risk_level'::text, 'revert'::text]) AND ((control_metadata - ARRAY['schema_version'::text, 'risk_level'::text, 'revert'::text]) = '{}'::jsonb) AND ((control_metadata ->> 'schema_version'::text) = 'changeset_controls@1'::text) AND ((control_metadata ->> 'risk_level'::text) = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text])) AND (((control_metadata -> 'revert'::text) = 'null'::jsonb) OR ((jsonb_typeof((control_metadata -> 'revert'::text)) = 'object'::text) AND ((control_metadata -> 'revert'::text) ?& ARRAY['source_changeset_id'::text, 'merge_sha'::text]) AND (((control_metadata -> 'revert'::text) - ARRAY['source_changeset_id'::text, 'merge_sha'::text]) = '{}'::jsonb) AND (jsonb_typeof(((control_metadata -> 'revert'::text) -> 'source_changeset_id'::text)) = 'string'::text) AND ((char_length(((control_metadata -> 'revert'::text) ->> 'source_changeset_id'::text)) >= 1) AND (char_length(((control_metadata -> 'revert'::text) ->> 'source_changeset_id'::text)) <= 128)) AND ((((control_metadata -> 'revert'::text) -> 'merge_sha'::text) = 'null'::jsonb) OR ((jsonb_typeof(((control_metadata -> 'revert'::text) -> 'merge_sha'::text)) = 'string'::text) AND ((char_length(((control_metadata -> 'revert'::text) ->> 'merge_sha'::text)) >= 1) AND (char_length(((control_metadata -> 'revert'::text) ->> 'merge_sha'::text)) <= 128))))))) IS TRUE)),
    CONSTRAINT codegen_changesets_effective_policy_sha256_check CHECK (((effective_safety_policy_sha256 IS NULL) OR (effective_safety_policy_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT codegen_changesets_external_ci_status_check CHECK (((external_ci_status IS NULL) OR (external_ci_status = ANY (ARRAY['pending'::text, 'passed'::text, 'failed'::text, 'unverified_external_ci'::text])))),
    CONSTRAINT codegen_changesets_github_pr_status_check CHECK (((github_pr_status IS NULL) OR (github_pr_status = ANY (ARRAY['draft'::text, 'open'::text, 'merged'::text, 'closed'::text])))),
    CONSTRAINT codegen_changesets_idempotency_key_check CHECK ((idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$'::text)),
    CONSTRAINT codegen_changesets_idempotency_request_sha256_check CHECK ((idempotency_request_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT codegen_changesets_llm_execution_snapshot_check CHECK (((llm_execution_snapshot IS NULL) OR (public.apdl_is_codegen_llm_execution_snapshot(llm_execution_snapshot, project_id, repository_grant_id, repository_id, repository_installation_id, repository_full_name) IS TRUE))),
    CONSTRAINT codegen_changesets_public_task_context_check CHECK ((((jsonb_typeof(task) = 'object'::text) AND (jsonb_typeof((task -> 'context'::text)) = 'object'::text) AND (NOT ((task -> 'context'::text) ?| ARRAY['revert_sha'::text, 'reverts_changeset'::text, 'reverts_pr_number'::text, 'retry_of'::text, 'risk_level'::text]))) IS TRUE)),
    CONSTRAINT codegen_changesets_publication_authorization_check CHECK ((((publication_authorization IS NULL) OR (((publication_authorization ->> 'schema_version'::text) = 'tenant_publication_authorization@1'::text) AND (publication_authorization ?& ARRAY['schema_version'::text, 'authority'::text, 'request'::text, 'decision'::text, 'draft_only'::text, 'authorization_sha256'::text]) AND ((publication_authorization - ARRAY['schema_version'::text, 'authority'::text, 'request'::text, 'decision'::text, 'draft_only'::text, 'authorization_sha256'::text]) = '{}'::jsonb) AND (jsonb_typeof((publication_authorization -> 'schema_version'::text)) = 'string'::text) AND ((publication_authorization ->> 'authority'::text) = 'tenant_model_assignments'::text) AND (jsonb_typeof((publication_authorization -> 'authority'::text)) = 'string'::text) AND ((publication_authorization -> 'draft_only'::text) = 'true'::jsonb) AND (jsonb_typeof((publication_authorization -> 'authorization_sha256'::text)) = 'string'::text) AND ((publication_authorization ->> 'authorization_sha256'::text) ~ '^[0-9a-f]{64}$'::text) AND public.apdl_is_tenant_publication_request((publication_authorization -> 'request'::text), llm_execution_snapshot, (control_metadata ->> 'risk_level'::text)) AND public.apdl_is_tenant_publication_decision((publication_authorization -> 'decision'::text), (control_metadata ->> 'risk_level'::text))) OR public.apdl_is_development_publication_authorization(publication_authorization, llm_execution_snapshot, (control_metadata ->> 'risk_level'::text))) IS TRUE)),
    CONSTRAINT codegen_changesets_repository_target_shape_check CHECK (((repository_target_quarantined AND (repository_grant_id IS NULL) AND (repository_id IS NULL) AND (repository_installation_id IS NULL) AND (repository_full_name IS NULL)) OR ((NOT repository_target_quarantined) AND (repository_grant_id IS NOT NULL) AND (repository_id IS NOT NULL) AND (repository_id > 0) AND (repository_installation_id IS NOT NULL) AND (repository_installation_id > 0) AND (repository_full_name IS NOT NULL) AND ((length(repository_full_name) >= 3) AND (length(repository_full_name) <= 201)) AND (repository_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'::text)))),
    CONSTRAINT codegen_changesets_retry_not_self_check CHECK (((retry_of_changeset_id IS NULL) OR (retry_of_changeset_id <> changeset_id))),
    CONSTRAINT codegen_changesets_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'cloning'::text, 'editing'::text, 'pushing'::text, 'pr_open'::text, 'merged'::text, 'abandoned'::text, 'error'::text]))),
    CONSTRAINT codegen_changesets_tenant_policy_snapshot_check CHECK (((tenant_policy_snapshot IS NULL) OR ((jsonb_typeof(tenant_policy_snapshot) = 'object'::text) AND ((tenant_policy_snapshot ->> 'schema_version'::text) = 'tenant_codegen_connection_policy@1'::text))))
);


--
-- Name: COLUMN codegen_changesets.publication_authorization; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.codegen_changesets.publication_authorization IS 'Strict tenant_publication_authorization@1 or local development_publication_authorization@1.';


--
-- Name: COLUMN codegen_changesets.control_metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.codegen_changesets.control_metadata IS 'Immutable changeset_controls@1 execution authority; never returned as public task data.';


--
-- Name: codegen_ci_remediation_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_ci_remediation_attempts (
    event_id text NOT NULL,
    attempt_id text NOT NULL,
    event_sequence integer NOT NULL,
    changeset_id text NOT NULL,
    repository text NOT NULL,
    pr_number integer NOT NULL,
    failed_head_sha text NOT NULL,
    failure_observation_id text NOT NULL,
    attempt_number integer NOT NULL,
    started_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: codegen_ci_remediation_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_ci_remediation_claims (
    changeset_id text NOT NULL,
    failed_head_sha text NOT NULL,
    claim_scope text NOT NULL,
    failure_observation_id text NOT NULL,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: codegen_ci_verification_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_ci_verification_observations (
    observation_id text NOT NULL,
    changeset_id text NOT NULL,
    repository text NOT NULL,
    pr_number integer NOT NULL,
    head_sha text NOT NULL,
    status text NOT NULL,
    evidence_hash text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: codegen_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_connections (
    project_id text NOT NULL,
    grant_id text NOT NULL,
    default_base_branch text DEFAULT 'main'::text NOT NULL,
    tenant_policy jsonb DEFAULT '{"gates": {"max_files": null, "max_lines": null, "additional_protected_paths": []}, "test_cmd": null, "schema_version": "tenant_codegen_connection_policy@1", "runtime_acceptance": {"enabled": false, "schema_version": "runtime_acceptance_request@1"}}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT codegen_connections_authorized_branch_check CHECK (((length(default_base_branch) >= 1) AND (length(default_base_branch) <= 255) AND (btrim(default_base_branch) <> ''::text) AND (POSITION(('
'::text) IN (default_base_branch)) = 0) AND (POSITION((''::text) IN (default_base_branch)) = 0))),
    CONSTRAINT codegen_connections_authorized_tenant_policy_check CHECK (((jsonb_typeof(tenant_policy) = 'object'::text) AND ((tenant_policy ->> 'schema_version'::text) = 'tenant_codegen_connection_policy@1'::text)))
);


--
-- Name: codegen_llm_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_llm_attempts (
    attempt_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    changeset_id text NOT NULL,
    phase text NOT NULL,
    role text NOT NULL,
    attempt_sequence integer NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    assignment_version bigint NOT NULL,
    credential_id uuid,
    credential_version bigint,
    status text NOT NULL,
    egress_at timestamp with time zone,
    finished_at timestamp with time zone,
    latency_ms bigint,
    input_tokens bigint,
    output_tokens bigint,
    cost_usd_micros bigint,
    error_classification text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT codegen_llm_attempts_assignment_version_check CHECK ((assignment_version > 0)),
    CONSTRAINT codegen_llm_attempts_attempt_sequence_check CHECK ((attempt_sequence > 0)),
    CONSTRAINT codegen_llm_attempts_cost_usd_micros_check CHECK (((cost_usd_micros IS NULL) OR (cost_usd_micros >= 0))),
    CONSTRAINT codegen_llm_attempts_credential_shape_check CHECK ((((credential_id IS NOT NULL) AND (credential_version IS NOT NULL) AND (credential_version > 0)) OR ((status = 'blocked'::text) AND (credential_id IS NULL) AND (credential_version IS NULL)))),
    CONSTRAINT codegen_llm_attempts_error_classification_check CHECK (((error_classification IS NULL) OR (error_classification = ANY (ARRAY['changeset_unavailable'::text, 'execution_authority_unavailable'::text, 'repository_authority_unavailable'::text, 'rollout_authority_unavailable'::text, 'credential_unavailable'::text, 'credential_replaced'::text, 'credential_revoked'::text, 'credential_authentication'::text, 'connection_unavailable'::text, 'model_unavailable'::text, 'provider_authentication'::text, 'provider_permission'::text, 'provider_rate_limited'::text, 'provider_timeout'::text, 'provider_unavailable'::text, 'provider_invalid_request'::text, 'provider_safety_block'::text, 'cancelled'::text, 'unknown'::text])))),
    CONSTRAINT codegen_llm_attempts_input_tokens_check CHECK (((input_tokens IS NULL) OR (input_tokens >= 0))),
    CONSTRAINT codegen_llm_attempts_latency_ms_check CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT codegen_llm_attempts_lifecycle_check CHECK ((((status = 'prepared'::text) AND (egress_at IS NULL) AND (finished_at IS NULL) AND (latency_ms IS NULL) AND (input_tokens IS NULL) AND (output_tokens IS NULL) AND (cost_usd_micros IS NULL) AND (error_classification IS NULL)) OR ((status = 'in_flight'::text) AND (egress_at IS NOT NULL) AND (finished_at IS NULL) AND (latency_ms IS NULL) AND (input_tokens IS NULL) AND (output_tokens IS NULL) AND (cost_usd_micros IS NULL) AND (error_classification IS NULL) AND (credential_id IS NOT NULL)) OR ((status = 'succeeded'::text) AND (egress_at IS NOT NULL) AND (finished_at IS NOT NULL) AND (latency_ms IS NOT NULL) AND (error_classification IS NULL) AND (credential_id IS NOT NULL)) OR ((status = 'failed'::text) AND (egress_at IS NOT NULL) AND (finished_at IS NOT NULL) AND (latency_ms IS NOT NULL) AND (error_classification IS NOT NULL) AND (credential_id IS NOT NULL)) OR ((status = 'blocked'::text) AND (egress_at IS NULL) AND (finished_at IS NOT NULL) AND (latency_ms IS NULL) AND (input_tokens IS NULL) AND (output_tokens IS NULL) AND (cost_usd_micros IS NULL) AND (error_classification IS NOT NULL)) OR ((status = 'cancelled'::text) AND (finished_at IS NOT NULL) AND (error_classification = 'cancelled'::text) AND (((egress_at IS NULL) AND (latency_ms IS NULL) AND (input_tokens IS NULL) AND (output_tokens IS NULL) AND (cost_usd_micros IS NULL)) OR ((egress_at IS NOT NULL) AND (latency_ms IS NOT NULL) AND (credential_id IS NOT NULL) AND (credential_version IS NOT NULL)))))),
    CONSTRAINT codegen_llm_attempts_output_tokens_check CHECK (((output_tokens IS NULL) OR (output_tokens >= 0))),
    CONSTRAINT codegen_llm_attempts_phase_check CHECK ((phase = ANY (ARRAY['brief'::text, 'edit'::text, 'review'::text, 'repair'::text]))),
    CONSTRAINT codegen_llm_attempts_phase_role_check CHECK ((((phase = ANY (ARRAY['brief'::text, 'review'::text])) AND (role = 'helper'::text)) OR ((phase = ANY (ARRAY['edit'::text, 'repair'::text])) AND (role = 'editor'::text)))),
    CONSTRAINT codegen_llm_attempts_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT codegen_llm_attempts_role_check CHECK ((role = ANY (ARRAY['editor'::text, 'helper'::text]))),
    CONSTRAINT codegen_llm_attempts_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'in_flight'::text, 'succeeded'::text, 'failed'::text, 'blocked'::text, 'cancelled'::text]))),
    CONSTRAINT codegen_llm_attempts_usage_shape_check CHECK ((((input_tokens IS NULL) AND (output_tokens IS NULL) AND (cost_usd_micros IS NULL)) OR ((input_tokens IS NOT NULL) AND (output_tokens IS NOT NULL) AND (cost_usd_micros IS NOT NULL))))
);


--
-- Name: codegen_project_model_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_project_model_assignments (
    project_id text NOT NULL,
    role text NOT NULL,
    provider text NOT NULL,
    model_id text NOT NULL,
    assignment_version bigint NOT NULL,
    connection_version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    catalog_version text NOT NULL,
    assigned_by_actor text NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT codegen_project_model_assignments_assigned_by_actor_check CHECK (((assigned_by_actor = btrim(assigned_by_actor)) AND ((length(assigned_by_actor) >= 1) AND (length(assigned_by_actor) <= 512)) AND (POSITION((chr(10)) IN (assigned_by_actor)) = 0) AND (POSITION((chr(13)) IN (assigned_by_actor)) = 0))),
    CONSTRAINT codegen_project_model_assignments_assignment_version_check CHECK ((assignment_version > 0)),
    CONSTRAINT codegen_project_model_assignments_catalog_version_check CHECK ((catalog_version ~ '^codegen-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT codegen_project_model_assignments_connection_version_check CHECK ((connection_version > 0)),
    CONSTRAINT codegen_project_model_assignments_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT codegen_project_model_assignments_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT codegen_project_model_assignments_role_check CHECK ((role = ANY (ARRAY['editor'::text, 'helper'::text])))
);


--
-- Name: codegen_project_provider_connection_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_project_provider_connection_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    connection_version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    credential_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    model_count integer NOT NULL,
    catalog_version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT codegen_project_provider_connection_au_connection_version_check CHECK ((connection_version > 0)),
    CONSTRAINT codegen_project_provider_connection_aud_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT codegen_project_provider_connection_audit_action_check CHECK ((action = ANY (ARRAY['connect'::text, 'replace'::text, 'refresh'::text, 'revoke'::text]))),
    CONSTRAINT codegen_project_provider_connection_audit_catalog_version_check CHECK ((catalog_version ~ '^codegen-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT codegen_project_provider_connection_audit_model_count_check CHECK (((model_count >= 0) AND (model_count <= 1000))),
    CONSTRAINT codegen_project_provider_connection_audit_outcome_check CHECK ((outcome = 'succeeded'::text)),
    CONSTRAINT codegen_project_provider_connection_audit_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT codegen_project_provider_connection_audit_shape_check CHECK ((((action = 'revoke'::text) AND (model_count = 0)) OR ((action <> 'revoke'::text) AND (model_count > 0))))
);


--
-- Name: codegen_project_provider_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_project_provider_connections (
    project_id text NOT NULL,
    provider text NOT NULL,
    version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    state text NOT NULL,
    credential_id uuid NOT NULL,
    catalog_version text NOT NULL,
    validated_at timestamp with time zone NOT NULL,
    validated_by_actor text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT codegen_project_provider_connections_catalog_version_check CHECK ((catalog_version ~ '^codegen-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT codegen_project_provider_connections_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT codegen_project_provider_connections_lifecycle_check CHECK ((((state = 'active'::text) AND (revoked_at IS NULL)) OR ((state = 'revoked'::text) AND (revoked_at IS NOT NULL)))),
    CONSTRAINT codegen_project_provider_connections_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT codegen_project_provider_connections_state_check CHECK ((state = ANY (ARRAY['active'::text, 'revoked'::text]))),
    CONSTRAINT codegen_project_provider_connections_validated_by_actor_check CHECK (((validated_by_actor = btrim(validated_by_actor)) AND ((length(validated_by_actor) >= 1) AND (length(validated_by_actor) <= 512)) AND (POSITION((chr(10)) IN (validated_by_actor)) = 0) AND (POSITION((chr(13)) IN (validated_by_actor)) = 0))),
    CONSTRAINT codegen_project_provider_connections_version_check CHECK ((version > 0))
);


--
-- Name: codegen_project_provider_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_project_provider_models (
    project_id text NOT NULL,
    provider text NOT NULL,
    connection_version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    schema_version text NOT NULL,
    model_id text NOT NULL,
    display_name text NOT NULL,
    supported_roles text[] NOT NULL,
    catalog_version text NOT NULL,
    context_window_tokens integer NOT NULL,
    supports_tool_calling boolean NOT NULL,
    supports_structured_output boolean NOT NULL,
    data_residency text NOT NULL,
    allowed_data_classifications text[] NOT NULL,
    input_cost_per_million_tokens_usd_micros bigint NOT NULL,
    output_cost_per_million_tokens_usd_micros bigint NOT NULL,
    pricing_status text NOT NULL,
    discovered_at timestamp with time zone NOT NULL,
    CONSTRAINT codegen_project_provider_mod_allowed_data_classifications_check CHECK (((allowed_data_classifications = ARRAY['public'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text, 'confidential'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text, 'confidential'::text, 'restricted'::text]))),
    CONSTRAINT codegen_project_provider_mod_input_cost_per_million_token_check CHECK ((input_cost_per_million_tokens_usd_micros >= 0)),
    CONSTRAINT codegen_project_provider_mod_output_cost_per_million_toke_check CHECK ((output_cost_per_million_tokens_usd_micros >= 0)),
    CONSTRAINT codegen_project_provider_models_catalog_version_check CHECK ((catalog_version ~ '^codegen-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT codegen_project_provider_models_connection_version_check CHECK ((connection_version > 0)),
    CONSTRAINT codegen_project_provider_models_context_window_tokens_check CHECK ((context_window_tokens >= 16000)),
    CONSTRAINT codegen_project_provider_models_data_residency_check CHECK ((data_residency = ANY (ARRAY['ca'::text, 'us'::text, 'eu'::text, 'global'::text]))),
    CONSTRAINT codegen_project_provider_models_display_name_check CHECK (((display_name = btrim(display_name)) AND ((length(display_name) >= 1) AND (length(display_name) <= 200)))),
    CONSTRAINT codegen_project_provider_models_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT codegen_project_provider_models_model_id_check CHECK (((length(model_id) >= 1) AND (length(model_id) <= 128) AND (model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'::text))),
    CONSTRAINT codegen_project_provider_models_pricing_status_check CHECK ((pricing_status = 'catalog_reviewed'::text)),
    CONSTRAINT codegen_project_provider_models_schema_version_check CHECK ((schema_version = 'codegen_provider_model@1'::text)),
    CONSTRAINT codegen_project_provider_models_supported_roles_check CHECK (((supported_roles = ARRAY['editor'::text]) OR (supported_roles = ARRAY['helper'::text]) OR (supported_roles = ARRAY['editor'::text, 'helper'::text])))
);


--
-- Name: codegen_pull_request_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_pull_request_observations (
    observation_id text NOT NULL,
    delivery_id text,
    changeset_id text NOT NULL,
    repository text NOT NULL,
    pr_number integer NOT NULL,
    head_sha text NOT NULL,
    status text NOT NULL,
    github_updated_at timestamp with time zone NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: codegen_pull_request_publication_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_pull_request_publication_events (
    event_id text NOT NULL,
    event_sequence bigint NOT NULL,
    changeset_id text NOT NULL,
    event_type text NOT NULL,
    intent_event_id text,
    cleanup_request_event_id text,
    pr_number integer,
    github_url text,
    recorded_at timestamp with time zone NOT NULL,
    inserted_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb NOT NULL,
    CONSTRAINT codegen_pr_publication_cleanup_link_check CHECK ((((event_type = 'cleanup_confirmed'::text) AND (cleanup_request_event_id IS NOT NULL) AND (cleanup_request_event_id ~ '^cpub_[0-9a-f]{32}$'::text)) OR ((event_type <> 'cleanup_confirmed'::text) AND (cleanup_request_event_id IS NULL)))),
    CONSTRAINT codegen_pr_publication_event_id_check CHECK ((event_id ~ '^cpub_[0-9a-f]{32}$'::text)),
    CONSTRAINT codegen_pr_publication_event_type_check CHECK ((event_type = ANY (ARRAY['intent_recorded'::text, 'branch_published'::text, 'create_accepted'::text, 'identity_validated'::text, 'cleanup_requested'::text, 'cleanup_confirmed'::text, 'manual_intervention'::text, 'recovery_deferred'::text]))),
    CONSTRAINT codegen_pr_publication_intent_link_check CHECK ((((event_type = 'intent_recorded'::text) AND (intent_event_id IS NULL)) OR ((event_type <> 'intent_recorded'::text) AND (intent_event_id IS NOT NULL) AND (intent_event_id ~ '^cpub_[0-9a-f]{32}$'::text)))),
    CONSTRAINT codegen_pr_publication_payload_identity_check CHECK (((payload ?& ARRAY['schema_version'::text, 'event_id'::text, 'changeset_id'::text, 'recorded_at'::text, 'event_type'::text]) AND (NOT ((payload ->> 'event_id'::text) IS DISTINCT FROM event_id)) AND (NOT ((payload ->> 'changeset_id'::text) IS DISTINCT FROM changeset_id)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type)) AND (((event_type = 'intent_recorded'::text) AND (NOT (payload ? 'intent_event_id'::text))) OR ((event_type <> 'intent_recorded'::text) AND (payload ? 'intent_event_id'::text) AND (NOT ((payload ->> 'intent_event_id'::text) IS DISTINCT FROM intent_event_id)))) AND (((event_type = 'cleanup_confirmed'::text) AND (payload ? 'cleanup_request_event_id'::text) AND (NOT ((payload ->> 'cleanup_request_event_id'::text) IS DISTINCT FROM cleanup_request_event_id))) OR ((event_type <> 'cleanup_confirmed'::text) AND (NOT (payload ? 'cleanup_request_event_id'::text)))))),
    CONSTRAINT codegen_pr_publication_payload_object_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT codegen_pr_publication_payload_type_check CHECK ((((event_type = 'intent_recorded'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_publication_intent@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'branch_published'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_branch_published@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'create_accepted'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_create_accepted@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'identity_validated'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_identity_validated@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'cleanup_requested'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_cleanup_requested@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'cleanup_confirmed'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_cleanup_confirmed@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'manual_intervention'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_manual_intervention@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))) OR ((event_type = 'recovery_deferred'::text) AND (NOT ((payload ->> 'schema_version'::text) IS DISTINCT FROM 'pull_request_recovery_deferred@1'::text)) AND (NOT ((payload ->> 'event_type'::text) IS DISTINCT FROM event_type))))),
    CONSTRAINT codegen_pr_publication_pr_number_check CHECK (((pr_number IS NULL) OR (pr_number > 0)))
);


--
-- Name: TABLE codegen_pull_request_publication_events; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.codegen_pull_request_publication_events IS 'Append-only intent, accepted GitHub identity, validation, cleanup, and recovery journal.';


--
-- Name: codegen_pull_request_publication_events_event_sequence_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.codegen_pull_request_publication_events ALTER COLUMN event_sequence ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.codegen_pull_request_publication_events_event_sequence_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: codegen_runtime_collection_claims; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_runtime_collection_claims (
    changeset_id text NOT NULL,
    head_sha text NOT NULL,
    ci_observation_id text NOT NULL,
    claimed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: codegen_runtime_evidence_observations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.codegen_runtime_evidence_observations (
    observation_id text NOT NULL,
    changeset_id text NOT NULL,
    repository text NOT NULL,
    pr_number integer NOT NULL,
    head_sha text NOT NULL,
    ci_observation_id text NOT NULL,
    evidence_hash text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: config_exposure_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_exposure_receipts (
    project_id text NOT NULL,
    message_id text NOT NULL,
    canonical_payload jsonb NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT config_exposure_receipts_message_check CHECK ((btrim(message_id) <> ''::text)),
    CONSTRAINT config_exposure_receipts_payload_check CHECK (((jsonb_typeof(canonical_payload) = 'object'::text) AND (jsonb_typeof((canonical_payload -> 'stream_key'::text)) = 'string'::text) AND (btrim((canonical_payload ->> 'stream_key'::text)) <> ''::text) AND (jsonb_typeof((canonical_payload -> 'event'::text)) = 'object'::text) AND (NOT ((canonical_payload -> 'event'::text) ? 'timestamp'::text)) AND ((canonical_payload #>> '{event,message_id}'::text[]) = message_id))),
    CONSTRAINT config_exposure_receipts_project_check CHECK ((btrim(project_id) <> ''::text)),
    CONSTRAINT config_exposure_receipts_server_timestamp_check CHECK ((NOT ((canonical_payload -> 'event'::text) ? 'server_timestamp'::text))),
    CONSTRAINT config_exposure_receipts_time_check CHECK ((last_seen_at >= first_seen_at))
);


--
-- Name: TABLE config_exposure_receipts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.config_exposure_receipts IS 'Exposure idempotency/conflict ledger retained beyond ClickHouse event TTL';


--
-- Name: COLUMN config_exposure_receipts.canonical_payload; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.config_exposure_receipts.canonical_payload IS 'Exact stream/event exposure payload with generated event times removed';


--
-- Name: COLUMN config_exposure_receipts.last_seen_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.config_exposure_receipts.last_seen_at IS 'Receipt retention anchor; migration backfill includes terminal delivery time';


--
-- Name: config_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_outbox (
    id bigint NOT NULL,
    project_id text NOT NULL,
    kind text NOT NULL,
    dedup_key text NOT NULL,
    payload jsonb NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    processed_at timestamp with time zone,
    last_error text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    quarantined_at timestamp with time zone,
    failure_class text,
    failure_code text,
    CONSTRAINT config_outbox_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT config_outbox_kind_check CHECK ((kind = ANY (ARRAY['flag_change'::text, 'experiment_change'::text, 'exposure'::text]))),
    CONSTRAINT config_outbox_project_version_check CHECK (((kind <> ALL (ARRAY['flag_change'::text, 'experiment_change'::text])) OR ((payload ? 'project_version'::text) AND (NOT (jsonb_typeof((payload -> 'project_version'::text)) IS DISTINCT FROM 'number'::text)) AND COALESCE(((payload ->> 'project_version'::text) ~ '^[1-9][0-9]*$'::text), false)))),
    CONSTRAINT config_outbox_quarantine_evidence_check CHECK ((((quarantined_at IS NULL) AND (failure_class IS NULL) AND (failure_code IS NULL)) OR ((quarantined_at IS NOT NULL) AND (failure_class = ANY (ARRAY['permanent'::text, 'attempts_exhausted'::text])) AND (failure_code IS NOT NULL) AND (btrim(failure_code) <> ''::text) AND (last_error <> ''::text)))),
    CONSTRAINT config_outbox_terminal_state_check CHECK (((processed_at IS NULL) OR (quarantined_at IS NULL)))
);


--
-- Name: COLUMN config_outbox.quarantined_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.config_outbox.quarantined_at IS 'Terminal failure time; quarantined rows retain payload and no longer block a lane';


--
-- Name: COLUMN config_outbox.failure_class; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.config_outbox.failure_class IS 'Canonical terminal class: permanent or attempts_exhausted';


--
-- Name: COLUMN config_outbox.failure_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.config_outbox.failure_code IS 'Bounded machine-readable terminal failure code';


--
-- Name: config_outbox_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.config_outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: config_outbox_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.config_outbox_id_seq OWNED BY public.config_outbox.id;


--
-- Name: config_outbox_operator_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_outbox_operator_log (
    id bigint NOT NULL,
    project_id text NOT NULL,
    outbox_id bigint NOT NULL,
    action text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    kind text NOT NULL,
    failure_class text NOT NULL,
    failure_code text NOT NULL,
    payload_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT config_outbox_operator_log_action_check CHECK ((action = ANY (ARRAY['replay'::text, 'discard'::text]))),
    CONSTRAINT config_outbox_operator_log_actor_check CHECK ((btrim(actor) <> ''::text)),
    CONSTRAINT config_outbox_operator_log_failure_class_check CHECK ((btrim(failure_class) <> ''::text)),
    CONSTRAINT config_outbox_operator_log_failure_code_check CHECK ((btrim(failure_code) <> ''::text)),
    CONSTRAINT config_outbox_operator_log_kind_check CHECK ((btrim(kind) <> ''::text)),
    CONSTRAINT config_outbox_operator_log_outbox_id_check CHECK ((outbox_id >= 1)),
    CONSTRAINT config_outbox_operator_log_payload_sha256_check CHECK ((payload_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT config_outbox_operator_log_project_id_check CHECK ((btrim(project_id) <> ''::text)),
    CONSTRAINT config_outbox_operator_log_reason_check CHECK ((btrim(reason) <> ''::text))
);


--
-- Name: TABLE config_outbox_operator_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.config_outbox_operator_log IS 'Immutable replay/discard evidence without duplicated outbox payloads';


--
-- Name: config_outbox_operator_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.config_outbox_operator_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: config_outbox_operator_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.config_outbox_operator_log_id_seq OWNED BY public.config_outbox_operator_log.id;


--
-- Name: config_project_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config_project_versions (
    project_id text NOT NULL,
    project_version bigint NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT config_project_versions_project_version_check CHECK ((project_version >= 0))
);


--
-- Name: custom_agent_test_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.custom_agent_test_runs (
    test_run_id text NOT NULL,
    project_id text NOT NULL,
    agent_slug text NOT NULL,
    model_tier text NOT NULL,
    time_range_days integer NOT NULL,
    max_tool_steps integer NOT NULL,
    allowed_tool_count integer NOT NULL,
    configured_preset_count integer NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    preset_tool_calls integer DEFAULT 0 NOT NULL,
    agentic_tool_calls integer DEFAULT 0 NOT NULL,
    llm_calls integer DEFAULT 0 NOT NULL,
    llm_latency_ms integer,
    total_latency_ms integer,
    error text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    lease_expires_at timestamp with time zone NOT NULL,
    CONSTRAINT custom_agent_test_runs_counts_check CHECK (((allowed_tool_count >= 0) AND (configured_preset_count >= 0) AND (preset_tool_calls >= 0) AND (agentic_tool_calls >= 0) AND (llm_calls >= 0))),
    CONSTRAINT custom_agent_test_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text]))),
    CONSTRAINT custom_agent_test_runs_time_range_check CHECK (((time_range_days >= 1) AND (time_range_days <= 90))),
    CONSTRAINT custom_agent_test_runs_tool_steps_check CHECK (((max_tool_steps >= 1) AND (max_tool_steps <= 16)))
);


--
-- Name: custom_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.custom_agents (
    agent_id text NOT NULL,
    project_id text NOT NULL,
    slug text NOT NULL,
    display_name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    system_prompt text NOT NULL,
    user_prompt_template text NOT NULL,
    model_tier text DEFAULT 'reasoning'::text NOT NULL,
    tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    preset_tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    requires jsonb DEFAULT '[]'::jsonb NOT NULL,
    produces text NOT NULL,
    memory_query text,
    memory_top_k integer DEFAULT 5 NOT NULL,
    pipeline_order integer DEFAULT 100 NOT NULL,
    max_tool_steps integer DEFAULT 8 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT custom_agents_model_tier_check CHECK ((model_tier = ANY (ARRAY['fast'::text, 'reasoning'::text]))),
    CONSTRAINT custom_agents_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))
);


--
-- Name: designed_experiments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.designed_experiments (
    project_id text NOT NULL,
    experiment_id text NOT NULL,
    run_id text,
    insight_key text DEFAULT ''::text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    hypothesis text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'designed'::text NOT NULL,
    changeset_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: event_pipeline_watermarks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_pipeline_watermarks (
    project_id text NOT NULL,
    stream_key text NOT NULL,
    provenance_start_stream_id text NOT NULL,
    contiguous_stream_id text NOT NULL,
    consumer_group_entries_read bigint NOT NULL,
    status text NOT NULL,
    failure_reason text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT event_pipeline_watermarks_entries_read_check CHECK ((consumer_group_entries_read >= 0)),
    CONSTRAINT event_pipeline_watermarks_frontier_id_check CHECK (((contiguous_stream_id ~ '^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$'::text) AND ((split_part(contiguous_stream_id, '-'::text, 1))::numeric <= '18446744073709551615'::numeric) AND ((split_part(contiguous_stream_id, '-'::text, 2))::numeric <= '18446744073709551615'::numeric))),
    CONSTRAINT event_pipeline_watermarks_project_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT event_pipeline_watermarks_range_check CHECK ((((split_part(provenance_start_stream_id, '-'::text, 1))::numeric < (split_part(contiguous_stream_id, '-'::text, 1))::numeric) OR (((split_part(provenance_start_stream_id, '-'::text, 1))::numeric = (split_part(contiguous_stream_id, '-'::text, 1))::numeric) AND ((split_part(provenance_start_stream_id, '-'::text, 2))::numeric <= (split_part(contiguous_stream_id, '-'::text, 2))::numeric)))),
    CONSTRAINT event_pipeline_watermarks_start_id_check CHECK (((provenance_start_stream_id ~ '^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$'::text) AND ((split_part(provenance_start_stream_id, '-'::text, 1))::numeric <= '18446744073709551615'::numeric) AND ((split_part(provenance_start_stream_id, '-'::text, 2))::numeric <= '18446744073709551615'::numeric))),
    CONSTRAINT event_pipeline_watermarks_status_check CHECK ((((status = 'healthy'::text) AND (failure_reason IS NULL)) OR ((status = 'degraded'::text) AND (failure_reason = ANY (ARRAY['legacy_state_unverifiable'::text, 'dead_lettered_event'::text, 'lost_pending_entry'::text, 'stream_state_unverifiable'::text]))))),
    CONSTRAINT event_pipeline_watermarks_stream_check CHECK ((stream_key = ('events:raw:'::text || project_id)))
);


--
-- Name: TABLE event_pipeline_watermarks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.event_pipeline_watermarks IS 'Per-project contiguous Redis delivery frontier persisted only after ClickHouse durability and Redis ACK';


--
-- Name: experiment_analysis_boundaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_analysis_boundaries (
    project_id text NOT NULL,
    experiment_key text NOT NULL,
    config_version bigint NOT NULL,
    stream_key text NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    marker_token text NOT NULL,
    marker_stream_id text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    marked_at timestamp with time zone,
    marker_publish_state text DEFAULT 'pending'::text NOT NULL,
    marker_publish_attempts smallint DEFAULT 0 NOT NULL,
    marker_publish_next_attempt_at timestamp with time zone DEFAULT now(),
    marker_publish_failure_code text,
    marker_publish_last_error_at timestamp with time zone,
    marker_publish_quarantined_at timestamp with time zone,
    marker_publish_observed_stream_id text,
    CONSTRAINT experiment_analysis_boundaries_key_check CHECK ((experiment_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'::text)),
    CONSTRAINT experiment_analysis_boundaries_marker_check CHECK ((((marker_stream_id IS NULL) AND (marked_at IS NULL)) OR ((marker_stream_id ~ '^[1-9][0-9]*-(0|[1-9][0-9]*)$'::text) AND ((split_part(marker_stream_id, '-'::text, 1))::numeric <= '18446744073709551615'::numeric) AND ((split_part(marker_stream_id, '-'::text, 2))::numeric <= '18446744073709551615'::numeric) AND (marked_at IS NOT NULL)))),
    CONSTRAINT experiment_analysis_boundaries_project_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT experiment_analysis_boundaries_publish_attempts_check CHECK (((marker_publish_attempts >= 0) AND (marker_publish_attempts <= 5))),
    CONSTRAINT experiment_analysis_boundaries_publish_failure_check CHECK (((marker_publish_failure_code IS NULL) OR (marker_publish_failure_code = ANY (ARRAY['event_stream_capacity'::text, 'redis_publish_failed'::text, 'invalid_redis_marker_id'::text, 'boundary_authority_update_failed'::text, 'boundary_authority_update_invalid'::text, 'invalid_boundary_marker_dedup'::text, 'invalid_stream_authority'::text, 'invalid_marker_token'::text, 'unexpected_publish_failure'::text])))),
    CONSTRAINT experiment_analysis_boundaries_publish_history_check CHECK ((((marker_publish_attempts = 0) AND (marker_publish_failure_code IS NULL) AND (marker_publish_last_error_at IS NULL)) OR ((marker_publish_attempts > 0) AND (marker_publish_failure_code IS NOT NULL) AND (marker_publish_last_error_at IS NOT NULL) AND (marker_publish_last_error_at >= requested_at)))),
    CONSTRAINT experiment_analysis_boundaries_publish_observed_id_check CHECK (((marker_publish_observed_stream_id IS NULL) OR ((marker_publish_observed_stream_id ~ '^[1-9][0-9]*-(0|[1-9][0-9]*)$'::text) AND ((split_part(marker_publish_observed_stream_id, '-'::text, 1))::numeric <= '18446744073709551615'::numeric) AND ((split_part(marker_publish_observed_stream_id, '-'::text, 2))::numeric <= '18446744073709551615'::numeric)))),
    CONSTRAINT experiment_analysis_boundaries_publish_state_check CHECK ((((marker_publish_state = 'pending'::text) AND (marker_stream_id IS NULL) AND (marked_at IS NULL) AND (marker_publish_attempts < 5) AND (marker_publish_next_attempt_at IS NOT NULL) AND (marker_publish_quarantined_at IS NULL) AND ((marker_publish_attempts > 0) OR (marker_publish_observed_stream_id IS NULL)) AND ((marker_publish_attempts = 0) OR ((marker_publish_failure_code = ANY (ARRAY['event_stream_capacity'::text, 'redis_publish_failed'::text, 'boundary_authority_update_failed'::text, 'unexpected_publish_failure'::text])) AND (marker_publish_next_attempt_at > marker_publish_last_error_at)))) OR ((marker_publish_state = 'published'::text) AND (marker_stream_id IS NOT NULL) AND (marked_at IS NOT NULL) AND (marker_publish_next_attempt_at IS NULL) AND (marker_publish_quarantined_at IS NULL) AND (marker_publish_observed_stream_id = marker_stream_id) AND ((marker_publish_attempts = 0) OR (marker_publish_failure_code = ANY (ARRAY['event_stream_capacity'::text, 'redis_publish_failed'::text, 'boundary_authority_update_failed'::text, 'unexpected_publish_failure'::text])))) OR ((marker_publish_state = 'quarantined'::text) AND (marker_stream_id IS NULL) AND (marked_at IS NULL) AND (marker_publish_attempts > 0) AND (marker_publish_next_attempt_at IS NULL) AND (marker_publish_quarantined_at IS NOT NULL) AND (marker_publish_quarantined_at >= marker_publish_last_error_at) AND (((marker_publish_attempts = 5) AND (marker_publish_failure_code = ANY (ARRAY['event_stream_capacity'::text, 'redis_publish_failed'::text, 'boundary_authority_update_failed'::text, 'unexpected_publish_failure'::text]))) OR (marker_publish_failure_code = ANY (ARRAY['invalid_redis_marker_id'::text, 'boundary_authority_update_invalid'::text, 'invalid_boundary_marker_dedup'::text, 'invalid_stream_authority'::text, 'invalid_marker_token'::text])))))),
    CONSTRAINT experiment_analysis_boundaries_stream_check CHECK ((stream_key = ('events:raw:'::text || project_id))),
    CONSTRAINT experiment_analysis_boundaries_token_check CHECK ((marker_token ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT experiment_analysis_boundaries_version_check CHECK ((config_version > 0)),
    CONSTRAINT experiment_analysis_boundaries_window_check CHECK ((window_end > window_start))
);


--
-- Name: TABLE experiment_analysis_boundaries; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.experiment_analysis_boundaries IS 'Immutable experiment window plus a deterministic marker in its project event stream';


--
-- Name: COLUMN experiment_analysis_boundaries.marker_publish_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiment_analysis_boundaries.marker_publish_state IS 'Monotone pending, published, or quarantined marker publication state';


--
-- Name: COLUMN experiment_analysis_boundaries.marker_publish_failure_code; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiment_analysis_boundaries.marker_publish_failure_code IS 'Bounded safe code for the most recent publication failure';


--
-- Name: COLUMN experiment_analysis_boundaries.marker_publish_observed_stream_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiment_analysis_boundaries.marker_publish_observed_stream_id IS 'First validated Redis marker ID, retained across retry or quarantine';


--
-- Name: experiment_analysis_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_analysis_snapshots (
    project_id text NOT NULL,
    experiment_key text NOT NULL,
    config_version bigint NOT NULL,
    boundary_stream_id text NOT NULL,
    snapshot_payload jsonb NOT NULL,
    snapshot_sha256 text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT experiment_analysis_snapshots_boundary_check CHECK (((boundary_stream_id ~ '^[1-9][0-9]*-(0|[1-9][0-9]*)$'::text) AND ((split_part(boundary_stream_id, '-'::text, 1))::numeric <= '18446744073709551615'::numeric) AND ((split_part(boundary_stream_id, '-'::text, 2))::numeric <= '18446744073709551615'::numeric))),
    CONSTRAINT experiment_analysis_snapshots_payload_check CHECK (((jsonb_typeof(snapshot_payload) = 'object'::text) AND ((snapshot_payload ->> 'analysis_status'::text) = 'decision_snapshot'::text) AND ((snapshot_payload ->> 'data_completeness'::text) = 'verified'::text) AND ((snapshot_payload ->> 'experiment_key'::text) = experiment_key) AND (((snapshot_payload ->> 'config_version'::text))::bigint = config_version))),
    CONSTRAINT experiment_analysis_snapshots_sha256_check CHECK ((snapshot_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: TABLE experiment_analysis_snapshots; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.experiment_analysis_snapshots IS 'Immutable verified experiment decision payload frozen at one covered stream boundary';


--
-- Name: experiment_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_audit_log (
    id bigint NOT NULL,
    project_id text NOT NULL,
    experiment_key text NOT NULL,
    action text NOT NULL,
    actor text NOT NULL,
    previous_version integer,
    new_version integer,
    before jsonb,
    after jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT experiment_audit_log_action_check CHECK ((action = ANY (ARRAY['experiment_created'::text, 'experiment_updated'::text, 'experiment_status_changed'::text, 'experiment_archived'::text, 'experiment_deleted'::text]))),
    CONSTRAINT experiment_audit_log_actor_check CHECK ((btrim(actor) <> ''::text)),
    CONSTRAINT experiment_audit_log_new_version_check CHECK (((new_version IS NULL) OR (new_version >= 1))),
    CONSTRAINT experiment_audit_log_previous_version_check CHECK (((previous_version IS NULL) OR (previous_version >= 1)))
);


--
-- Name: TABLE experiment_audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.experiment_audit_log IS 'Append-only experiment lifecycle evidence retained across draft deletion';


--
-- Name: experiment_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.experiment_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: experiment_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.experiment_audit_log_id_seq OWNED BY public.experiment_audit_log.id;


--
-- Name: experiment_audit_purge_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_audit_purge_log (
    id bigint NOT NULL,
    project_id text NOT NULL,
    purge_before timestamp with time zone NOT NULL,
    deleted_rows bigint NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT experiment_audit_purge_log_actor_check CHECK ((btrim(actor) <> ''::text)),
    CONSTRAINT experiment_audit_purge_log_deleted_rows_check CHECK ((deleted_rows >= 0)),
    CONSTRAINT experiment_audit_purge_log_project_id_check CHECK ((btrim(project_id) <> ''::text)),
    CONSTRAINT experiment_audit_purge_log_reason_check CHECK ((btrim(reason) <> ''::text))
);


--
-- Name: TABLE experiment_audit_purge_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.experiment_audit_purge_log IS 'Immutable evidence for project-scoped experiment snapshot purges';


--
-- Name: experiment_audit_purge_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.experiment_audit_purge_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: experiment_audit_purge_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.experiment_audit_purge_log_id_seq OWNED BY public.experiment_audit_purge_log.id;


--
-- Name: experiment_verdicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment_verdicts (
    id bigint NOT NULL,
    project_id text NOT NULL,
    experiment_id text NOT NULL,
    run_id text,
    verdict text NOT NULL,
    reasoning text DEFAULT ''::text NOT NULL,
    results jsonb DEFAULT '{}'::jsonb,
    durable_feature text DEFAULT ''::text NOT NULL,
    consumed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: experiment_verdicts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.experiment_verdicts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: experiment_verdicts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.experiment_verdicts_id_seq OWNED BY public.experiment_verdicts.id;


--
-- Name: experiments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiments (
    key text NOT NULL,
    project_id text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    flag_key text DEFAULT ''::text NOT NULL,
    default_variant text DEFAULT 'control'::text NOT NULL,
    variants_json text DEFAULT '[]'::text NOT NULL,
    targeting_rules_json text DEFAULT '[]'::text NOT NULL,
    primary_metric_json text DEFAULT '{}'::text NOT NULL,
    traffic_percentage double precision DEFAULT 100.0 NOT NULL,
    start_date timestamp with time zone,
    end_date timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    statistical_plan jsonb,
    creation_idempotency_key text,
    creation_idempotency_request_sha256 character(64),
    archived_at timestamp with time zone,
    archived_by text,
    minimum_exposure_config_version integer,
    bucket_by text NOT NULL,
    CONSTRAINT experiments_archive_metadata_check CHECK ((((archived_at IS NULL) AND (archived_by IS NULL)) OR ((archived_at IS NOT NULL) AND (archived_by IS NOT NULL) AND (btrim(archived_by) <> ''::text)))),
    CONSTRAINT experiments_bucket_by_check CHECK ((bucket_by = ANY (ARRAY['anonymous_id'::text, 'user_id'::text]))),
    CONSTRAINT experiments_creation_idempotency_key_check CHECK (((creation_idempotency_key IS NULL) OR (creation_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$'::text))),
    CONSTRAINT experiments_creation_idempotency_pair_check CHECK ((((creation_idempotency_key IS NULL) AND (creation_idempotency_request_sha256 IS NULL)) OR ((creation_idempotency_key IS NOT NULL) AND (creation_idempotency_request_sha256 ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT experiments_date_window_check CHECK (((end_date IS NULL) OR ((start_date IS NOT NULL) AND (end_date > start_date)))),
    CONSTRAINT experiments_minimum_exposure_version_check CHECK ((((status = 'draft'::text) AND (minimum_exposure_config_version IS NULL)) OR ((status <> 'draft'::text) AND (minimum_exposure_config_version >= 1)))),
    CONSTRAINT experiments_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'scheduled'::text, 'running'::text, 'completed'::text, 'stopped'::text]))),
    CONSTRAINT experiments_targeting_rules_check CHECK (public.apdl_experiment_rules_are_canonical(targeting_rules_json)),
    CONSTRAINT experiments_traffic_percentage_check CHECK ((((traffic_percentage)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])) AND (traffic_percentage >= (0.0)::double precision) AND (traffic_percentage <= (100.0)::double precision))),
    CONSTRAINT experiments_variants_canonical_check CHECK (public.apdl_experiment_variants_are_canonical(variants_json, default_variant)),
    CONSTRAINT experiments_version_check CHECK ((version >= 1))
);


--
-- Name: COLUMN experiments.statistical_plan; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiments.statistical_plan IS 'Immutable fixed-horizon nominal plan; rows without a plan cannot enter traffic';


--
-- Name: COLUMN experiments.archived_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiments.archived_at IS 'Tombstone time for an experiment that left draft; archived rows are immutable';


--
-- Name: COLUMN experiments.minimum_exposure_config_version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiments.minimum_exposure_config_version IS 'Lowest backing-flag version whose assignment exposure is valid for analysis';


--
-- Name: COLUMN experiments.bucket_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.experiments.bucket_by IS 'Explicit immutable-after-draft experiment actor identity: anonymous_id or user_id';


--
-- Name: CONSTRAINT experiments_minimum_exposure_version_check ON experiments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON CONSTRAINT experiments_minimum_exposure_version_check ON public.experiments IS 'Load-bearing with apdl_enforce_experiment_enrollment_immutability: blocks status-only draft downgrades while the trigger blocks clearing the exposure-version floor';


--
-- Name: feature_proposals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feature_proposals (
    proposal_id text NOT NULL,
    project_id text NOT NULL,
    run_id text,
    claim_run_id text,
    status text DEFAULT 'approved'::text NOT NULL,
    title text NOT NULL,
    spec text NOT NULL,
    priority text,
    changeset_id text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: flag_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flag_audit_log (
    id bigint NOT NULL,
    project_id text NOT NULL,
    flag_key text NOT NULL,
    action text NOT NULL,
    actor text DEFAULT 'system'::text NOT NULL,
    previous_version integer,
    new_version integer,
    before jsonb,
    after jsonb,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    origin text DEFAULT 'manual'::text NOT NULL,
    CONSTRAINT flag_audit_log_origin_check CHECK ((origin = ANY (ARRAY['manual'::text, 'automation'::text, 'experiment'::text, 'scheduler'::text, 'migration'::text])))
);


--
-- Name: flag_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.flag_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: flag_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.flag_audit_log_id_seq OWNED BY public.flag_audit_log.id;


--
-- Name: flags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flags (
    key text NOT NULL,
    project_id text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    state text DEFAULT 'draft'::text NOT NULL,
    owners jsonb DEFAULT '[]'::jsonb NOT NULL,
    review_by text,
    enabled boolean DEFAULT false NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    default_variant text DEFAULT 'control'::text NOT NULL,
    variants jsonb DEFAULT '[{"key": "control", "weight": 1}, {"key": "treatment", "weight": 1}]'::jsonb NOT NULL,
    rules jsonb DEFAULT '[]'::jsonb NOT NULL,
    fallthrough jsonb DEFAULT '{"rollout": {"bucket_by": "user_id", "percentage": 0}}'::jsonb NOT NULL,
    salt text DEFAULT md5(((random())::text || (clock_timestamp())::text)) NOT NULL,
    evaluation_mode text DEFAULT 'client'::text NOT NULL,
    auto_disable boolean DEFAULT false NOT NULL,
    guardrails jsonb DEFAULT '[]'::jsonb NOT NULL,
    disabled_reason text DEFAULT ''::text NOT NULL,
    disabled_by text DEFAULT ''::text NOT NULL,
    disabled_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT flags_default_variant_non_empty_check CHECK ((default_variant <> ''::text)),
    CONSTRAINT flags_evaluation_mode_check CHECK ((evaluation_mode = ANY (ARRAY['client'::text, 'server'::text, 'both'::text]))),
    CONSTRAINT flags_fallthrough_rollout_only_check CHECK (
CASE
    WHEN (jsonb_typeof(fallthrough) <> 'object'::text) THEN false
    ELSE ((fallthrough ? 'rollout'::text) AND ((fallthrough - 'rollout'::text) = '{}'::jsonb))
END),
    CONSTRAINT flags_rollouts_canonical_check CHECK (public.apdl_flag_rollouts_are_canonical(rules, fallthrough)),
    CONSTRAINT flags_rules_array_check CHECK ((jsonb_typeof(rules) = 'array'::text)),
    CONSTRAINT flags_state_check CHECK ((state = ANY (ARRAY['draft'::text, 'active'::text, 'disabled'::text, 'archived'::text]))),
    CONSTRAINT flags_state_enabled_check CHECK (((state = 'active'::text) = enabled)),
    CONSTRAINT flags_variants_array_non_empty_check CHECK (
CASE
    WHEN (jsonb_typeof(variants) <> 'array'::text) THEN false
    ELSE (jsonb_array_length(variants) > 0)
END),
    CONSTRAINT flags_variants_canonical_check CHECK (public.apdl_flag_variants_are_canonical(variants, default_variant))
);


--
-- Name: github_repository_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.github_repository_grants (
    grant_id text NOT NULL,
    project_id text NOT NULL,
    installation_id bigint NOT NULL,
    repository_id bigint NOT NULL,
    repository_full_name text NOT NULL,
    status text NOT NULL,
    authorization_source text NOT NULL,
    authorization_subject text NOT NULL,
    verified_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT github_repository_grants_authorization_source_check CHECK ((authorization_source = ANY (ARRAY['github_oauth'::text, 'operator'::text]))),
    CONSTRAINT github_repository_grants_authorization_subject_check CHECK (((length(authorization_subject) >= 1) AND (length(authorization_subject) <= 512) AND (btrim(authorization_subject) = authorization_subject) AND (authorization_subject <> ''::text) AND (POSITION(('
'::text) IN (authorization_subject)) = 0) AND (POSITION((''::text) IN (authorization_subject)) = 0))),
    CONSTRAINT github_repository_grants_grant_id_check CHECK (((length(grant_id) >= 5) AND (length(grant_id) <= 132) AND (grant_id ~ '^ghg_[A-Za-z0-9_-]+$'::text))),
    CONSTRAINT github_repository_grants_installation_id_check CHECK ((installation_id > 0)),
    CONSTRAINT github_repository_grants_lifecycle_check CHECK ((((status = 'pending_reauthorization'::text) AND (verified_at IS NULL) AND (revoked_at IS NULL)) OR ((status = 'active'::text) AND (verified_at IS NOT NULL) AND (revoked_at IS NULL)) OR ((status = 'revoked'::text) AND (revoked_at IS NOT NULL)))),
    CONSTRAINT github_repository_grants_project_id_check CHECK ((project_id ~ '^[A-Za-z0-9]{1,64}$'::text)),
    CONSTRAINT github_repository_grants_repository_id_check CHECK ((repository_id > 0)),
    CONSTRAINT github_repository_grants_repository_name_check CHECK (((length(repository_full_name) >= 3) AND (length(repository_full_name) <= 201) AND (repository_full_name ~ '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'::text))),
    CONSTRAINT github_repository_grants_status_check CHECK ((status = ANY (ARRAY['pending_reauthorization'::text, 'active'::text, 'revoked'::text])))
);


--
-- Name: llm_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_calls (
    call_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    run_id text NOT NULL,
    execution_kind text NOT NULL,
    execution_owner_id text NOT NULL,
    purpose text NOT NULL,
    data_classification text NOT NULL,
    prompt_sha256 character(64) NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    attempt_count smallint DEFAULT 0 NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cost_usd_micros bigint DEFAULT 0 NOT NULL,
    error_classification text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT llm_calls_completion_check CHECK ((((status = ANY (ARRAY['prepared'::text, 'in_flight'::text])) AND (completed_at IS NULL) AND (error_classification IS NULL)) OR ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'cancelled'::text, 'blocked'::text])) AND (completed_at IS NOT NULL) AND (((status = 'succeeded'::text) AND (error_classification IS NULL)) OR ((status <> 'succeeded'::text) AND (error_classification IS NOT NULL)))))),
    CONSTRAINT llm_calls_counts_check CHECK (((attempt_count >= 0) AND (attempt_count <= 16) AND (input_tokens >= 0) AND (output_tokens >= 0) AND (cost_usd_micros >= 0))),
    CONSTRAINT llm_calls_data_classification_check CHECK ((data_classification = ANY (ARRAY['public'::text, 'internal'::text, 'confidential'::text, 'restricted'::text]))),
    CONSTRAINT llm_calls_error_classification_check CHECK (((error_classification IS NULL) OR (error_classification = ANY (ARRAY['timeout'::text, 'network'::text, 'rate_limited'::text, 'provider_unavailable'::text, 'authentication'::text, 'permission'::text, 'invalid_request'::text, 'model_not_found'::text, 'safety_block'::text, 'policy_denied'::text, 'budget_exceeded'::text, 'run_inactive'::text, 'cost_overrun'::text, 'no_provider'::text, 'credential_unavailable'::text, 'cancelled'::text, 'governance_unavailable'::text, 'unknown'::text])))),
    CONSTRAINT llm_calls_error_message_check CHECK (((error_message IS NULL) OR (char_length(error_message) <= 4000))),
    CONSTRAINT llm_calls_execution_kind_check CHECK ((execution_kind = ANY (ARRAY['agent_run'::text, 'custom_agent_test'::text]))),
    CONSTRAINT llm_calls_execution_owner_check CHECK (((char_length(execution_owner_id) >= 1) AND (char_length(execution_owner_id) <= 512) AND (btrim(execution_owner_id) <> ''::text) AND (execution_owner_id !~ '[[:space:]]'::text))),
    CONSTRAINT llm_calls_prompt_sha256_check CHECK ((prompt_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT llm_calls_purpose_check CHECK (((char_length(purpose) >= 1) AND (char_length(purpose) <= 128) AND (purpose ~ '^[a-z][a-z0-9_.:-]*$'::text))),
    CONSTRAINT llm_calls_run_id_check CHECK (((char_length(run_id) >= 1) AND (char_length(run_id) <= 128) AND (btrim(run_id) <> ''::text))),
    CONSTRAINT llm_calls_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'in_flight'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'blocked'::text])))
);


--
-- Name: llm_project_model_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_model_assignments (
    project_id text NOT NULL,
    tier text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    model_catalog_version text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_project_model_assignments_model_catalog_version_check CHECK ((model_catalog_version ~ '^llm-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT llm_project_model_assignments_model_check CHECK (((length(model) >= 1) AND (length(model) <= 128) AND (model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'::text))),
    CONSTRAINT llm_project_model_assignments_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'xai'::text, 'local'::text]))),
    CONSTRAINT llm_project_model_assignments_tier_check CHECK ((tier = ANY (ARRAY['fast'::text, 'reasoning'::text])))
);


--
-- Name: TABLE llm_project_model_assignments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_project_model_assignments IS 'Canonical fast/reasoning assignments bound to exact current project inventories.';


--
-- Name: llm_project_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_policies (
    project_id text NOT NULL,
    required_data_residency text DEFAULT 'local'::text NOT NULL,
    allow_cross_vendor_retry boolean DEFAULT false NOT NULL,
    project_daily_cost_limit_usd_micros bigint DEFAULT 20000000 NOT NULL,
    run_cost_limit_usd_micros bigint DEFAULT 2000000 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    state text DEFAULT 'inactive'::text NOT NULL,
    version bigint DEFAULT 0 NOT NULL,
    activated_by_actor_user_id uuid,
    activated_at timestamp with time zone,
    deactivated_by_actor_user_id uuid,
    deactivation_reason text,
    deactivated_at timestamp with time zone,
    CONSTRAINT llm_project_policies_activation_shape_check CHECK ((((state = 'active'::text) AND (version > 0) AND (activated_by_actor_user_id IS NOT NULL) AND (activated_at IS NOT NULL) AND (deactivated_by_actor_user_id IS NULL) AND (deactivation_reason IS NULL) AND (deactivated_at IS NULL)) OR ((state = 'inactive'::text) AND (((version = 0) AND (activated_by_actor_user_id IS NULL) AND (activated_at IS NULL) AND (deactivated_by_actor_user_id IS NULL) AND (deactivation_reason IS NULL) AND (deactivated_at IS NULL)) OR ((version > 0) AND (activated_by_actor_user_id IS NOT NULL) AND (activated_at IS NOT NULL) AND (deactivated_by_actor_user_id IS NOT NULL) AND (deactivation_reason IS NOT NULL) AND (deactivated_at IS NOT NULL)))))),
    CONSTRAINT llm_project_policies_bounded_budget_check CHECK (((project_daily_cost_limit_usd_micros <= '1000000000000000'::bigint) AND (run_cost_limit_usd_micros <= '1000000000000000'::bigint) AND (run_cost_limit_usd_micros <= project_daily_cost_limit_usd_micros))),
    CONSTRAINT llm_project_policies_deactivation_reason_check CHECK (((deactivation_reason IS NULL) OR ((deactivation_reason = btrim(deactivation_reason)) AND ((length(deactivation_reason) >= 1) AND (length(deactivation_reason) <= 2000)) AND (POSITION((chr(10)) IN (deactivation_reason)) = 0) AND (POSITION((chr(13)) IN (deactivation_reason)) = 0)))),
    CONSTRAINT llm_project_policies_state_check CHECK ((state = ANY (ARRAY['inactive'::text, 'active'::text]))),
    CONSTRAINT llm_project_policies_version_check CHECK ((version >= 0)),
    CONSTRAINT llm_project_policy_cost_limits_check CHECK (((project_daily_cost_limit_usd_micros >= 0) AND (run_cost_limit_usd_micros >= 0))),
    CONSTRAINT llm_project_policy_residency_check CHECK ((required_data_residency = ANY (ARRAY['local'::text, 'ca'::text, 'us'::text, 'eu'::text, 'global'::text])))
);


--
-- Name: llm_project_policy_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_policy_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL,
    previous_policy jsonb NOT NULL,
    next_policy jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_project_policy_audit_actor_check CHECK (((char_length(actor) >= 1) AND (char_length(actor) <= 512) AND (actor = btrim(actor)) AND (POSITION((chr(10)) IN (actor)) = 0) AND (POSITION((chr(13)) IN (actor)) = 0))),
    CONSTRAINT llm_project_policy_audit_next_check CHECK (((jsonb_typeof(next_policy) = 'object'::text) AND ((next_policy ->> 'schema'::text) = 'llm_project_policy_snapshot@1'::text) AND (jsonb_typeof((next_policy -> 'project_policy'::text)) = 'object'::text) AND (jsonb_typeof((next_policy -> 'provider_policies'::text)) = 'array'::text))),
    CONSTRAINT llm_project_policy_audit_previous_check CHECK (((jsonb_typeof(previous_policy) = 'object'::text) AND ((previous_policy ->> 'schema'::text) = 'llm_project_policy_snapshot@1'::text) AND (jsonb_typeof((previous_policy -> 'project_policy'::text)) = 'object'::text) AND (jsonb_typeof((previous_policy -> 'provider_policies'::text)) = 'array'::text))),
    CONSTRAINT llm_project_policy_audit_reason_check CHECK (((char_length(reason) >= 1) AND (char_length(reason) <= 2000) AND (reason = btrim(reason)) AND (POSITION((chr(10)) IN (reason)) = 0) AND (POSITION((chr(13)) IN (reason)) = 0)))
);


--
-- Name: llm_project_provider_connection_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_provider_connection_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    connection_version bigint NOT NULL,
    credential_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    model_count integer NOT NULL,
    catalog_version text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_project_provider_connection_audit_action_check CHECK ((action = ANY (ARRAY['connect'::text, 'replace'::text, 'refresh'::text, 'revoke'::text]))),
    CONSTRAINT llm_project_provider_connection_audit_catalog_version_check CHECK ((catalog_version ~ '^llm-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT llm_project_provider_connection_audit_connection_version_check CHECK ((connection_version > 0)),
    CONSTRAINT llm_project_provider_connection_audit_model_count_check CHECK (((model_count >= 0) AND (model_count <= 1000))),
    CONSTRAINT llm_project_provider_connection_audit_outcome_check CHECK ((outcome = 'succeeded'::text)),
    CONSTRAINT llm_project_provider_connection_audit_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT llm_project_provider_connection_audit_shape_check CHECK ((((action = 'revoke'::text) AND (model_count = 0)) OR ((action <> 'revoke'::text) AND (model_count > 0))))
);


--
-- Name: llm_project_provider_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_provider_connections (
    project_id text NOT NULL,
    provider text NOT NULL,
    version bigint NOT NULL,
    state text NOT NULL,
    credential_id uuid NOT NULL,
    catalog_version text NOT NULL,
    validated_at timestamp with time zone NOT NULL,
    validated_by_actor text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    inventory_version bigint NOT NULL,
    CONSTRAINT llm_project_provider_connections_catalog_version_check CHECK ((catalog_version ~ '^llm-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT llm_project_provider_connections_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT llm_project_provider_connections_lifecycle_check CHECK ((((state = 'active'::text) AND (revoked_at IS NULL)) OR ((state = 'revoked'::text) AND (revoked_at IS NOT NULL)))),
    CONSTRAINT llm_project_provider_connections_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT llm_project_provider_connections_state_check CHECK ((state = ANY (ARRAY['active'::text, 'revoked'::text]))),
    CONSTRAINT llm_project_provider_connections_validated_by_actor_check CHECK (((validated_by_actor = btrim(validated_by_actor)) AND ((length(validated_by_actor) >= 1) AND (length(validated_by_actor) <= 512)) AND (POSITION((chr(10)) IN (validated_by_actor)) = 0) AND (POSITION((chr(13)) IN (validated_by_actor)) = 0))),
    CONSTRAINT llm_project_provider_connections_version_check CHECK ((version > 0))
);


--
-- Name: llm_project_provider_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_provider_models (
    project_id text NOT NULL,
    provider text NOT NULL,
    connection_version bigint NOT NULL,
    schema_version text NOT NULL,
    model_id text NOT NULL,
    display_name text NOT NULL,
    supported_tiers text[] NOT NULL,
    catalog_version text NOT NULL,
    data_residency text NOT NULL,
    allowed_data_classifications text[] NOT NULL,
    pricing_status text NOT NULL,
    discovered_at timestamp with time zone NOT NULL,
    inventory_version bigint NOT NULL,
    CONSTRAINT llm_project_provider_models_allowed_data_classifications_check CHECK (((allowed_data_classifications = ARRAY['public'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text, 'confidential'::text]) OR (allowed_data_classifications = ARRAY['public'::text, 'internal'::text, 'confidential'::text, 'restricted'::text]))),
    CONSTRAINT llm_project_provider_models_catalog_version_check CHECK ((catalog_version ~ '^llm-provider-catalog@[1-9][0-9]*$'::text)),
    CONSTRAINT llm_project_provider_models_data_residency_check CHECK ((data_residency = ANY (ARRAY['ca'::text, 'us'::text, 'eu'::text, 'global'::text]))),
    CONSTRAINT llm_project_provider_models_display_name_check CHECK (((display_name = btrim(display_name)) AND ((length(display_name) >= 1) AND (length(display_name) <= 200)))),
    CONSTRAINT llm_project_provider_models_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT llm_project_provider_models_model_id_check CHECK (((length(model_id) >= 1) AND (length(model_id) <= 128) AND (model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'::text))),
    CONSTRAINT llm_project_provider_models_pricing_status_check CHECK ((pricing_status = 'catalog_reviewed'::text)),
    CONSTRAINT llm_project_provider_models_schema_version_check CHECK ((schema_version = 'llm_provider_model@1'::text)),
    CONSTRAINT llm_project_provider_models_supported_tiers_check CHECK (((supported_tiers = ARRAY['fast'::text]) OR (supported_tiers = ARRAY['reasoning'::text]) OR (supported_tiers = ARRAY['fast'::text, 'reasoning'::text])))
);


--
-- Name: llm_project_provider_policies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_provider_policies (
    project_id text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    endpoint_url text NOT NULL,
    data_residency text NOT NULL,
    allowed_data_classifications text[] NOT NULL,
    input_cost_per_million_tokens_usd_micros bigint NOT NULL,
    output_cost_per_million_tokens_usd_micros bigint NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_project_provider_classifications_check CHECK (((cardinality(allowed_data_classifications) >= 1) AND (cardinality(allowed_data_classifications) <= 4) AND (allowed_data_classifications <@ ARRAY['public'::text, 'internal'::text, 'confidential'::text, 'restricted'::text]))),
    CONSTRAINT llm_project_provider_cost_check CHECK (((input_cost_per_million_tokens_usd_micros >= 0) AND (output_cost_per_million_tokens_usd_micros >= 0) AND ((provider = 'local'::text) OR (input_cost_per_million_tokens_usd_micros > 0) OR (output_cost_per_million_tokens_usd_micros > 0)))),
    CONSTRAINT llm_project_provider_endpoint_check CHECK (((char_length(endpoint_url) >= 8) AND (char_length(endpoint_url) <= 512) AND (endpoint_url ~ '^https?://[^[:space:]]+$'::text) AND ("right"(endpoint_url, 1) <> '/'::text))),
    CONSTRAINT llm_project_provider_local_residency_check CHECK ((((provider = 'local'::text) AND (data_residency = 'local'::text)) OR ((provider <> 'local'::text) AND (data_residency <> 'local'::text)))),
    CONSTRAINT llm_project_provider_model_check CHECK (((char_length(model) >= 1) AND (char_length(model) <= 128) AND (btrim(model) <> ''::text))),
    CONSTRAINT llm_project_provider_name_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'xai'::text, 'local'::text]))),
    CONSTRAINT llm_project_provider_residency_check CHECK ((data_residency = ANY (ARRAY['local'::text, 'ca'::text, 'us'::text, 'eu'::text, 'global'::text])))
);


--
-- Name: llm_project_setup_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_project_setup_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    actor_user_id uuid NOT NULL,
    setup_version bigint NOT NULL,
    previous_setup jsonb NOT NULL,
    next_setup jsonb NOT NULL,
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_project_setup_audit_action_check CHECK ((action = ANY (ARRAY['activate'::text, 'reconfigure'::text, 'deactivate'::text]))),
    CONSTRAINT llm_project_setup_audit_outcome_check CHECK ((outcome = 'succeeded'::text)),
    CONSTRAINT llm_project_setup_audit_reason_check CHECK ((((action = 'deactivate'::text) AND (reason IS NOT NULL) AND (reason = btrim(reason)) AND ((length(reason) >= 1) AND (length(reason) <= 2000)) AND (POSITION((chr(10)) IN (reason)) = 0) AND (POSITION((chr(13)) IN (reason)) = 0)) OR ((action <> 'deactivate'::text) AND (reason IS NULL)))),
    CONSTRAINT llm_project_setup_audit_setup_version_check CHECK ((setup_version > 0)),
    CONSTRAINT llm_project_setup_audit_snapshot_check CHECK (((jsonb_typeof(previous_setup) = 'object'::text) AND (jsonb_typeof(next_setup) = 'object'::text)))
);


--
-- Name: TABLE llm_project_setup_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_project_setup_audit IS 'Immutable, non-secret owner/delegate Agents activation history.';


--
-- Name: llm_provider_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_provider_attempts (
    attempt_id uuid DEFAULT gen_random_uuid() NOT NULL,
    call_id uuid NOT NULL,
    project_id text NOT NULL,
    run_id text NOT NULL,
    attempt_number smallint NOT NULL,
    execution_owner_id text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    endpoint_url text NOT NULL,
    status text DEFAULT 'prepared'::text NOT NULL,
    prompt_sha256 character(64) NOT NULL,
    estimated_input_tokens integer NOT NULL,
    max_output_tokens integer NOT NULL,
    input_tokens integer,
    output_tokens integer,
    reserved_cost_usd_micros bigint NOT NULL,
    charged_cost_usd_micros bigint,
    latency_ms integer,
    retryable boolean DEFAULT false NOT NULL,
    error_classification text,
    error_message text,
    prepared_at timestamp with time zone DEFAULT now() NOT NULL,
    egress_started_at timestamp with time zone,
    completed_at timestamp with time zone,
    credential_id uuid,
    credential_version bigint,
    setup_version bigint NOT NULL,
    model_tier text NOT NULL,
    connection_version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    model_catalog_version text NOT NULL,
    CONSTRAINT llm_provider_attempts_credential_binding_check CHECK ((((provider = 'local'::text) AND (credential_id IS NULL) AND (credential_version IS NULL)) OR ((provider <> 'local'::text) AND (credential_id IS NOT NULL) AND (credential_version IS NOT NULL) AND (credential_version > 0)))),
    CONSTRAINT llm_provider_attempts_endpoint_check CHECK (((char_length(endpoint_url) >= 8) AND (char_length(endpoint_url) <= 512) AND (endpoint_url ~ '^https?://[^[:space:]]+$'::text) AND ("right"(endpoint_url, 1) <> '/'::text))),
    CONSTRAINT llm_provider_attempts_error_classification_check CHECK (((error_classification IS NULL) OR (error_classification = ANY (ARRAY['timeout'::text, 'network'::text, 'rate_limited'::text, 'provider_unavailable'::text, 'authentication'::text, 'permission'::text, 'invalid_request'::text, 'model_not_found'::text, 'safety_block'::text, 'policy_denied'::text, 'budget_exceeded'::text, 'run_inactive'::text, 'cost_overrun'::text, 'no_provider'::text, 'credential_unavailable'::text, 'cancelled'::text, 'governance_unavailable'::text, 'unknown'::text])))),
    CONSTRAINT llm_provider_attempts_error_message_check CHECK (((error_message IS NULL) OR (char_length(error_message) <= 4000))),
    CONSTRAINT llm_provider_attempts_execution_owner_check CHECK (((char_length(execution_owner_id) >= 1) AND (char_length(execution_owner_id) <= 512) AND (btrim(execution_owner_id) <> ''::text) AND (execution_owner_id !~ '[[:space:]]'::text))),
    CONSTRAINT llm_provider_attempts_lifecycle_check CHECK ((((status = 'prepared'::text) AND (egress_started_at IS NULL) AND (completed_at IS NULL) AND (charged_cost_usd_micros IS NULL) AND (error_classification IS NULL)) OR ((status = 'in_flight'::text) AND (egress_started_at IS NOT NULL) AND (completed_at IS NULL) AND (charged_cost_usd_micros IS NULL) AND (error_classification IS NULL)) OR ((status = ANY (ARRAY['succeeded'::text, 'failed'::text, 'cancelled'::text])) AND (egress_started_at IS NOT NULL) AND (completed_at IS NOT NULL) AND (charged_cost_usd_micros IS NOT NULL) AND (((status = 'succeeded'::text) AND (error_classification IS NULL)) OR ((status <> 'succeeded'::text) AND (error_classification IS NOT NULL)))) OR ((status = 'blocked'::text) AND (egress_started_at IS NULL) AND (completed_at IS NOT NULL) AND (charged_cost_usd_micros = 0) AND (error_classification IS NOT NULL)))),
    CONSTRAINT llm_provider_attempts_model_check CHECK (((char_length(model) >= 1) AND (char_length(model) <= 128) AND (btrim(model) <> ''::text))),
    CONSTRAINT llm_provider_attempts_number_check CHECK (((attempt_number >= 1) AND (attempt_number <= 16))),
    CONSTRAINT llm_provider_attempts_prompt_sha256_check CHECK ((prompt_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT llm_provider_attempts_provider_check CHECK ((provider = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'xai'::text, 'local'::text]))),
    CONSTRAINT llm_provider_attempts_run_id_check CHECK (((char_length(run_id) >= 1) AND (char_length(run_id) <= 128) AND (btrim(run_id) <> ''::text))),
    CONSTRAINT llm_provider_attempts_setup_binding_check CHECK (((setup_version > 0) AND (model_tier = ANY (ARRAY['fast'::text, 'reasoning'::text])) AND (connection_version > 0) AND (inventory_version > 0) AND (model_catalog_version ~ '^llm-provider-catalog@[1-9][0-9]*$'::text))),
    CONSTRAINT llm_provider_attempts_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'in_flight'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'blocked'::text]))),
    CONSTRAINT llm_provider_attempts_usage_check CHECK (((estimated_input_tokens >= 0) AND (max_output_tokens > 0) AND ((input_tokens IS NULL) OR (input_tokens >= 0)) AND ((output_tokens IS NULL) OR (output_tokens >= 0)) AND (reserved_cost_usd_micros >= 0) AND ((charged_cost_usd_micros IS NULL) OR (charged_cost_usd_micros >= 0)) AND ((latency_ms IS NULL) OR (latency_ms >= 0))))
);


--
-- Name: llm_vault_access_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_access_audit (
    access_id uuid DEFAULT gen_random_uuid() NOT NULL,
    connection_id uuid NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    credential_id uuid NOT NULL,
    credential_version bigint NOT NULL,
    consumer text NOT NULL,
    execution_id text NOT NULL,
    purpose text NOT NULL,
    outcome text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_vault_access_audit_consumer_check CHECK ((consumer = ANY (ARRAY['agents'::text, 'codegen'::text]))),
    CONSTRAINT llm_vault_access_audit_credential_version_check CHECK ((credential_version > 0)),
    CONSTRAINT llm_vault_access_audit_execution_id_check CHECK (((execution_id = btrim(execution_id)) AND ((length(execution_id) >= 1) AND (length(execution_id) <= 256)) AND (POSITION((chr(10)) IN (execution_id)) = 0) AND (POSITION((chr(13)) IN (execution_id)) = 0))),
    CONSTRAINT llm_vault_access_audit_outcome_check CHECK ((outcome = 'issued'::text)),
    CONSTRAINT llm_vault_access_audit_purpose_check CHECK ((purpose ~ '^[a-z][a-z0-9_.:-]{0,127}$'::text))
);


--
-- Name: TABLE llm_vault_access_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_vault_access_audit IS 'Immutable just-in-time plaintext credential issuance evidence.';


--
-- Name: llm_vault_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    connection_id uuid NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    credential_id uuid NOT NULL,
    credential_version bigint NOT NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    consumers text[] NOT NULL,
    actor_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_vault_audit_action_check CHECK ((action = ANY (ARRAY['create'::text, 'replace'::text, 'refresh'::text, 'revoke'::text]))),
    CONSTRAINT llm_vault_audit_consumers_check CHECK ((((consumers = ARRAY['agents'::text]) OR (consumers = ARRAY['codegen'::text])) OR (consumers = ARRAY['agents'::text, 'codegen'::text]))),
    CONSTRAINT llm_vault_audit_credential_version_check CHECK ((credential_version > 0)),
    CONSTRAINT llm_vault_audit_outcome_check CHECK ((outcome = 'succeeded'::text))
);


--
-- Name: llm_vault_connection_consumers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_connection_consumers (
    connection_id uuid NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    consumer text NOT NULL,
    granted_by_actor_user_id uuid NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_vault_connection_consumers_consumer_check CHECK ((consumer = ANY (ARRAY['agents'::text, 'codegen'::text])))
);


--
-- Name: llm_vault_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_connections (
    connection_id uuid DEFAULT gen_random_uuid() NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    label text NOT NULL,
    version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    state text NOT NULL,
    validated_at timestamp with time zone NOT NULL,
    created_by_actor_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_by_actor_user_id uuid,
    revocation_reason text,
    revoked_at timestamp with time zone,
    CONSTRAINT llm_vault_connections_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT llm_vault_connections_label_check CHECK (((label = btrim(label)) AND ((length(label) >= 1) AND (length(label) <= 80)) AND (POSITION((chr(10)) IN (label)) = 0) AND (POSITION((chr(13)) IN (label)) = 0))),
    CONSTRAINT llm_vault_connections_lifecycle_check CHECK ((((state = 'active'::text) AND (revoked_by_actor_user_id IS NULL) AND (revocation_reason IS NULL) AND (revoked_at IS NULL)) OR ((state = 'revoked'::text) AND (revoked_by_actor_user_id IS NOT NULL) AND (revocation_reason IS NOT NULL) AND (revocation_reason = btrim(revocation_reason)) AND ((length(revocation_reason) >= 1) AND (length(revocation_reason) <= 2000)) AND (POSITION((chr(10)) IN (revocation_reason)) = 0) AND (POSITION((chr(13)) IN (revocation_reason)) = 0) AND (revoked_at IS NOT NULL)))),
    CONSTRAINT llm_vault_connections_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'google'::text, 'xai'::text]))),
    CONSTRAINT llm_vault_connections_state_check CHECK ((state = ANY (ARRAY['active'::text, 'revoked'::text]))),
    CONSTRAINT llm_vault_connections_version_check CHECK ((version > 0))
);


--
-- Name: TABLE llm_vault_connections; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_vault_connections IS 'Canonical project provider connections with explicit Agents/Codegen grants.';


--
-- Name: llm_vault_key_rotation_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_key_rotation_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    connection_id uuid NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    credential_id uuid NOT NULL,
    credential_version bigint NOT NULL,
    action text NOT NULL,
    outcome text NOT NULL,
    operator text NOT NULL,
    previous_encryption_key_id text NOT NULL,
    encryption_key_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_vault_key_rotation_audit_action_check CHECK ((action = 'reencrypt'::text)),
    CONSTRAINT llm_vault_key_rotation_audit_credential_version_check CHECK ((credential_version > 0)),
    CONSTRAINT llm_vault_key_rotation_audit_encryption_key_id_check CHECK ((encryption_key_id ~ '^sha256:[0-9a-f]{32}$'::text)),
    CONSTRAINT llm_vault_key_rotation_audit_key_change_check CHECK ((previous_encryption_key_id <> encryption_key_id)),
    CONSTRAINT llm_vault_key_rotation_audit_operator_check CHECK (((operator = btrim(operator)) AND ((length(operator) >= 1) AND (length(operator) <= 512)) AND (POSITION((chr(10)) IN (operator)) = 0) AND (POSITION((chr(13)) IN (operator)) = 0))),
    CONSTRAINT llm_vault_key_rotation_audit_outcome_check CHECK ((outcome = 'succeeded'::text)),
    CONSTRAINT llm_vault_key_rotation_audit_previous_encryption_key_id_check CHECK ((previous_encryption_key_id ~ '^sha256:[0-9a-f]{32}$'::text))
);


--
-- Name: TABLE llm_vault_key_rotation_audit; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_vault_key_rotation_audit IS 'Immutable evidence for offline, vault-wide encryption-key rotation.';


--
-- Name: llm_vault_provider_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_provider_credentials (
    credential_id uuid NOT NULL,
    connection_id uuid NOT NULL,
    project_id text NOT NULL,
    provider text NOT NULL,
    credential_version bigint NOT NULL,
    state text NOT NULL,
    successor_credential_id uuid,
    created_by_actor_user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_by_actor_user_id uuid,
    retirement_reason text,
    retired_at timestamp with time zone,
    CONSTRAINT llm_vault_provider_credentials_credential_version_check CHECK ((credential_version > 0)),
    CONSTRAINT llm_vault_provider_credentials_lifecycle_check CHECK ((((state = 'active'::text) AND (successor_credential_id IS NULL) AND (retired_by_actor_user_id IS NULL) AND (retirement_reason IS NULL) AND (retired_at IS NULL)) OR ((state = 'replaced'::text) AND (successor_credential_id IS NOT NULL) AND (successor_credential_id <> credential_id) AND (retired_by_actor_user_id IS NOT NULL) AND (retirement_reason IS NOT NULL) AND (retired_at IS NOT NULL)) OR ((state = 'revoked'::text) AND (successor_credential_id IS NULL) AND (retired_by_actor_user_id IS NOT NULL) AND (retirement_reason IS NOT NULL) AND (retired_at IS NOT NULL)))),
    CONSTRAINT llm_vault_provider_credentials_reason_check CHECK (((retirement_reason IS NULL) OR ((retirement_reason = btrim(retirement_reason)) AND ((length(retirement_reason) >= 1) AND (length(retirement_reason) <= 2000)) AND (POSITION((chr(10)) IN (retirement_reason)) = 0) AND (POSITION((chr(13)) IN (retirement_reason)) = 0)))),
    CONSTRAINT llm_vault_provider_credentials_state_check CHECK ((state = ANY (ARRAY['active'::text, 'replaced'::text, 'revoked'::text])))
);


--
-- Name: llm_vault_provider_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_provider_models (
    connection_id uuid NOT NULL,
    connection_version bigint NOT NULL,
    inventory_version bigint NOT NULL,
    model_id text NOT NULL,
    discovered_at timestamp with time zone NOT NULL,
    CONSTRAINT llm_vault_provider_models_inventory_version_check CHECK ((inventory_version > 0)),
    CONSTRAINT llm_vault_provider_models_model_id_check CHECK ((((length(model_id) >= 1) AND (length(model_id) <= 128)) AND (model_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$'::text)))
);


--
-- Name: llm_vault_provider_secrets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_vault_provider_secrets (
    credential_id uuid NOT NULL,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    algorithm text NOT NULL,
    schema_version text NOT NULL,
    encryption_key_id text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT llm_vault_provider_secrets_algorithm_check CHECK ((algorithm = 'AES-256-GCM'::text)),
    CONSTRAINT llm_vault_provider_secrets_ciphertext_check CHECK ((octet_length(ciphertext) > 16)),
    CONSTRAINT llm_vault_provider_secrets_encryption_key_id_check CHECK ((encryption_key_id ~ '^sha256:[0-9a-f]{32}$'::text)),
    CONSTRAINT llm_vault_provider_secrets_nonce_check CHECK ((octet_length(nonce) = 12)),
    CONSTRAINT llm_vault_provider_secrets_schema_version_check CHECK ((schema_version = 'llm_vault_provider_secret@1'::text))
);


--
-- Name: TABLE llm_vault_provider_secrets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.llm_vault_provider_secrets IS 'Vault-only AES-256-GCM ciphertext; runtime services have no privileges.';


--
-- Name: agent_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_audit_log ALTER COLUMN id SET DEFAULT nextval('public.agent_audit_log_id_seq'::regclass);


--
-- Name: agent_memory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory ALTER COLUMN id SET DEFAULT nextval('public.agent_memory_id_seq'::regclass);


--
-- Name: config_outbox id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_outbox ALTER COLUMN id SET DEFAULT nextval('public.config_outbox_id_seq'::regclass);


--
-- Name: config_outbox_operator_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_outbox_operator_log ALTER COLUMN id SET DEFAULT nextval('public.config_outbox_operator_log_id_seq'::regclass);


--
-- Name: experiment_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_audit_log ALTER COLUMN id SET DEFAULT nextval('public.experiment_audit_log_id_seq'::regclass);


--
-- Name: experiment_audit_purge_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_audit_purge_log ALTER COLUMN id SET DEFAULT nextval('public.experiment_audit_purge_log_id_seq'::regclass);


--
-- Name: experiment_verdicts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_verdicts ALTER COLUMN id SET DEFAULT nextval('public.experiment_verdicts_id_seq'::regclass);


--
-- Name: flag_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flag_audit_log ALTER COLUMN id SET DEFAULT nextval('public.flag_audit_log_id_seq'::regclass);


--
-- Name: admin_credential_audit admin_credential_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_credential_audit
    ADD CONSTRAINT admin_credential_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: admin_login_account_risk admin_login_account_risk_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_account_risk
    ADD CONSTRAINT admin_login_account_risk_pkey PRIMARY KEY (user_id);


--
-- Name: admin_login_rate_buckets admin_login_rate_buckets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_rate_buckets
    ADD CONSTRAINT admin_login_rate_buckets_pkey PRIMARY KEY (scope, key_hash);


--
-- Name: admin_login_source_risk admin_login_source_risk_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_source_risk
    ADD CONSTRAINT admin_login_source_risk_pkey PRIMARY KEY (scope, source_hash, email_hash);


--
-- Name: admin_managed_credentials admin_managed_credentials_identity_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_managed_credentials
    ADD CONSTRAINT admin_managed_credentials_identity_unique UNIQUE (credential_id, project_id);


--
-- Name: admin_managed_credentials admin_managed_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_managed_credentials
    ADD CONSTRAINT admin_managed_credentials_pkey PRIMARY KEY (credential_id);


--
-- Name: admin_project_execution_authorizations admin_project_execution_authorizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_execution_authorizations
    ADD CONSTRAINT admin_project_execution_authorizations_pkey PRIMARY KEY (project_id);


--
-- Name: admin_project_invitations admin_project_invitations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_invitations
    ADD CONSTRAINT admin_project_invitations_pkey PRIMARY KEY (invitation_id);


--
-- Name: admin_project_invitations admin_project_invitations_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_invitations
    ADD CONSTRAINT admin_project_invitations_token_hash_key UNIQUE (token_hash);


--
-- Name: admin_project_membership_audit admin_project_membership_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_membership_audit
    ADD CONSTRAINT admin_project_membership_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_ownership_audit
    ADD CONSTRAINT admin_project_ownership_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: admin_projects admin_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_projects
    ADD CONSTRAINT admin_projects_pkey PRIMARY KEY (project_id);


--
-- Name: admin_proxy_audit admin_proxy_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_proxy_audit
    ADD CONSTRAINT admin_proxy_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: admin_security_notifications admin_security_notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_security_notifications
    ADD CONSTRAINT admin_security_notifications_pkey PRIMARY KEY (notification_id);


--
-- Name: admin_sessions admin_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_pkey PRIMARY KEY (session_id);


--
-- Name: admin_sessions admin_sessions_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_token_hash_key UNIQUE (token_hash);


--
-- Name: admin_user_projects admin_user_projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_projects
    ADD CONSTRAINT admin_user_projects_pkey PRIMARY KEY (user_id, project_id);


--
-- Name: admin_users admin_users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_email_key UNIQUE (email);


--
-- Name: admin_users admin_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_pkey PRIMARY KEY (user_id);


--
-- Name: agent_approval_commands agent_approval_commands_effect_parent_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_commands
    ADD CONSTRAINT agent_approval_commands_effect_parent_unique UNIQUE (command_id, run_id, project_id);


--
-- Name: agent_approval_commands agent_approval_commands_gate_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_commands
    ADD CONSTRAINT agent_approval_commands_gate_unique UNIQUE (run_id, gate_id);


--
-- Name: agent_approval_commands agent_approval_commands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_commands
    ADD CONSTRAINT agent_approval_commands_pkey PRIMARY KEY (command_id);


--
-- Name: agent_approval_commands agent_approval_commands_request_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_commands
    ADD CONSTRAINT agent_approval_commands_request_unique UNIQUE (run_id, request_sha256);


--
-- Name: agent_approval_decisions agent_approval_decisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_decisions
    ADD CONSTRAINT agent_approval_decisions_pkey PRIMARY KEY (command_id, item_id);


--
-- Name: agent_approval_effects agent_approval_effects_dependency_parent_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_dependency_parent_unique UNIQUE (command_id, effect_id);


--
-- Name: agent_approval_effects agent_approval_effects_idempotency_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_idempotency_unique UNIQUE (idempotency_key);


--
-- Name: agent_approval_effects agent_approval_effects_identity_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_identity_unique UNIQUE (command_id, item_id, effect_type);


--
-- Name: agent_approval_effects agent_approval_effects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_pkey PRIMARY KEY (effect_id);


--
-- Name: agent_audit_log agent_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_audit_log
    ADD CONSTRAINT agent_audit_log_pkey PRIMARY KEY (id);


--
-- Name: agent_memory agent_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_memory
    ADD CONSTRAINT agent_memory_pkey PRIMARY KEY (id);


--
-- Name: agent_mutation_quota_reservations agent_mutation_quota_reservations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_mutation_quota_reservations
    ADD CONSTRAINT agent_mutation_quota_reservations_pkey PRIMARY KEY (project_id, action_type, idempotency_key);


--
-- Name: agent_run_results agent_run_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_results
    ADD CONSTRAINT agent_run_results_pkey PRIMARY KEY (run_id, agent_name);


--
-- Name: agent_runs agent_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (run_id);


--
-- Name: analytics_data_deletion_audit analytics_data_deletion_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analytics_data_deletion_audit
    ADD CONSTRAINT analytics_data_deletion_audit_pkey PRIMARY KEY (request_id, event_type);


--
-- Name: apdl_analysis_table_registry apdl_analysis_table_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.apdl_analysis_table_registry
    ADD CONSTRAINT apdl_analysis_table_registry_pkey PRIMARY KEY (table_name);


--
-- Name: apdl_execution_table_registry apdl_execution_table_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.apdl_execution_table_registry
    ADD CONSTRAINT apdl_execution_table_registry_pkey PRIMARY KEY (table_name);


--
-- Name: auth_credentials auth_credentials_id_project_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_credentials
    ADD CONSTRAINT auth_credentials_id_project_unique UNIQUE (credential_id, project_id);


--
-- Name: auth_credentials auth_credentials_key_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_credentials
    ADD CONSTRAINT auth_credentials_key_hash_key UNIQUE (key_hash);


--
-- Name: auth_credentials auth_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_credentials
    ADD CONSTRAINT auth_credentials_pkey PRIMARY KEY (credential_id);


--
-- Name: codegen_changesets codegen_changesets_attempt_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_changesets
    ADD CONSTRAINT codegen_changesets_attempt_identity_key UNIQUE (changeset_id, project_id);


--
-- Name: codegen_changesets codegen_changesets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_changesets
    ADD CONSTRAINT codegen_changesets_pkey PRIMARY KEY (changeset_id);


--
-- Name: codegen_ci_remediation_attempts codegen_ci_remediation_attempts_attempt_id_event_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_ci_remediation_attempts
    ADD CONSTRAINT codegen_ci_remediation_attempts_attempt_id_event_sequence_key UNIQUE (attempt_id, event_sequence);


--
-- Name: codegen_ci_remediation_attempts codegen_ci_remediation_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_ci_remediation_attempts
    ADD CONSTRAINT codegen_ci_remediation_attempts_pkey PRIMARY KEY (event_id);


--
-- Name: codegen_ci_remediation_claims codegen_ci_remediation_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_ci_remediation_claims
    ADD CONSTRAINT codegen_ci_remediation_claims_pkey PRIMARY KEY (changeset_id, failed_head_sha, claim_scope);


--
-- Name: codegen_ci_verification_observations codegen_ci_verification_obser_changeset_id_head_sha_evidenc_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_ci_verification_observations
    ADD CONSTRAINT codegen_ci_verification_obser_changeset_id_head_sha_evidenc_key UNIQUE (changeset_id, head_sha, evidence_hash);


--
-- Name: codegen_ci_verification_observations codegen_ci_verification_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_ci_verification_observations
    ADD CONSTRAINT codegen_ci_verification_observations_pkey PRIMARY KEY (observation_id);


--
-- Name: codegen_connections codegen_connections_authorized_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_connections
    ADD CONSTRAINT codegen_connections_authorized_pkey PRIMARY KEY (project_id);


--
-- Name: codegen_connections codegen_connections_authorized_project_grant_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_connections
    ADD CONSTRAINT codegen_connections_authorized_project_grant_key UNIQUE (project_id, grant_id);


--
-- Name: codegen_llm_attempts codegen_llm_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_llm_attempts
    ADD CONSTRAINT codegen_llm_attempts_pkey PRIMARY KEY (attempt_id);


--
-- Name: codegen_llm_attempts codegen_llm_attempts_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_llm_attempts
    ADD CONSTRAINT codegen_llm_attempts_sequence_key UNIQUE (changeset_id, phase, attempt_sequence);


--
-- Name: codegen_project_model_assignments codegen_project_model_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_model_assignments
    ADD CONSTRAINT codegen_project_model_assignments_pkey PRIMARY KEY (project_id, role);


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connection_audit
    ADD CONSTRAINT codegen_project_provider_connection_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_inventory_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_inventory_identity_key UNIQUE (project_id, provider, version, inventory_version, catalog_version);


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_inventory_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_inventory_version_key UNIQUE (project_id, provider, inventory_version);


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_pkey PRIMARY KEY (project_id, provider);


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_version_key UNIQUE (project_id, provider, version);


--
-- Name: codegen_project_provider_models codegen_project_provider_models_inventory_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_models
    ADD CONSTRAINT codegen_project_provider_models_inventory_identity_key UNIQUE (project_id, provider, model_id, connection_version, inventory_version, catalog_version);


--
-- Name: codegen_project_provider_models codegen_project_provider_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_models
    ADD CONSTRAINT codegen_project_provider_models_pkey PRIMARY KEY (project_id, provider, model_id);


--
-- Name: codegen_pull_request_observations codegen_pull_request_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_pull_request_observations
    ADD CONSTRAINT codegen_pull_request_observations_pkey PRIMARY KEY (observation_id);


--
-- Name: codegen_pull_request_publication_events codegen_pull_request_publication_events_event_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_pull_request_publication_events
    ADD CONSTRAINT codegen_pull_request_publication_events_event_sequence_key UNIQUE (event_sequence);


--
-- Name: codegen_pull_request_publication_events codegen_pull_request_publication_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_pull_request_publication_events
    ADD CONSTRAINT codegen_pull_request_publication_events_pkey PRIMARY KEY (event_id);


--
-- Name: codegen_runtime_collection_claims codegen_runtime_collection_claims_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_runtime_collection_claims
    ADD CONSTRAINT codegen_runtime_collection_claims_pkey PRIMARY KEY (changeset_id, head_sha, ci_observation_id);


--
-- Name: codegen_runtime_evidence_observations codegen_runtime_evidence_obse_changeset_id_head_sha_evidenc_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_runtime_evidence_observations
    ADD CONSTRAINT codegen_runtime_evidence_obse_changeset_id_head_sha_evidenc_key UNIQUE (changeset_id, head_sha, evidence_hash);


--
-- Name: codegen_runtime_evidence_observations codegen_runtime_evidence_observations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_runtime_evidence_observations
    ADD CONSTRAINT codegen_runtime_evidence_observations_pkey PRIMARY KEY (observation_id);


--
-- Name: config_exposure_receipts config_exposure_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_exposure_receipts
    ADD CONSTRAINT config_exposure_receipts_pkey PRIMARY KEY (project_id, message_id);


--
-- Name: config_outbox_operator_log config_outbox_operator_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_outbox_operator_log
    ADD CONSTRAINT config_outbox_operator_log_pkey PRIMARY KEY (id);


--
-- Name: config_outbox config_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_outbox
    ADD CONSTRAINT config_outbox_pkey PRIMARY KEY (id);


--
-- Name: config_outbox config_outbox_project_id_kind_dedup_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_outbox
    ADD CONSTRAINT config_outbox_project_id_kind_dedup_key_key UNIQUE (project_id, kind, dedup_key);


--
-- Name: config_project_versions config_project_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config_project_versions
    ADD CONSTRAINT config_project_versions_pkey PRIMARY KEY (project_id);


--
-- Name: custom_agent_test_runs custom_agent_test_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custom_agent_test_runs
    ADD CONSTRAINT custom_agent_test_runs_pkey PRIMARY KEY (test_run_id);


--
-- Name: custom_agents custom_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.custom_agents
    ADD CONSTRAINT custom_agents_pkey PRIMARY KEY (agent_id);


--
-- Name: designed_experiments designed_experiments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.designed_experiments
    ADD CONSTRAINT designed_experiments_pkey PRIMARY KEY (project_id, experiment_id);


--
-- Name: event_pipeline_watermarks event_pipeline_watermarks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_pipeline_watermarks
    ADD CONSTRAINT event_pipeline_watermarks_pkey PRIMARY KEY (project_id);


--
-- Name: event_pipeline_watermarks event_pipeline_watermarks_stream_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_pipeline_watermarks
    ADD CONSTRAINT event_pipeline_watermarks_stream_key_key UNIQUE (stream_key);


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_marker_identity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_boundaries
    ADD CONSTRAINT experiment_analysis_boundaries_marker_identity UNIQUE (project_id, experiment_key, config_version, marker_stream_id);


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_marker_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_boundaries
    ADD CONSTRAINT experiment_analysis_boundaries_marker_token_key UNIQUE (marker_token);


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_observed_stream_identity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_boundaries
    ADD CONSTRAINT experiment_analysis_boundaries_observed_stream_identity UNIQUE (project_id, marker_publish_observed_stream_id);


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_boundaries
    ADD CONSTRAINT experiment_analysis_boundaries_pkey PRIMARY KEY (project_id, experiment_key, config_version);


--
-- Name: experiment_analysis_snapshots experiment_analysis_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_snapshots
    ADD CONSTRAINT experiment_analysis_snapshots_pkey PRIMARY KEY (project_id, experiment_key, config_version);


--
-- Name: experiment_audit_log experiment_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_audit_log
    ADD CONSTRAINT experiment_audit_log_pkey PRIMARY KEY (id);


--
-- Name: experiment_audit_purge_log experiment_audit_purge_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_audit_purge_log
    ADD CONSTRAINT experiment_audit_purge_log_pkey PRIMARY KEY (id);


--
-- Name: experiment_verdicts experiment_verdicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_verdicts
    ADD CONSTRAINT experiment_verdicts_pkey PRIMARY KEY (id);


--
-- Name: experiments experiments_active_statistical_plan_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.experiments
    ADD CONSTRAINT experiments_active_statistical_plan_check CHECK (((status <> ALL (ARRAY['scheduled'::text, 'running'::text])) OR public.apdl_experiment_statistical_plan_is_canonical(statistical_plan))) NOT VALID;


--
-- Name: experiments experiments_flag_key_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_flag_key_unique UNIQUE (project_id, flag_key);


--
-- Name: experiments experiments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_pkey PRIMARY KEY (project_id, key);


--
-- Name: experiments experiments_statistical_plan_canonical_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.experiments
    ADD CONSTRAINT experiments_statistical_plan_canonical_check CHECK (((statistical_plan IS NULL) OR public.apdl_experiment_statistical_plan_is_canonical(statistical_plan))) NOT VALID;


--
-- Name: feature_proposals feature_proposals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feature_proposals
    ADD CONSTRAINT feature_proposals_pkey PRIMARY KEY (project_id, proposal_id);


--
-- Name: flag_audit_log flag_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flag_audit_log
    ADD CONSTRAINT flag_audit_log_pkey PRIMARY KEY (id);


--
-- Name: flags flags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flags
    ADD CONSTRAINT flags_pkey PRIMARY KEY (project_id, key);


--
-- Name: github_repository_grants github_repository_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.github_repository_grants
    ADD CONSTRAINT github_repository_grants_pkey PRIMARY KEY (grant_id);


--
-- Name: github_repository_grants github_repository_grants_project_grant_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.github_repository_grants
    ADD CONSTRAINT github_repository_grants_project_grant_key UNIQUE (project_id, grant_id);


--
-- Name: llm_calls llm_calls_governed_execution_identity; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_governed_execution_identity UNIQUE (project_id, run_id, call_id, execution_owner_id);


--
-- Name: llm_calls llm_calls_governed_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_governed_pkey PRIMARY KEY (call_id);


--
-- Name: llm_project_model_assignments llm_project_model_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_model_assignments
    ADD CONSTRAINT llm_project_model_assignments_pkey PRIMARY KEY (project_id, tier);


--
-- Name: llm_project_policies llm_project_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policies
    ADD CONSTRAINT llm_project_policies_pkey PRIMARY KEY (project_id);


--
-- Name: llm_project_policy_audit llm_project_policy_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policy_audit
    ADD CONSTRAINT llm_project_policy_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connection_audit
    ADD CONSTRAINT llm_project_provider_connection_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: llm_project_provider_connections llm_project_provider_connections_inventory_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connections
    ADD CONSTRAINT llm_project_provider_connections_inventory_identity_key UNIQUE (project_id, provider, inventory_version);


--
-- Name: llm_project_provider_connections llm_project_provider_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connections
    ADD CONSTRAINT llm_project_provider_connections_pkey PRIMARY KEY (project_id, provider);


--
-- Name: llm_project_provider_connections llm_project_provider_connections_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connections
    ADD CONSTRAINT llm_project_provider_connections_version_key UNIQUE (project_id, provider, version);


--
-- Name: llm_project_provider_models llm_project_provider_models_inventory_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_models
    ADD CONSTRAINT llm_project_provider_models_inventory_identity_key UNIQUE (project_id, provider, connection_version, inventory_version, model_id);


--
-- Name: llm_project_provider_models llm_project_provider_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_models
    ADD CONSTRAINT llm_project_provider_models_pkey PRIMARY KEY (project_id, provider, model_id);


--
-- Name: llm_project_provider_policies llm_project_provider_policies_assignment_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_policies
    ADD CONSTRAINT llm_project_provider_policies_assignment_identity_key UNIQUE (project_id, provider, model, endpoint_url);


--
-- Name: llm_project_provider_policies llm_project_provider_policies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_policies
    ADD CONSTRAINT llm_project_provider_policies_pkey PRIMARY KEY (project_id, provider, model);


--
-- Name: llm_project_setup_audit llm_project_setup_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_setup_audit
    ADD CONSTRAINT llm_project_setup_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: llm_provider_attempts llm_provider_attempts_call_order_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_provider_attempts
    ADD CONSTRAINT llm_provider_attempts_call_order_key UNIQUE (call_id, attempt_number);


--
-- Name: llm_provider_attempts llm_provider_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_provider_attempts
    ADD CONSTRAINT llm_provider_attempts_pkey PRIMARY KEY (attempt_id);


--
-- Name: llm_vault_access_audit llm_vault_access_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_access_audit
    ADD CONSTRAINT llm_vault_access_audit_pkey PRIMARY KEY (access_id);


--
-- Name: llm_vault_audit llm_vault_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_audit
    ADD CONSTRAINT llm_vault_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: llm_vault_connection_consumers llm_vault_connection_consumers_one_binding_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connection_consumers
    ADD CONSTRAINT llm_vault_connection_consumers_one_binding_key UNIQUE (project_id, provider, consumer);


--
-- Name: llm_vault_connection_consumers llm_vault_connection_consumers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connection_consumers
    ADD CONSTRAINT llm_vault_connection_consumers_pkey PRIMARY KEY (connection_id, consumer);


--
-- Name: llm_vault_connections llm_vault_connections_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_identity_key UNIQUE (connection_id, project_id, provider);


--
-- Name: llm_vault_connections llm_vault_connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_pkey PRIMARY KEY (connection_id);


--
-- Name: llm_vault_connections llm_vault_connections_project_label_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_project_label_key UNIQUE (project_id, provider, label);


--
-- Name: llm_vault_connections llm_vault_connections_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_version_key UNIQUE (connection_id, version);


--
-- Name: llm_vault_key_rotation_audit llm_vault_key_rotation_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_key_rotation_audit
    ADD CONSTRAINT llm_vault_key_rotation_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_attempt_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_attempt_identity_key UNIQUE (credential_id, project_id, provider, credential_version);


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_identity_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_identity_key UNIQUE (credential_id, project_id, provider);


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_pkey PRIMARY KEY (credential_id);


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_version_key UNIQUE (connection_id, credential_version);


--
-- Name: llm_vault_provider_models llm_vault_provider_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_models
    ADD CONSTRAINT llm_vault_provider_models_pkey PRIMARY KEY (connection_id, inventory_version, model_id);


--
-- Name: llm_vault_provider_secrets llm_vault_provider_secrets_nonce_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_secrets
    ADD CONSTRAINT llm_vault_provider_secrets_nonce_key UNIQUE (encryption_key_id, nonce);


--
-- Name: llm_vault_provider_secrets llm_vault_provider_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_secrets
    ADD CONSTRAINT llm_vault_provider_secrets_pkey PRIMARY KEY (credential_id);


--
-- Name: admin_credential_audit_credential_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_credential_audit_credential_created_idx ON public.admin_credential_audit USING btree (credential_id, created_at DESC);


--
-- Name: admin_credential_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_credential_audit_project_created_idx ON public.admin_credential_audit USING btree (project_id, created_at DESC);


--
-- Name: admin_credential_audit_successor_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_credential_audit_successor_created_idx ON public.admin_credential_audit USING btree (successor_credential_id, created_at DESC) WHERE (successor_credential_id IS NOT NULL);


--
-- Name: admin_login_account_risk_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX admin_login_account_risk_email_idx ON public.admin_login_account_risk USING btree (email_hash);


--
-- Name: admin_login_rate_buckets_updated_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_login_rate_buckets_updated_idx ON public.admin_login_rate_buckets USING btree (updated_at);


--
-- Name: admin_login_source_risk_updated_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_login_source_risk_updated_idx ON public.admin_login_source_risk USING btree (updated_at);


--
-- Name: admin_managed_credentials_one_successor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX admin_managed_credentials_one_successor_idx ON public.admin_managed_credentials USING btree (rotated_from_credential_id) WHERE (rotated_from_credential_id IS NOT NULL);


--
-- Name: admin_managed_credentials_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_managed_credentials_project_created_idx ON public.admin_managed_credentials USING btree (project_id, created_at DESC);


--
-- Name: admin_project_invitations_pending_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX admin_project_invitations_pending_email_idx ON public.admin_project_invitations USING btree (project_id, email) WHERE ((accepted_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: admin_project_invitations_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_project_invitations_project_created_idx ON public.admin_project_invitations USING btree (project_id, created_at DESC, invitation_id DESC);


--
-- Name: admin_project_membership_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_project_membership_audit_project_created_idx ON public.admin_project_membership_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: admin_project_ownership_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_project_ownership_audit_project_created_idx ON public.admin_project_ownership_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: admin_proxy_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_proxy_audit_project_created_idx ON public.admin_proxy_audit USING btree (project_id, created_at DESC);


--
-- Name: admin_proxy_audit_user_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_proxy_audit_user_created_idx ON public.admin_proxy_audit USING btree (user_id, created_at DESC);


--
-- Name: admin_security_notifications_unread_login_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX admin_security_notifications_unread_login_idx ON public.admin_security_notifications USING btree (user_id, kind) WHERE (status = 'unread'::text);


--
-- Name: admin_security_notifications_user_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_security_notifications_user_created_idx ON public.admin_security_notifications USING btree (user_id, created_at DESC);


--
-- Name: admin_sessions_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_sessions_active_idx ON public.admin_sessions USING btree (token_hash, expires_at) WHERE (revoked_at IS NULL);


--
-- Name: admin_sessions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_sessions_user_idx ON public.admin_sessions USING btree (user_id);


--
-- Name: admin_user_projects_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX admin_user_projects_project_idx ON public.admin_user_projects USING btree (project_id);


--
-- Name: agent_approval_commands_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX agent_approval_commands_run_idx ON public.agent_approval_commands USING btree (run_id, created_at DESC);


--
-- Name: agent_approval_effects_command_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX agent_approval_effects_command_idx ON public.agent_approval_effects USING btree (command_id, effect_order, created_at);


--
-- Name: agent_approval_effects_dispatch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX agent_approval_effects_dispatch_idx ON public.agent_approval_effects USING btree (next_attempt_at, created_at) WHERE (status = ANY (ARRAY['queued'::text, 'retryable_failed'::text, 'processing'::text]));


--
-- Name: agent_mutation_quota_reservations_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX agent_mutation_quota_reservations_lookup_idx ON public.agent_mutation_quota_reservations USING btree (project_id, action_type, policy_version, occurred_at DESC);


--
-- Name: agent_runs_identity_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX agent_runs_identity_project_idx ON public.agent_runs USING btree (run_id, project_id);


--
-- Name: agent_runs_one_execution_lane_per_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX agent_runs_one_execution_lane_per_project_idx ON public.agent_runs USING btree (execution_lane_project_id) WHERE (execution_lane_project_id IS NOT NULL);


--
-- Name: auth_credentials_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX auth_credentials_project_idx ON public.auth_credentials USING btree (project_id) WHERE active;


--
-- Name: codegen_changesets_one_retry_child_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX codegen_changesets_one_retry_child_idx ON public.codegen_changesets USING btree (retry_of_changeset_id) WHERE (retry_of_changeset_id IS NOT NULL);


--
-- Name: codegen_changesets_project_idempotency_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX codegen_changesets_project_idempotency_key_idx ON public.codegen_changesets USING btree (project_id, idempotency_key);


--
-- Name: codegen_changesets_project_identity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX codegen_changesets_project_identity_idx ON public.codegen_changesets USING btree (project_id, changeset_id);


--
-- Name: codegen_llm_attempts_changeset_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX codegen_llm_attempts_changeset_idx ON public.codegen_llm_attempts USING btree (project_id, changeset_id, created_at, attempt_id);


--
-- Name: codegen_project_model_assignments_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX codegen_project_model_assignments_provider_idx ON public.codegen_project_model_assignments USING btree (project_id, provider, model_id);


--
-- Name: codegen_project_provider_connection_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX codegen_project_provider_connection_audit_project_created_idx ON public.codegen_project_provider_connection_audit USING btree (project_id, provider, created_at DESC, audit_id DESC);


--
-- Name: codegen_project_provider_connections_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX codegen_project_provider_connections_project_idx ON public.codegen_project_provider_connections USING btree (project_id, updated_at DESC);


--
-- Name: codegen_project_provider_models_inventory_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX codegen_project_provider_models_inventory_idx ON public.codegen_project_provider_models USING btree (project_id, provider, inventory_version, model_id);


--
-- Name: custom_agent_test_runs_expired_lease_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX custom_agent_test_runs_expired_lease_idx ON public.custom_agent_test_runs USING btree (lease_expires_at) WHERE (status = 'running'::text);


--
-- Name: custom_agent_test_runs_one_running_per_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX custom_agent_test_runs_one_running_per_project_idx ON public.custom_agent_test_runs USING btree (project_id) WHERE (status = 'running'::text);


--
-- Name: custom_agent_test_runs_project_started_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX custom_agent_test_runs_project_started_idx ON public.custom_agent_test_runs USING btree (project_id, started_at DESC);


--
-- Name: designed_experiments_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX designed_experiments_project_created_idx ON public.designed_experiments USING btree (project_id, created_at DESC);


--
-- Name: experiment_verdicts_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX experiment_verdicts_project_created_idx ON public.experiment_verdicts USING btree (project_id, created_at DESC);


--
-- Name: experiments_project_creation_idempotency_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX experiments_project_creation_idempotency_key_idx ON public.experiments USING btree (project_id, creation_idempotency_key) WHERE (creation_idempotency_key IS NOT NULL);


--
-- Name: feature_proposals_project_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX feature_proposals_project_status_idx ON public.feature_proposals USING btree (project_id, status, updated_at DESC);


--
-- Name: idx_agent_audit_correlation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_audit_correlation ON public.agent_audit_log USING btree (correlation_id) WHERE (correlation_id IS NOT NULL);


--
-- Name: idx_agent_audit_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_agent_audit_idempotency ON public.agent_audit_log USING btree (run_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: idx_agent_audit_run_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_audit_run_created ON public.agent_audit_log USING btree (run_id, created_at DESC);


--
-- Name: idx_agent_memory_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_embedding ON public.agent_memory USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_agent_memory_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_memory_project ON public.agent_memory USING btree (project_id);


--
-- Name: idx_agent_runs_lease_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_lease_expiry ON public.agent_runs USING btree (lease_expires_at) WHERE ((status = ANY (ARRAY['started'::text, 'running'::text])) OR ((phase = 'resuming'::text) AND (status = ANY (ARRAY['approved'::text, 'rejected'::text]))));


--
-- Name: idx_agent_runs_project_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_project_started ON public.agent_runs USING btree (project_id, started_at DESC);


--
-- Name: idx_agent_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_runs_status ON public.agent_runs USING btree (status, phase);


--
-- Name: idx_analytics_data_deletion_audit_project_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_data_deletion_audit_project_time ON public.analytics_data_deletion_audit USING btree (project_id, recorded_at, request_id, event_type);


--
-- Name: idx_codegen_changesets_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_changesets_project ON public.codegen_changesets USING btree (project_id, created_at DESC);


--
-- Name: idx_codegen_changesets_repository_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_changesets_repository_target ON public.codegen_changesets USING btree (repository_id, created_at DESC) WHERE (NOT repository_target_quarantined);


--
-- Name: idx_codegen_ci_observation_head; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_ci_observation_head ON public.codegen_ci_verification_observations USING btree (changeset_id, head_sha, observed_at DESC);


--
-- Name: idx_codegen_pr_observation_head; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_pr_observation_head ON public.codegen_pull_request_observations USING btree (changeset_id, head_sha, github_updated_at DESC, observed_at DESC);


--
-- Name: idx_codegen_pr_publication_recovery; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_pr_publication_recovery ON public.codegen_pull_request_publication_events USING btree (changeset_id, event_sequence DESC);


--
-- Name: idx_codegen_remediation_attempt_head; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_remediation_attempt_head ON public.codegen_ci_remediation_attempts USING btree (changeset_id, failed_head_sha, recorded_at DESC);


--
-- Name: idx_codegen_runtime_evidence_ci_observation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_runtime_evidence_ci_observation ON public.codegen_runtime_evidence_observations USING btree (changeset_id, head_sha, ci_observation_id, observed_at DESC);


--
-- Name: idx_codegen_runtime_evidence_head; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_codegen_runtime_evidence_head ON public.codegen_runtime_evidence_observations USING btree (changeset_id, head_sha, observed_at DESC);


--
-- Name: idx_config_exposure_receipts_cleanup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_exposure_receipts_cleanup ON public.config_exposure_receipts USING btree (last_seen_at, project_id, message_id);


--
-- Name: idx_config_outbox_cleanup_processed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_cleanup_processed ON public.config_outbox USING btree (processed_at, id) WHERE (processed_at IS NOT NULL);


--
-- Name: idx_config_outbox_cleanup_quarantined; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_cleanup_quarantined ON public.config_outbox USING btree (quarantined_at, id) WHERE (quarantined_at IS NOT NULL);


--
-- Name: idx_config_outbox_metrics_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_metrics_pending ON public.config_outbox USING btree (created_at, id) INCLUDE (attempts) WHERE ((processed_at IS NULL) AND (quarantined_at IS NULL));


--
-- Name: idx_config_outbox_operator_log_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_operator_log_project ON public.config_outbox_operator_log USING btree (project_id, created_at DESC, id DESC);


--
-- Name: idx_config_outbox_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_pending ON public.config_outbox USING btree (available_at, id) WHERE ((processed_at IS NULL) AND (quarantined_at IS NULL));


--
-- Name: idx_config_outbox_quarantine_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_config_outbox_quarantine_project_id ON public.config_outbox USING btree (project_id, id DESC) WHERE (quarantined_at IS NOT NULL);


--
-- Name: idx_custom_agents_project_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_custom_agents_project_slug ON public.custom_agents USING btree (project_id, slug) WHERE (status = 'active'::text);


--
-- Name: idx_experiment_analysis_boundaries_publish_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_experiment_analysis_boundaries_publish_due ON public.experiment_analysis_boundaries USING btree (marker_publish_next_attempt_at, requested_at, project_id, experiment_key, config_version) WHERE ((marker_publish_state = 'pending'::text) AND (marker_stream_id IS NULL));


--
-- Name: idx_experiment_audit_keyset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_experiment_audit_keyset ON public.experiment_audit_log USING btree (project_id, experiment_key, id DESC);


--
-- Name: idx_experiment_audit_project_experiment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_experiment_audit_project_experiment ON public.experiment_audit_log USING btree (project_id, experiment_key, created_at DESC, id DESC);


--
-- Name: idx_experiment_audit_purge_log_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_experiment_audit_purge_log_project ON public.experiment_audit_purge_log USING btree (project_id, created_at DESC, id DESC);


--
-- Name: idx_experiments_project_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_experiments_project_updated ON public.experiments USING btree (project_id, updated_at DESC);


--
-- Name: idx_feature_proposals_claim_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feature_proposals_claim_run ON public.feature_proposals USING btree (claim_run_id) WHERE (status = 'implementing'::text);


--
-- Name: idx_flag_audit_project_flag; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flag_audit_project_flag ON public.flag_audit_log USING btree (project_id, flag_key, created_at DESC);


--
-- Name: idx_flags_project_state_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flags_project_state_review ON public.flags USING btree (project_id, state, review_by, updated_at DESC);


--
-- Name: idx_flags_project_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flags_project_updated ON public.flags USING btree (project_id, archived_at, updated_at DESC);


--
-- Name: idx_github_repository_grants_repository; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_github_repository_grants_repository ON public.github_repository_grants USING btree (repository_id, status);


--
-- Name: llm_calls_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_calls_project_created_idx ON public.llm_calls USING btree (project_id, created_at DESC);


--
-- Name: llm_calls_run_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_calls_run_created_idx ON public.llm_calls USING btree (project_id, run_id, created_at DESC);


--
-- Name: llm_project_model_assignments_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_model_assignments_provider_idx ON public.llm_project_model_assignments USING btree (project_id, provider, model);


--
-- Name: llm_project_policy_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_policy_audit_project_created_idx ON public.llm_project_policy_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: llm_project_provider_connection_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_provider_connection_audit_project_created_idx ON public.llm_project_provider_connection_audit USING btree (project_id, provider, created_at DESC, audit_id DESC);


--
-- Name: llm_project_provider_connections_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_provider_connections_project_idx ON public.llm_project_provider_connections USING btree (project_id, updated_at DESC);


--
-- Name: llm_project_provider_models_tier_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_provider_models_tier_idx ON public.llm_project_provider_models USING btree (project_id, provider, connection_version, model_id);


--
-- Name: llm_project_setup_audit_project_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_project_setup_audit_project_created_idx ON public.llm_project_setup_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: llm_provider_attempts_project_budget_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_provider_attempts_project_budget_idx ON public.llm_provider_attempts USING btree (project_id, prepared_at DESC);


--
-- Name: llm_provider_attempts_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_provider_attempts_provider_idx ON public.llm_provider_attempts USING btree (project_id, provider, model, prepared_at DESC);


--
-- Name: llm_provider_attempts_run_budget_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_provider_attempts_run_budget_idx ON public.llm_provider_attempts USING btree (project_id, run_id, prepared_at DESC);


--
-- Name: llm_vault_access_audit_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_vault_access_audit_project_idx ON public.llm_vault_access_audit USING btree (project_id, consumer, created_at DESC, access_id DESC);


--
-- Name: llm_vault_audit_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_vault_audit_project_idx ON public.llm_vault_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: llm_vault_connections_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_vault_connections_project_idx ON public.llm_vault_connections USING btree (project_id, provider, updated_at DESC);


--
-- Name: llm_vault_key_rotation_audit_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_vault_key_rotation_audit_project_idx ON public.llm_vault_key_rotation_audit USING btree (project_id, created_at DESC, audit_id DESC);


--
-- Name: llm_vault_provider_credentials_one_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX llm_vault_provider_credentials_one_active_idx ON public.llm_vault_provider_credentials USING btree (connection_id) WHERE (state = 'active'::text);


--
-- Name: llm_vault_provider_credentials_project_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX llm_vault_provider_credentials_project_idx ON public.llm_vault_provider_credentials USING btree (project_id, provider, created_at DESC, credential_id DESC);


--
-- Name: uq_codegen_pr_observation_delivery; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_codegen_pr_observation_delivery ON public.codegen_pull_request_observations USING btree (delivery_id) WHERE (delivery_id IS NOT NULL);


--
-- Name: uq_codegen_pr_publication_intent; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_codegen_pr_publication_intent ON public.codegen_pull_request_publication_events USING btree (changeset_id) WHERE (event_type = 'intent_recorded'::text);


--
-- Name: uq_github_repository_grants_active_project; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_github_repository_grants_active_project ON public.github_repository_grants USING btree (project_id) WHERE (status = 'active'::text);


--
-- Name: admin_credential_audit admin_credential_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_credential_audit_no_truncate BEFORE TRUNCATE ON public.admin_credential_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_managed_credential_history_mutation();


--
-- Name: admin_credential_audit admin_credential_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_credential_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.admin_credential_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_managed_credential_history_mutation();


--
-- Name: admin_managed_credentials admin_managed_credentials_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_managed_credentials_no_truncate BEFORE TRUNCATE ON public.admin_managed_credentials FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_managed_credential_history_mutation();


--
-- Name: admin_managed_credentials admin_managed_credentials_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_managed_credentials_no_update_delete BEFORE DELETE OR UPDATE ON public.admin_managed_credentials FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_managed_credential_history_mutation();


--
-- Name: admin_managed_credentials admin_managed_credentials_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_managed_credentials_validate BEFORE INSERT ON public.admin_managed_credentials FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_managed_credential();


--
-- Name: admin_project_execution_authorizations admin_project_execution_authorizations_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_execution_authorizations_immutable BEFORE DELETE OR UPDATE ON public.admin_project_execution_authorizations FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_execution_authorization_mutation();


--
-- Name: admin_project_execution_authorizations admin_project_execution_authorizations_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_execution_authorizations_validate BEFORE INSERT ON public.admin_project_execution_authorizations FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_execution_authorization_provenance();


--
-- Name: admin_project_invitations admin_project_invitations_no_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_invitations_no_delete BEFORE DELETE ON public.admin_project_invitations FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_project_membership_history_mutation();


--
-- Name: admin_project_invitations admin_project_invitations_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_invitations_no_truncate BEFORE TRUNCATE ON public.admin_project_invitations FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_project_membership_history_mutation();


--
-- Name: admin_project_invitations admin_project_invitations_validate_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_invitations_validate_update BEFORE UPDATE ON public.admin_project_invitations FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_project_invitation_update();


--
-- Name: admin_project_membership_audit admin_project_membership_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_membership_audit_no_truncate BEFORE TRUNCATE ON public.admin_project_membership_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_project_membership_history_mutation();


--
-- Name: admin_project_membership_audit admin_project_membership_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_membership_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.admin_project_membership_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_project_membership_history_mutation();


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_ownership_audit_no_truncate BEFORE TRUNCATE ON public.admin_project_ownership_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_project_ownership_audit_mutation();


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_project_ownership_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.admin_project_ownership_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_project_ownership_audit_mutation();


--
-- Name: admin_projects admin_projects_authorize_operator_execution; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_projects_authorize_operator_execution AFTER INSERT ON public.admin_projects FOR EACH ROW WHEN ((new.created_by IS NULL)) EXECUTE FUNCTION public.apdl_authorize_operator_project();


--
-- Name: admin_projects admin_projects_creator_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_projects_creator_immutable BEFORE UPDATE OF created_by ON public.admin_projects FOR EACH ROW EXECUTE FUNCTION public.reject_admin_project_creator_change();


--
-- Name: admin_projects admin_projects_ensure_llm_policy; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_projects_ensure_llm_policy AFTER INSERT ON public.admin_projects FOR EACH ROW EXECUTE FUNCTION public.ensure_llm_project_policy_defaults();


--
-- Name: admin_projects admin_projects_require_human_owner; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER admin_projects_require_human_owner AFTER INSERT OR UPDATE OF created_by, owner_user_id ON public.admin_projects DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_require_self_created_project_owner();


--
-- Name: admin_projects admin_projects_validate_owner; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_projects_validate_owner BEFORE INSERT OR UPDATE OF owner_user_id, project_id ON public.admin_projects FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_project_owner_assignment();


--
-- Name: admin_user_projects admin_user_projects_ensure_admin_project; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_user_projects_ensure_admin_project BEFORE INSERT OR UPDATE OF project_id ON public.admin_user_projects FOR EACH ROW EXECUTE FUNCTION public.ensure_admin_project_exists();


--
-- Name: admin_user_projects admin_user_projects_execution_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_user_projects_execution_authority BEFORE INSERT OR UPDATE OF project_id, roles ON public.admin_user_projects FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_roles();


--
-- Name: admin_user_projects admin_user_projects_protect_owner; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_user_projects_protect_owner BEFORE DELETE OR UPDATE ON public.admin_user_projects FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_project_owner_membership();


--
-- Name: admin_users admin_users_protect_active_owner; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER admin_users_protect_active_owner BEFORE UPDATE OF active ON public.admin_users FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_active_project_owner();


--
-- Name: agent_approval_effects agent_approval_effects_guard_live_lane_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_approval_effects_guard_live_lane_insert BEFORE INSERT ON public.agent_approval_effects FOR EACH ROW WHEN ((new.status = ANY (ARRAY['queued'::text, 'processing'::text, 'retryable_failed'::text]))) EXECUTE FUNCTION public.apdl_guard_agent_live_effect_lane();


--
-- Name: agent_approval_effects agent_approval_effects_guard_live_lane_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_approval_effects_guard_live_lane_update BEFORE UPDATE OF status, run_id, project_id ON public.agent_approval_effects FOR EACH ROW WHEN ((new.status = ANY (ARRAY['queued'::text, 'processing'::text, 'retryable_failed'::text]))) EXECUTE FUNCTION public.apdl_guard_agent_live_effect_lane();


--
-- Name: agent_runs agent_runs_guard_execution_lane_release; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_runs_guard_execution_lane_release BEFORE UPDATE OF status ON public.agent_runs FOR EACH ROW WHEN ((old.status IS DISTINCT FROM new.status)) EXECUTE FUNCTION public.apdl_guard_agent_execution_lane_release();


--
-- Name: analytics_data_deletion_audit analytics_data_deletion_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER analytics_data_deletion_audit_no_truncate BEFORE TRUNCATE ON public.analytics_data_deletion_audit FOR EACH STATEMENT EXECUTE FUNCTION public.prevent_analytics_data_deletion_audit_mutation();


--
-- Name: analytics_data_deletion_audit analytics_data_deletion_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER analytics_data_deletion_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.analytics_data_deletion_audit FOR EACH ROW EXECUTE FUNCTION public.prevent_analytics_data_deletion_audit_mutation();


--
-- Name: analytics_data_deletion_audit analytics_data_deletion_audit_validate_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER analytics_data_deletion_audit_validate_insert BEFORE INSERT ON public.analytics_data_deletion_audit FOR EACH ROW EXECUTE FUNCTION public.validate_analytics_data_deletion_audit_insert();


--
-- Name: agent_runs apdl_analysis_project_active; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_analysis_project_active BEFORE INSERT OR UPDATE OF project_id ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_analysis_table_project();


--
-- Name: custom_agent_test_runs apdl_analysis_project_active; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_analysis_project_active BEFORE INSERT OR UPDATE OF project_id ON public.custom_agent_test_runs FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_analysis_table_project();


--
-- Name: llm_calls apdl_analysis_project_active; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_analysis_project_active BEFORE INSERT OR UPDATE OF project_id ON public.llm_calls FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_analysis_table_project();


--
-- Name: llm_provider_attempts apdl_analysis_project_active; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_analysis_project_active BEFORE INSERT OR UPDATE OF project_id ON public.llm_provider_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_analysis_table_project();


--
-- Name: agent_approval_commands apdl_execution_project_authorized; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_execution_project_authorized BEFORE INSERT OR UPDATE OF project_id ON public.agent_approval_commands FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_table_project();


--
-- Name: agent_approval_effects apdl_execution_project_authorized; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_execution_project_authorized BEFORE INSERT OR UPDATE OF project_id ON public.agent_approval_effects FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_table_project();


--
-- Name: agent_mutation_quota_reservations apdl_execution_project_authorized; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_execution_project_authorized BEFORE INSERT OR UPDATE OF project_id ON public.agent_mutation_quota_reservations FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_table_project();


--
-- Name: codegen_changesets apdl_execution_project_authorized; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_execution_project_authorized BEFORE INSERT OR UPDATE OF project_id ON public.codegen_changesets FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_table_project();


--
-- Name: codegen_llm_attempts apdl_execution_project_authorized; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER apdl_execution_project_authorized BEFORE INSERT OR UPDATE OF project_id, status ON public.codegen_llm_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_codegen_llm_attempt_project();


--
-- Name: auth_credentials auth_credentials_ensure_admin_project; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER auth_credentials_ensure_admin_project BEFORE INSERT OR UPDATE OF project_id ON public.auth_credentials FOR EACH ROW EXECUTE FUNCTION public.ensure_admin_project_exists();


--
-- Name: auth_credentials auth_credentials_execution_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER auth_credentials_execution_authority BEFORE INSERT OR UPDATE OF project_id, roles ON public.auth_credentials FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_execution_roles();


--
-- Name: codegen_changesets codegen_changesets_enforce_repository_target; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_changesets_enforce_repository_target BEFORE INSERT OR UPDATE ON public.codegen_changesets FOR EACH ROW EXECUTE FUNCTION public.enforce_codegen_changeset_repository_target();


--
-- Name: codegen_changesets codegen_changesets_private_controls_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_changesets_private_controls_trigger BEFORE INSERT OR UPDATE OF control_metadata, project_id ON public.codegen_changesets FOR EACH ROW EXECUTE FUNCTION public.validate_codegen_changeset_private_controls();


--
-- Name: codegen_changesets codegen_changesets_protect_llm_snapshot; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_changesets_protect_llm_snapshot BEFORE INSERT OR UPDATE OF llm_execution_snapshot ON public.codegen_changesets FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_codegen_llm_snapshot();


--
-- Name: codegen_connections codegen_connections_require_active_grant; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_connections_require_active_grant BEFORE INSERT OR UPDATE OF project_id, grant_id ON public.codegen_connections FOR EACH ROW EXECUTE FUNCTION public.require_active_codegen_repository_grant();


--
-- Name: codegen_project_provider_connections codegen_connections_revalidate_model_assignments; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER codegen_connections_revalidate_model_assignments AFTER INSERT OR DELETE OR UPDATE ON public.codegen_project_provider_connections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_revalidate_codegen_model_assignments();


--
-- Name: codegen_llm_attempts codegen_llm_attempts_enforce_transition; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_llm_attempts_enforce_transition BEFORE UPDATE ON public.codegen_llm_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_codegen_llm_attempt_transition();


--
-- Name: codegen_llm_attempts codegen_llm_attempts_protect_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_llm_attempts_protect_identity BEFORE UPDATE ON public.codegen_llm_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_codegen_llm_attempt_identity();


--
-- Name: codegen_llm_attempts codegen_llm_attempts_validate_snapshot; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_llm_attempts_validate_snapshot BEFORE INSERT OR UPDATE OF project_id, changeset_id, role, provider, model_id, assignment_version ON public.codegen_llm_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_codegen_llm_attempt_snapshot();


--
-- Name: codegen_project_provider_models codegen_models_revalidate_model_assignments; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER codegen_models_revalidate_model_assignments AFTER INSERT OR DELETE OR UPDATE ON public.codegen_project_provider_models DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_revalidate_codegen_model_assignments();


--
-- Name: codegen_pull_request_publication_events codegen_pr_publication_events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_pr_publication_events_append_only BEFORE DELETE OR UPDATE ON public.codegen_pull_request_publication_events FOR EACH ROW EXECUTE FUNCTION public.reject_codegen_pr_publication_event_mutation();


--
-- Name: codegen_pull_request_publication_events codegen_pr_publication_events_require_intent; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_pr_publication_events_require_intent BEFORE INSERT ON public.codegen_pull_request_publication_events FOR EACH ROW EXECUTE FUNCTION public.enforce_codegen_pr_publication_intent_link();


--
-- Name: codegen_project_model_assignments codegen_project_model_assignments_validate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_project_model_assignments_validate BEFORE INSERT OR UPDATE ON public.codegen_project_model_assignments FOR EACH ROW EXECUTE FUNCTION public.apdl_validate_codegen_model_assignment();


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_project_provider_connection_audit_no_truncate BEFORE TRUNCATE ON public.codegen_project_provider_connection_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_codegen_connection_audit_mutation();


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER codegen_project_provider_connection_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.codegen_project_provider_connection_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_codegen_connection_audit_mutation();


--
-- Name: config_outbox_operator_log config_outbox_operator_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER config_outbox_operator_log_no_truncate BEFORE TRUNCATE ON public.config_outbox_operator_log FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();


--
-- Name: config_outbox_operator_log config_outbox_operator_log_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER config_outbox_operator_log_no_update_delete BEFORE DELETE OR UPDATE ON public.config_outbox_operator_log FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();


--
-- Name: event_pipeline_watermarks event_pipeline_watermarks_monotonic; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER event_pipeline_watermarks_monotonic BEFORE DELETE OR UPDATE ON public.event_pipeline_watermarks FOR EACH ROW EXECUTE FUNCTION public.enforce_event_pipeline_watermark_monotonicity();


--
-- Name: event_pipeline_watermarks event_pipeline_watermarks_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER event_pipeline_watermarks_no_truncate BEFORE TRUNCATE ON public.event_pipeline_watermarks FOR EACH STATEMENT EXECUTE FUNCTION public.reject_experiment_completeness_truncate();


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_analysis_boundaries_immutable BEFORE DELETE OR UPDATE ON public.experiment_analysis_boundaries FOR EACH ROW EXECUTE FUNCTION public.enforce_experiment_analysis_boundary_immutability();


--
-- Name: experiment_analysis_boundaries experiment_analysis_boundaries_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_analysis_boundaries_no_truncate BEFORE TRUNCATE ON public.experiment_analysis_boundaries FOR EACH STATEMENT EXECUTE FUNCTION public.reject_experiment_completeness_truncate();


--
-- Name: experiment_analysis_snapshots experiment_analysis_snapshots_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_analysis_snapshots_immutable BEFORE DELETE OR UPDATE ON public.experiment_analysis_snapshots FOR EACH ROW EXECUTE FUNCTION public.reject_experiment_analysis_snapshot_mutation();


--
-- Name: experiment_analysis_snapshots experiment_analysis_snapshots_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_analysis_snapshots_no_truncate BEFORE TRUNCATE ON public.experiment_analysis_snapshots FOR EACH STATEMENT EXECUTE FUNCTION public.reject_experiment_completeness_truncate();


--
-- Name: experiment_audit_log experiment_audit_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_audit_log_no_truncate BEFORE TRUNCATE ON public.experiment_audit_log FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_experiment_audit_mutation();


--
-- Name: experiment_audit_log experiment_audit_log_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_audit_log_no_update_delete BEFORE DELETE OR UPDATE ON public.experiment_audit_log FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_experiment_audit_mutation();


--
-- Name: experiment_audit_purge_log experiment_audit_purge_log_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_audit_purge_log_no_truncate BEFORE TRUNCATE ON public.experiment_audit_purge_log FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();


--
-- Name: experiment_audit_purge_log experiment_audit_purge_log_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiment_audit_purge_log_no_update_delete BEFORE DELETE OR UPDATE ON public.experiment_audit_purge_log FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_operator_audit_mutation();


--
-- Name: experiments experiments_enforce_archive_lifecycle; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiments_enforce_archive_lifecycle BEFORE DELETE OR UPDATE ON public.experiments FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_experiment_archive_lifecycle();


--
-- Name: experiments experiments_enforce_enrollment_immutability; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiments_enforce_enrollment_immutability BEFORE UPDATE OF status, bucket_by, traffic_percentage, targeting_rules_json, minimum_exposure_config_version ON public.experiments FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_experiment_enrollment_immutability();


--
-- Name: experiments experiments_enforce_statistical_plan; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiments_enforce_statistical_plan BEFORE INSERT OR UPDATE ON public.experiments FOR EACH ROW EXECUTE FUNCTION public.apdl_enforce_experiment_statistical_plan();


--
-- Name: experiments experiments_immutable_flag_ownership; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER experiments_immutable_flag_ownership BEFORE UPDATE OF project_id, flag_key ON public.experiments FOR EACH ROW EXECUTE FUNCTION public.reject_experiment_flag_ownership_change();


--
-- Name: github_repository_grants github_repository_grants_enforce_lifecycle; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER github_repository_grants_enforce_lifecycle BEFORE UPDATE ON public.github_repository_grants FOR EACH ROW EXECUTE FUNCTION public.enforce_github_repository_grant_lifecycle();


--
-- Name: github_repository_grants github_repository_grants_prevent_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER github_repository_grants_prevent_delete BEFORE DELETE ON public.github_repository_grants FOR EACH ROW EXECUTE FUNCTION public.prevent_github_repository_grant_deletion();


--
-- Name: llm_project_model_assignments llm_project_model_assignments_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_project_model_assignments_validate_active_setup AFTER INSERT OR DELETE OR UPDATE ON public.llm_project_model_assignments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_project_policies llm_project_policies_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_project_policies_validate_active_setup AFTER INSERT OR UPDATE ON public.llm_project_policies DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_project_policy_audit llm_project_policy_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_policy_audit_no_truncate BEFORE TRUNCATE ON public.llm_project_policy_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_policy_audit_mutation();


--
-- Name: llm_project_policy_audit llm_project_policy_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_policy_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_project_policy_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_policy_audit_mutation();


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_provider_connection_audit_no_truncate BEFORE TRUNCATE ON public.llm_project_provider_connection_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_connection_audit_mutation();


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_provider_connection_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_project_provider_connection_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_connection_audit_mutation();


--
-- Name: llm_project_provider_connections llm_project_provider_connections_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_project_provider_connections_validate_active_setup AFTER INSERT OR DELETE OR UPDATE ON public.llm_project_provider_connections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_project_provider_models llm_project_provider_models_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_project_provider_models_validate_active_setup AFTER INSERT OR DELETE OR UPDATE ON public.llm_project_provider_models DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_project_provider_policies llm_project_provider_policies_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_project_provider_policies_validate_active_setup AFTER INSERT OR DELETE OR UPDATE ON public.llm_project_provider_policies DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_project_setup_audit llm_project_setup_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_setup_audit_no_truncate BEFORE TRUNCATE ON public.llm_project_setup_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_setup_audit_mutation();


--
-- Name: llm_project_setup_audit llm_project_setup_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_project_setup_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_project_setup_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_setup_audit_mutation();


--
-- Name: llm_provider_attempts llm_provider_attempts_protect_credential_binding; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_provider_attempts_protect_credential_binding BEFORE UPDATE OF credential_id, credential_version ON public.llm_provider_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_llm_attempt_credential_binding();


--
-- Name: llm_provider_attempts llm_provider_attempts_protect_setup_binding; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_provider_attempts_protect_setup_binding BEFORE UPDATE OF setup_version, model_tier, connection_version, inventory_version, model_catalog_version ON public.llm_provider_attempts FOR EACH ROW EXECUTE FUNCTION public.apdl_protect_llm_attempt_setup_binding();


--
-- Name: llm_vault_access_audit llm_vault_access_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_access_audit_no_truncate BEFORE TRUNCATE ON public.llm_vault_access_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_access_audit llm_vault_access_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_access_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_vault_access_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_audit llm_vault_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_audit_no_truncate BEFORE TRUNCATE ON public.llm_vault_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_audit llm_vault_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_vault_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_connections llm_vault_connections_validate_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_connections_validate_authority AFTER INSERT OR UPDATE ON public.llm_vault_connections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger();


--
-- Name: llm_vault_connection_consumers llm_vault_consumers_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_consumers_validate_active_setup AFTER INSERT OR DELETE OR UPDATE ON public.llm_vault_connection_consumers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_vault_connection_consumers llm_vault_consumers_validate_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_consumers_validate_authority AFTER INSERT OR DELETE OR UPDATE ON public.llm_vault_connection_consumers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger();


--
-- Name: llm_vault_provider_credentials llm_vault_credentials_validate_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_credentials_validate_authority AFTER INSERT OR DELETE OR UPDATE ON public.llm_vault_provider_credentials DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger();


--
-- Name: llm_vault_key_rotation_audit llm_vault_key_rotation_audit_no_truncate; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_key_rotation_audit_no_truncate BEFORE TRUNCATE ON public.llm_vault_key_rotation_audit FOR EACH STATEMENT EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_key_rotation_audit llm_vault_key_rotation_audit_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER llm_vault_key_rotation_audit_no_update_delete BEFORE DELETE OR UPDATE ON public.llm_vault_key_rotation_audit FOR EACH ROW EXECUTE FUNCTION public.apdl_reject_llm_vault_audit_mutation();


--
-- Name: llm_vault_provider_models llm_vault_models_validate_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_models_validate_authority AFTER INSERT OR DELETE OR UPDATE ON public.llm_vault_provider_models DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger();


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_validate_active_setup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_provider_credentials_validate_active_setup AFTER DELETE OR UPDATE ON public.llm_vault_provider_credentials DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_active_agents_setup_trigger();


--
-- Name: llm_vault_provider_secrets llm_vault_secrets_validate_authority; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER llm_vault_secrets_validate_authority AFTER INSERT OR DELETE OR UPDATE ON public.llm_vault_provider_secrets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.apdl_check_llm_vault_connection_authority_trigger();


--
-- Name: admin_credential_audit admin_credential_audit_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_credential_audit
    ADD CONSTRAINT admin_credential_audit_credential_fk FOREIGN KEY (credential_id, project_id) REFERENCES public.admin_managed_credentials(credential_id, project_id) ON DELETE RESTRICT;


--
-- Name: admin_credential_audit admin_credential_audit_successor_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_credential_audit
    ADD CONSTRAINT admin_credential_audit_successor_fk FOREIGN KEY (successor_credential_id, project_id) REFERENCES public.admin_managed_credentials(credential_id, project_id) ON DELETE RESTRICT;


--
-- Name: admin_login_account_risk admin_login_account_risk_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_account_risk
    ADD CONSTRAINT admin_login_account_risk_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.admin_users(user_id) ON DELETE CASCADE;


--
-- Name: admin_managed_credentials admin_managed_credentials_auth_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_managed_credentials
    ADD CONSTRAINT admin_managed_credentials_auth_fk FOREIGN KEY (credential_id, project_id) REFERENCES public.auth_credentials(credential_id, project_id) ON DELETE RESTRICT;


--
-- Name: admin_managed_credentials admin_managed_credentials_rotated_from_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_managed_credentials
    ADD CONSTRAINT admin_managed_credentials_rotated_from_fk FOREIGN KEY (rotated_from_credential_id, project_id) REFERENCES public.admin_managed_credentials(credential_id, project_id) ON DELETE RESTRICT;


--
-- Name: admin_project_execution_authorizations admin_project_execution_authorizations_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_execution_authorizations
    ADD CONSTRAINT admin_project_execution_authorizations_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: admin_project_invitations admin_project_invitations_accepted_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_invitations
    ADD CONSTRAINT admin_project_invitations_accepted_by_user_id_fkey FOREIGN KEY (accepted_by_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_invitations admin_project_invitations_inviter_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_invitations
    ADD CONSTRAINT admin_project_invitations_inviter_user_id_fkey FOREIGN KEY (inviter_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_invitations admin_project_invitations_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_invitations
    ADD CONSTRAINT admin_project_invitations_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: admin_project_membership_audit admin_project_membership_audit_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_membership_audit
    ADD CONSTRAINT admin_project_membership_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_membership_audit admin_project_membership_audit_invitation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_membership_audit
    ADD CONSTRAINT admin_project_membership_audit_invitation_id_fkey FOREIGN KEY (invitation_id) REFERENCES public.admin_project_invitations(invitation_id) ON DELETE RESTRICT;


--
-- Name: admin_project_membership_audit admin_project_membership_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_membership_audit
    ADD CONSTRAINT admin_project_membership_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: admin_project_membership_audit admin_project_membership_audit_subject_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_membership_audit
    ADD CONSTRAINT admin_project_membership_audit_subject_user_id_fkey FOREIGN KEY (subject_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_new_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_ownership_audit
    ADD CONSTRAINT admin_project_ownership_audit_new_owner_user_id_fkey FOREIGN KEY (new_owner_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_previous_owner_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_ownership_audit
    ADD CONSTRAINT admin_project_ownership_audit_previous_owner_user_id_fkey FOREIGN KEY (previous_owner_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_project_ownership_audit admin_project_ownership_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_project_ownership_audit
    ADD CONSTRAINT admin_project_ownership_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: admin_projects admin_projects_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_projects
    ADD CONSTRAINT admin_projects_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_projects admin_projects_owner_user_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_projects
    ADD CONSTRAINT admin_projects_owner_user_fk FOREIGN KEY (owner_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: admin_proxy_audit admin_proxy_audit_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_proxy_audit
    ADD CONSTRAINT admin_proxy_audit_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.admin_users(user_id) ON DELETE SET NULL;


--
-- Name: admin_security_notifications admin_security_notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_security_notifications
    ADD CONSTRAINT admin_security_notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.admin_users(user_id) ON DELETE CASCADE;


--
-- Name: admin_sessions admin_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_sessions
    ADD CONSTRAINT admin_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.admin_users(user_id) ON DELETE CASCADE;


--
-- Name: admin_user_projects admin_user_projects_admin_project_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_projects
    ADD CONSTRAINT admin_user_projects_admin_project_fk FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id);


--
-- Name: admin_user_projects admin_user_projects_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_projects
    ADD CONSTRAINT admin_user_projects_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.admin_users(user_id) ON DELETE CASCADE;


--
-- Name: agent_approval_commands agent_approval_commands_run_project_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_commands
    ADD CONSTRAINT agent_approval_commands_run_project_fk FOREIGN KEY (run_id, project_id) REFERENCES public.agent_runs(run_id, project_id) ON DELETE CASCADE;


--
-- Name: agent_approval_decisions agent_approval_decisions_command_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_decisions
    ADD CONSTRAINT agent_approval_decisions_command_id_fkey FOREIGN KEY (command_id) REFERENCES public.agent_approval_commands(command_id) ON DELETE CASCADE;


--
-- Name: agent_approval_effects agent_approval_effects_command_project_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_command_project_fk FOREIGN KEY (command_id, run_id, project_id) REFERENCES public.agent_approval_commands(command_id, run_id, project_id) ON DELETE CASCADE;


--
-- Name: agent_approval_effects agent_approval_effects_dependency_command_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_approval_effects
    ADD CONSTRAINT agent_approval_effects_dependency_command_fk FOREIGN KEY (command_id, depends_on_effect_id) REFERENCES public.agent_approval_effects(command_id, effect_id) ON DELETE RESTRICT;


--
-- Name: agent_mutation_quota_reservations agent_mutation_quota_reservations_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_mutation_quota_reservations
    ADD CONSTRAINT agent_mutation_quota_reservations_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE CASCADE;


--
-- Name: auth_credentials auth_credentials_admin_project_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.auth_credentials
    ADD CONSTRAINT auth_credentials_admin_project_fk FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id);


--
-- Name: codegen_changesets codegen_changesets_repository_grant_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_changesets
    ADD CONSTRAINT codegen_changesets_repository_grant_fkey FOREIGN KEY (project_id, repository_grant_id) REFERENCES public.github_repository_grants(project_id, grant_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: codegen_changesets codegen_changesets_retry_of_changeset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_changesets
    ADD CONSTRAINT codegen_changesets_retry_of_changeset_id_fkey FOREIGN KEY (project_id, retry_of_changeset_id) REFERENCES public.codegen_changesets(project_id, changeset_id) ON DELETE RESTRICT;


--
-- Name: codegen_connections codegen_connections_authorized_grant_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_connections
    ADD CONSTRAINT codegen_connections_authorized_grant_fkey FOREIGN KEY (project_id, grant_id) REFERENCES public.github_repository_grants(project_id, grant_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: codegen_llm_attempts codegen_llm_attempts_changeset_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_llm_attempts
    ADD CONSTRAINT codegen_llm_attempts_changeset_fk FOREIGN KEY (changeset_id, project_id) REFERENCES public.codegen_changesets(changeset_id, project_id) ON DELETE RESTRICT;


--
-- Name: codegen_llm_attempts codegen_llm_attempts_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_llm_attempts
    ADD CONSTRAINT codegen_llm_attempts_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider, credential_version) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider, credential_version) ON DELETE RESTRICT;


--
-- Name: codegen_project_model_assignments codegen_project_model_assignments_model_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_model_assignments
    ADD CONSTRAINT codegen_project_model_assignments_model_fk FOREIGN KEY (project_id, provider, model_id, connection_version, inventory_version, catalog_version) REFERENCES public.codegen_project_provider_models(project_id, provider, model_id, connection_version, inventory_version, catalog_version) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: codegen_project_model_assignments codegen_project_model_assignments_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_model_assignments
    ADD CONSTRAINT codegen_project_model_assignments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connection_audit
    ADD CONSTRAINT codegen_project_provider_connection_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connection_audit
    ADD CONSTRAINT codegen_project_provider_connection_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_connection_audit codegen_project_provider_connection_audit_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connection_audit
    ADD CONSTRAINT codegen_project_provider_connection_audit_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_connections codegen_project_provider_connections_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_connections
    ADD CONSTRAINT codegen_project_provider_connections_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: codegen_project_provider_models codegen_project_provider_models_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_project_provider_models
    ADD CONSTRAINT codegen_project_provider_models_connection_fk FOREIGN KEY (project_id, provider, connection_version, inventory_version, catalog_version) REFERENCES public.codegen_project_provider_connections(project_id, provider, version, inventory_version, catalog_version) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: codegen_pull_request_publication_events codegen_pull_request_publication_events_changeset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.codegen_pull_request_publication_events
    ADD CONSTRAINT codegen_pull_request_publication_events_changeset_id_fkey FOREIGN KEY (changeset_id) REFERENCES public.codegen_changesets(changeset_id) ON DELETE RESTRICT;


--
-- Name: experiment_analysis_snapshots experiment_analysis_snapshots_project_id_experiment_key_co_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment_analysis_snapshots
    ADD CONSTRAINT experiment_analysis_snapshots_project_id_experiment_key_co_fkey FOREIGN KEY (project_id, experiment_key, config_version, boundary_stream_id) REFERENCES public.experiment_analysis_boundaries(project_id, experiment_key, config_version, marker_stream_id);


--
-- Name: experiments experiments_flag_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiments
    ADD CONSTRAINT experiments_flag_key_fkey FOREIGN KEY (project_id, flag_key) REFERENCES public.flags(project_id, key) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: github_repository_grants github_repository_grants_project_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.github_repository_grants
    ADD CONSTRAINT github_repository_grants_project_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON UPDATE RESTRICT ON DELETE RESTRICT;


--
-- Name: llm_calls llm_calls_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT llm_calls_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE CASCADE;


--
-- Name: llm_project_model_assignments llm_project_model_assignments_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_model_assignments
    ADD CONSTRAINT llm_project_model_assignments_connection_fk FOREIGN KEY (project_id, provider) REFERENCES public.llm_project_provider_connections(project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_project_model_assignments llm_project_model_assignments_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_model_assignments
    ADD CONSTRAINT llm_project_model_assignments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.llm_project_policies(project_id) ON DELETE CASCADE;


--
-- Name: llm_project_policies llm_project_policies_activated_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policies
    ADD CONSTRAINT llm_project_policies_activated_by_actor_user_id_fkey FOREIGN KEY (activated_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_project_policies llm_project_policies_deactivated_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policies
    ADD CONSTRAINT llm_project_policies_deactivated_by_actor_user_id_fkey FOREIGN KEY (deactivated_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_project_policies llm_project_policies_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policies
    ADD CONSTRAINT llm_project_policies_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE CASCADE;


--
-- Name: llm_project_policy_audit llm_project_policy_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_policy_audit
    ADD CONSTRAINT llm_project_policy_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connection_audit
    ADD CONSTRAINT llm_project_provider_connection_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connection_audit
    ADD CONSTRAINT llm_project_provider_connection_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_connection_audit llm_project_provider_connection_audit_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connection_audit
    ADD CONSTRAINT llm_project_provider_connection_audit_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_connections llm_project_provider_connections_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connections
    ADD CONSTRAINT llm_project_provider_connections_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_connections llm_project_provider_connections_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_connections
    ADD CONSTRAINT llm_project_provider_connections_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_project_provider_models llm_project_provider_models_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_models
    ADD CONSTRAINT llm_project_provider_models_connection_fk FOREIGN KEY (project_id, provider, connection_version) REFERENCES public.llm_project_provider_connections(project_id, provider, version) ON DELETE CASCADE;


--
-- Name: llm_project_provider_models llm_project_provider_models_inventory_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_models
    ADD CONSTRAINT llm_project_provider_models_inventory_fk FOREIGN KEY (project_id, provider, inventory_version) REFERENCES public.llm_project_provider_connections(project_id, provider, inventory_version) ON DELETE CASCADE;


--
-- Name: llm_project_provider_policies llm_project_provider_policies_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_provider_policies
    ADD CONSTRAINT llm_project_provider_policies_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.llm_project_policies(project_id) ON DELETE CASCADE;


--
-- Name: llm_project_setup_audit llm_project_setup_audit_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_setup_audit
    ADD CONSTRAINT llm_project_setup_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_project_setup_audit llm_project_setup_audit_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_project_setup_audit
    ADD CONSTRAINT llm_project_setup_audit_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: llm_provider_attempts llm_provider_attempts_call_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_provider_attempts
    ADD CONSTRAINT llm_provider_attempts_call_fk FOREIGN KEY (project_id, run_id, call_id, execution_owner_id) REFERENCES public.llm_calls(project_id, run_id, call_id, execution_owner_id) ON DELETE CASCADE;


--
-- Name: llm_provider_attempts llm_provider_attempts_vault_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_provider_attempts
    ADD CONSTRAINT llm_provider_attempts_vault_credential_fk FOREIGN KEY (credential_id, project_id, provider, credential_version) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider, credential_version) ON DELETE RESTRICT;


--
-- Name: llm_vault_access_audit llm_vault_access_audit_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_access_audit
    ADD CONSTRAINT llm_vault_access_audit_connection_fk FOREIGN KEY (connection_id, project_id, provider) REFERENCES public.llm_vault_connections(connection_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_vault_access_audit llm_vault_access_audit_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_access_audit
    ADD CONSTRAINT llm_vault_access_audit_credential_fk FOREIGN KEY (credential_id, project_id, provider, credential_version) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider, credential_version) ON DELETE RESTRICT;


--
-- Name: llm_vault_audit llm_vault_audit_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_audit
    ADD CONSTRAINT llm_vault_audit_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_audit llm_vault_audit_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_audit
    ADD CONSTRAINT llm_vault_audit_connection_fk FOREIGN KEY (connection_id, project_id, provider) REFERENCES public.llm_vault_connections(connection_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_vault_audit llm_vault_audit_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_audit
    ADD CONSTRAINT llm_vault_audit_credential_fk FOREIGN KEY (credential_id, project_id, provider, credential_version) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider, credential_version) ON DELETE RESTRICT;


--
-- Name: llm_vault_connection_consumers llm_vault_connection_consumers_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connection_consumers
    ADD CONSTRAINT llm_vault_connection_consumers_connection_fk FOREIGN KEY (connection_id, project_id, provider) REFERENCES public.llm_vault_connections(connection_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_vault_connection_consumers llm_vault_connection_consumers_granted_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connection_consumers
    ADD CONSTRAINT llm_vault_connection_consumers_granted_by_actor_user_id_fkey FOREIGN KEY (granted_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_connections llm_vault_connections_created_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_created_by_actor_user_id_fkey FOREIGN KEY (created_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_connections llm_vault_connections_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.admin_projects(project_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_connections llm_vault_connections_revoked_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_connections
    ADD CONSTRAINT llm_vault_connections_revoked_by_actor_user_id_fkey FOREIGN KEY (revoked_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_key_rotation_audit llm_vault_key_rotation_audit_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_key_rotation_audit
    ADD CONSTRAINT llm_vault_key_rotation_audit_connection_fk FOREIGN KEY (connection_id, project_id, provider) REFERENCES public.llm_vault_connections(connection_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_vault_key_rotation_audit llm_vault_key_rotation_audit_credential_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_key_rotation_audit
    ADD CONSTRAINT llm_vault_key_rotation_audit_credential_fk FOREIGN KEY (credential_id, project_id, provider, credential_version) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider, credential_version) ON DELETE RESTRICT;


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_connection_fk FOREIGN KEY (connection_id, project_id, provider) REFERENCES public.llm_vault_connections(connection_id, project_id, provider) ON DELETE RESTRICT;


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_created_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_created_by_actor_user_id_fkey FOREIGN KEY (created_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_retired_by_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_retired_by_actor_user_id_fkey FOREIGN KEY (retired_by_actor_user_id) REFERENCES public.admin_users(user_id) ON DELETE RESTRICT;


--
-- Name: llm_vault_provider_credentials llm_vault_provider_credentials_successor_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_credentials
    ADD CONSTRAINT llm_vault_provider_credentials_successor_fk FOREIGN KEY (successor_credential_id, project_id, provider) REFERENCES public.llm_vault_provider_credentials(credential_id, project_id, provider) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: llm_vault_provider_models llm_vault_provider_models_connection_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_models
    ADD CONSTRAINT llm_vault_provider_models_connection_fk FOREIGN KEY (connection_id, connection_version) REFERENCES public.llm_vault_connections(connection_id, version) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: llm_vault_provider_secrets llm_vault_provider_secrets_credential_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_vault_provider_secrets
    ADD CONSTRAINT llm_vault_provider_secrets_credential_id_fkey FOREIGN KEY (credential_id) REFERENCES public.llm_vault_provider_credentials(credential_id) ON DELETE RESTRICT;


--
-- PostgreSQL database dump complete
--

\unrestrict VdSGORKEltZNGKU9SKTRyNQeUqdsqzUYYcO2pyQDUKO0cT9ayksKMpq7EUIFsfO


SET search_path = public, pg_catalog;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public
    FROM apdl_runtime, apdl_llm_vault, apdl_audit_operator,
         apdl_audit_purge_definer;
REVOKE ALL ON TABLE public.analytics_data_deletion_audit FROM PUBLIC;
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
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO apdl_llm_vault',
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
    ON public.config_outbox_operator_log
    FROM apdl_runtime;
REVOKE INSERT, UPDATE, DELETE
    ON public.experiment_audit_purge_log
    FROM apdl_runtime;
REVOKE UPDATE, DELETE
    ON public.experiment_audit_log
    FROM apdl_runtime;

GRANT USAGE ON SCHEMA public TO apdl_audit_purge_definer;
GRANT SELECT, DELETE
    ON public.experiment_audit_log
    TO apdl_audit_purge_definer;
GRANT INSERT
    ON public.experiment_audit_purge_log
    TO apdl_audit_purge_definer;
GRANT USAGE, SELECT
    ON SEQUENCE public.experiment_audit_purge_log_id_seq
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
    ON public.experiment_audit_log, public.experiment_audit_purge_log
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

GRANT USAGE ON SCHEMA public TO apdl_llm_vault;
REVOKE ALL ON FUNCTION public.apdl_llm_vault_has_management_authority(
    TEXT,
    UUID
) FROM PUBLIC, apdl_runtime;
GRANT EXECUTE ON FUNCTION public.apdl_llm_vault_has_management_authority(
    TEXT,
    UUID
) TO apdl_llm_vault;

REVOKE ALL ON
    public.llm_vault_connections,
    public.llm_vault_provider_credentials,
    public.llm_vault_provider_secrets,
    public.llm_vault_connection_consumers,
    public.llm_vault_provider_models,
    public.llm_vault_audit,
    public.llm_vault_access_audit,
    public.llm_vault_key_rotation_audit
FROM PUBLIC, apdl_runtime;

GRANT SELECT ON public.llm_vault_connections TO apdl_runtime;
GRANT SELECT ON public.llm_vault_provider_credentials TO apdl_runtime;
GRANT SELECT ON public.llm_vault_connection_consumers TO apdl_runtime;
GRANT SELECT ON public.llm_vault_provider_models TO apdl_runtime;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.llm_vault_connections,
    public.llm_vault_provider_credentials,
    public.llm_vault_provider_secrets,
    public.llm_vault_connection_consumers,
    public.llm_vault_provider_models,
    public.llm_project_provider_connections,
    public.llm_project_provider_models,
    public.llm_project_provider_connection_audit,
    public.codegen_project_provider_connections,
    public.codegen_project_provider_models,
    public.codegen_project_provider_connection_audit
TO apdl_llm_vault;

GRANT SELECT, INSERT ON
    public.llm_vault_audit,
    public.llm_vault_access_audit,
    public.llm_vault_key_rotation_audit
TO apdl_llm_vault;

GRANT SELECT ON
    public.admin_projects,
    public.admin_users,
    public.admin_user_projects,
    public.llm_project_policies,
    public.llm_project_model_assignments,
    public.codegen_project_model_assignments
TO apdl_llm_vault;

GRANT UPDATE ON public.codegen_project_model_assignments TO apdl_llm_vault;

INSERT INTO public.apdl_execution_table_registry (table_name)
VALUES
    ('public.agent_approval_commands'),
    ('public.agent_approval_effects'),
    ('public.agent_mutation_quota_reservations'),
    ('public.codegen_changesets'),
    ('public.codegen_llm_attempts');

INSERT INTO public.apdl_analysis_table_registry (table_name)
VALUES
    ('public.agent_runs'),
    ('public.custom_agent_test_runs'),
    ('public.llm_calls'),
    ('public.llm_provider_attempts');

SELECT public.apdl_assert_execution_table_registry();
SELECT public.apdl_assert_analysis_table_registry();
