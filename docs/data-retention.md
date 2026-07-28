# Analytics data retention and deletion

APDL has one retention authority for personally attributable analytics:
the date of the server-generated `received_at` timestamp. Client event time
remains queryable but cannot extend or shorten retention.

## Retention by data class

| Data class | ClickHouse tables | Retention |
|---|---|---|
| Raw behavior and delivery provenance | `events`, `experiment_event_deliveries` | 12 calendar months from the receipt date |
| Derived experience and health analytics | `feature_flag_exposures`, `frontend_health_events` | 12 calendar months from the source event's receipt date |
| Session analytics | `sessions` | 12 calendar months from the latest matching source receipt; a legacy session with no retained source is anchored to migration time |
| Identity linkage | `identity_alias_assertions` and the computed `resolved_identity_aliases` view | 12 calendar months from the assertion's receipt date |
| Deletion evidence | PostgreSQL `analytics_data_deletion_audit` | No automatic expiry; append-only operator audit evidence |
| Config experiment change snapshots | PostgreSQL `experiment_audit_log` | No automatic expiry; project/cutoff purge is available only through the audited migration-044 operator function |
| Delivery recovery evidence | PostgreSQL `config_outbox_operator_log` | No automatic expiry; immutable and payload-free |
| Experiment snapshot purge evidence | PostgreSQL `experiment_audit_purge_log` | No automatic expiry; immutable and payload-free |

All retained ClickHouse tables partition by project rather than client event
time. ClickHouse applies TTL removal asynchronously. The deletion workflow
below instead waits for every mutation replica and verifies that every explicit
target table has zero matching rows before recording completion.

Identity resolution is computed directly from retained assertions. There is no
separate irreversible aggregate state that can outlive its source assertions.
The deletion ledger stores a SHA-256 digest for a user target, never the raw
user ID. A project ID remains visible because it is the tenant audit boundary.

## Project and user deletion

Apply both migration sequences before deletion:

```bash
make migrate-postgres
make migrate-clickhouse
```

Prevent the source from creating replacement data first. For a project,
disable or revoke its event credentials and stop its producers. For a user,
ensure the application has stopped identifying or tracking that user.
The mutations are destructive and have no application-level undo; preserve any
lawful backup required by the operator's recovery policy before proceeding.

Generate a unique UUID in the operator's normal change-management system. Run
one of the strict commands from the repository root:

```bash
scripts/delete-analytics-data.sh project \
  --request-id 11111111-1111-4111-8111-111111111111 \
  --project-id demo \
  --actor privacy-operator \
  --reason "approved project erasure request"
```

```bash
scripts/delete-analytics-data.sh user \
  --request-id 22222222-2222-4222-8222-222222222222 \
  --project-id demo \
  --user-id user-42 \
  --actor privacy-operator \
  --reason "verified user erasure request"
```

`CLICKHOUSE_COMPOSE_FILE` selects a non-default supported Compose file. The
script resolves only its `clickhouse` and `postgres` containers and then takes
the same exclusive PostgreSQL maintenance barriers and ClickHouse runtime-write
gate used by schema migrations.

A project request removes its rows from all six retained personal-data tables.
A user request also resolves the user's retained anonymous aliases and removes
rows bearing either the user ID or those anonymous IDs. Derived tables are
deleted first, base events next, and alias assertions last. Keeping assertions
until last makes an interrupted request able to rediscover its aliases.

The tool has a fixed table allowlist; callers cannot provide table names or SQL.
Its ClickHouse principal needs `SELECT` and `ALTER DELETE` on those six tables,
schema-ledger read access, and the existing maintenance-gate permissions. Its
PostgreSQL principal needs advisory-lock authority plus `SELECT` and `INSERT`
on `analytics_data_deletion_audit`. The default Compose owner has these
permissions; deployments should use a dedicated maintenance principal.

The ledger records `requested` before any mutation and records `completed`
only after zero-row verification. Retry an interrupted command with exactly
the same request UUID, scope, project, user target, actor, and reason. A
completed retry performs no mutations and reports `already_completed`; reuse
of the UUID with different input fails closed. Update, delete, and truncate of
audit evidence are rejected by PostgreSQL triggers.

## Experiment audit snapshot retention

Config's `experiment_audit_log` contains full before/after lifecycle snapshots
and is deliberately outside the automatic ClickHouse TTL. When a privacy or
retention policy requires removal, migration
`044_operator_recovery_and_retention.sql` supplies the project-scoped
`public.apdl_purge_experiment_audit(...)` function and an immutable,
payload-free `experiment_audit_purge_log`.

The function is destructive, requires the exact confirmation phrase, and is
not executable by `PUBLIC` or the runtime service role. Its immutable actor is
the named PostgreSQL `session_user`; a separate ungranted `NOLOGIN` definer
holds only the narrow delete/log privileges. Follow the preview, execution,
verification, and privilege guidance in
[Experiment finality and delivery recovery](experiment-finality-and-delivery-recovery.md#privacy-purge-for-experiment-change-snapshots).
It does not replace the analytics deletion workflow above and does not delete
Query's immutable verified decision snapshots.

Operator-log `reason` and `actor` fields are retained evidence, not a place to
copy the data being recovered or deleted. Outbox recovery records the
authenticated Config credential ID; use a stable, non-personal operator
credential ID. Experiment purge records the named, non-shared PostgreSQL
`session_user`. Use a ticket/policy reference for either reason. Never include
raw user identifiers, payloads, secrets, free-form incident transcripts, or
unnecessary personal data. If an identity-management policy requires a
personally identifying login name, treat that actor field as personal data in
access controls and retention reviews.
