-- Project ownership carries every canonical human project permission.  The
-- operator execution-authorization record remains an independent prerequisite
-- for credentials and effectful execution.

CREATE OR REPLACE FUNCTION apdl_all_admin_roles()
RETURNS TEXT[]
LANGUAGE SQL
IMMUTABLE
AS $apdl_all_admin_roles$
    SELECT public.apdl_canonical_admin_roles(
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
    )
$apdl_all_admin_roles$;

-- Human permissions and executable service authority are separate contracts.
-- A human membership may carry agents:approve, while the surviving credential
-- trigger still requires immutable project execution authorization before that
-- role can be placed on an auth credential.
DROP TRIGGER IF EXISTS admin_user_projects_execution_authority
    ON admin_user_projects;

-- Reconcile every owner created before this invariant existed.  Migration 046
-- already guarantees that each owner is an active member with members:manage.
UPDATE admin_user_projects AS membership
SET roles = public.apdl_all_admin_roles()
FROM admin_projects AS project
WHERE project.project_id = membership.project_id
  AND project.owner_user_id = membership.user_id
  AND membership.roles IS DISTINCT FROM public.apdl_all_admin_roles();

CREATE OR REPLACE FUNCTION apdl_protect_project_owner_membership()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $apdl_protect_project_owner_membership$
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
                MESSAGE = 'project owner membership and all project permissions are required';
        END IF;
        RETURN OLD;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM admin_projects
        WHERE project_id = OLD.project_id
          AND owner_user_id = OLD.user_id
    ) AND (
        NEW.project_id IS DISTINCT FROM OLD.project_id
        OR NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.roles IS DISTINCT FROM public.apdl_all_admin_roles()
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project owner membership and all project permissions are required';
    END IF;

    RETURN NEW;
END
$apdl_protect_project_owner_membership$;

-- Existing application and operator flows first select an eligible member and
-- then update owner_user_id.  Widen that membership inside the same statement's
-- transaction so every assignment path establishes the complete role set.
CREATE OR REPLACE FUNCTION apdl_sync_project_owner_permissions()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $apdl_sync_project_owner_permissions$
BEGIN
    IF NEW.owner_user_id IS NULL THEN
        RETURN NEW;
    END IF;

    UPDATE admin_user_projects
    SET roles = public.apdl_all_admin_roles()
    WHERE project_id = NEW.project_id
      AND user_id = NEW.owner_user_id
      AND roles IS DISTINCT FROM public.apdl_all_admin_roles();

    RETURN NEW;
END
$apdl_sync_project_owner_permissions$;

CREATE TRIGGER admin_projects_sync_owner_permissions
AFTER INSERT OR UPDATE OF owner_user_id, project_id ON admin_projects
FOR EACH ROW
EXECUTE FUNCTION apdl_sync_project_owner_permissions();

-- Keep an end-of-transaction assertion in addition to the widening trigger.
-- This rejects incomplete direct assignments if either side of the ownership
-- transaction is changed by a future caller without establishing the invariant.
CREATE OR REPLACE FUNCTION apdl_require_full_project_owner_permissions()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $apdl_require_full_project_owner_permissions$
DECLARE
    current_owner_user_id UUID;
BEGIN
    SELECT project.owner_user_id
    INTO current_owner_user_id
    FROM admin_projects AS project
    WHERE project.project_id = NEW.project_id;

    IF NOT FOUND OR current_owner_user_id IS NULL THEN
        RETURN NULL;
    END IF;

    PERFORM 1
    FROM admin_users AS account
    JOIN admin_user_projects AS membership
      ON membership.user_id = account.user_id
     AND membership.project_id = NEW.project_id
    WHERE account.user_id = current_owner_user_id
      AND account.active
      AND membership.roles = public.apdl_all_admin_roles();

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'project owner must be an active member with all project permissions';
    END IF;

    RETURN NULL;
END
$apdl_require_full_project_owner_permissions$;

CREATE CONSTRAINT TRIGGER admin_projects_require_full_owner_permissions
AFTER INSERT OR UPDATE OF owner_user_id, project_id ON admin_projects
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION apdl_require_full_project_owner_permissions();
