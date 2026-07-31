# Pipeline

The APDL data pipeline moves events from Redis Streams into ClickHouse and owns
the ClickHouse and PostgreSQL migration paths.

For APDL 0.3.0, the Redis-to-ClickHouse writer and the PostgreSQL and ClickHouse
migrations used by the source-built single-node core are supported. Redis
Streams is the only event bus included in APDL.

## Layout

| Directory | 0.3.0 status | What it is |
|-----------|---|------------|
| `redis/` | Supported core | ClickHouse writer — consumes `events:raw:{project_id}` Redis Streams and batch-inserts into ClickHouse |
| `clickhouse/` | Supported core migrations | SQL migrations and reference schemas (tables + materialized views) |
| `postgres/` | Supported core migrations | The authoritative, versioned PostgreSQL migration sequence |

## ClickHouse writer

`redis/clickhouse_writer.py` is a single-file async consumer
(deps: `redis`, `clickhouse-driver`, `asyncpg`). The synchronous ClickHouse driver is
isolated in one dedicated worker thread, so inserts cannot block Redis reads,
pending claims, monitoring, or signal handling on the asyncio loop. It reads
from every `events:raw:*` stream — discovered via `SCAN`, or pinned with
`PROJECT_IDS` — using the `clickhouse-writer` consumer group (consumer name
`worker-{pid}`) and batch-inserts into the `events` table.

- **Single writer authority:** exactly one process may advance the
  `clickhouse-writer` group. Before reading Redis, startup takes a dedicated
  PostgreSQL session advisory lock and exits if another writer owns it. The
  same checked-out session is heartbeat-verified for the writer lifetime, and
  supported Compose declares one replica. This keeps the process-local
  completeness frontier equal to the group-wide frontier; horizontal writer
  scaling requires a shared frontier redesign.
- **Batching:** rotates fairly across streams and reads one tenant at a time so
  Redis's per-stream `COUNT` behavior cannot exceed the global 1000-event
  buffer (`BUFFER_SIZE`). It flushes when full or every 5 seconds
  (`FLUSH_INTERVAL`), whichever comes first.
- **Delivery:** at-least-once transport with idempotent storage. New consumer
  groups start at `0-0`, so a stream's
  existing backlog is consumed on first discovery. The writer periodically uses
  `XAUTOCLAIM` to take over stale Pending Entries List deliveries from prior
  consumers. Messages are atomically XACKed and XDEL'd only after ClickHouse or
  the DLQ accepts their rows. Durable finalization is isolated per stream:
  PostgreSQL verification/frontier waits are bounded, a blocked stream retains
  only its own IDs, and healthy streams continue to be read and finalized.
  This keeps `XLEN` equal to outstanding work so
  both event producers can enforce the shared 1,000,000-entry capacity without
  trimming accepted data. A crash between insert and ACK may replay an insert,
  but the stable client
  `message_id` and `(project_id, message_id)` replacement key make supported
  `FINAL` reads return that event exactly once. The canonical event tables
  partition by project, while retention dates derive from server-authoritative
  receipt time. Retries must preserve the complete logical event; reusing an ID
  for changed content has undefined winner semantics.
- **Tenant authority:** the project is derived only from a validated
  `events:raw:{project_id}` stream key. Conflicting project assertions inside a
  Redis message or its event JSON are rejected.
- **Validation and DLQ:** canonical ClickHouse row types are validated before
  buffering. Terminal parse/row rejects write safe metadata (never the event
  payload) to the bounded `events:dlq:{project_id}` Redis stream. The source is
  XACKed only after DLQ persistence; a DLQ failure leaves it in the PEL for
  later reclaim. A terminal row cannot hold valid rows or other tenants behind
  it.
- **Retries:** a failed ClickHouse flush remains buffered and stops further
  reads once the bounded buffer is full. Retries use capped exponential
  backoff shared by the consumer and periodic flusher; events are not dropped
  after an arbitrary retry count. Only narrow client-side row serialization
  errors are terminal—server/schema failures retain the batch.
- **Experiment boundaries:** marker publication selects at most one due marker
  per project per sweep. Each marker failure is isolated from later tenants and
  persists a server-time exponential-backoff deadline in PostgreSQL. Transient
  failures enter terminal quarantine on the fifth failed publication attempt;
  malformed markers quarantine immediately. Both paths persist a fixed safe
  failure code. Existing Redis dedup IDs are atomically checked against the
  exact stream entry and marker fields before reuse. The first valid observed
  stream ID is retained across retries; a quarantined observed delivery is
  ACKed only after its project completeness frontier is permanently degraded.
  A poisoned dedup ID already owned by another boundary is quarantined without
  stealing that ID, while genuine post-XADD observations remain mandatory.
  Redis insertion remains token-idempotent, and the original project,
  experiment, version, window, stream, token, and observed identity never
  changes. Startup holds the migration guards while proving the exact baseline
  ledger checksum, columns, canonical constraint definitions, and exact
  monotone/terminal trigger function before taking singleton writer authority.
- **Shutdown:** SIGINT/SIGTERM trigger a bounded final flush and stats log.
  Cancellation never marks a still-running synchronous insert as complete. If
  the shutdown deadline expires, the writer closes the native ClickHouse socket
  and leaves the Redis deliveries pending for replay instead of ACKing an
  unobserved insert result.

The deletion and completeness contract permits exactly one live consumer in
the one required `clickhouse-writer` group and no second durable consumer
group. Adding a consumer requires a shared completeness frontier; adding a
group also requires all-group acknowledgement tracking before entries can be
deleted. Redis must use non-evicting memory policy plus durable persistence;
the supported Compose stack uses AOF (`appendfsync everysec`) and
an explicit aggregate memory ceiling with `maxmemory-policy noeviction`. Route
`event_stream_pressure`,
`event_stream_overloaded`, `redis_memory_pressure`, and
`lost_or_deleted_pending` logs to alerts and monitor Redis memory/disk capacity.
Logging is only the checked-in signal; it does not become an operational alert
until the deployment routes it.

The boundary/watermark decision record, non-final reason mapping, and response
to pending, quarantined, or irreversibly degraded authority are documented in
[Experiment finality and delivery recovery](../docs/experiment-finality-and-delivery-recovery.md).

Before starting producers, verify that Redis's configured memory ceiling leaves
operating headroom. The writer safely trims only acknowledged history before
the earliest pending delivery; a stream with no consumer group cannot be
trimmed to manufacture headroom.

Environment variables: `REDIS_URL` (default `redis://localhost:6379`),
`POSTGRES_URL` (required; for local development use
`postgresql://apdl_runtime:apdl_runtime_dev@localhost:5432/apdl`; it provides
singleton-writer and migration-inhibitor authority, while the database owner is
migration-only),
`CLICKHOUSE_NATIVE_URL` (default
`clickhouse://apdl:apdl_dev@localhost:9000/apdl`), `BUFFER_SIZE`,
`FLUSH_INTERVAL`, `DLQ_MAXLEN` (default 10000 per project),
`PENDING_CLAIM_IDLE_MS` (default 60000),
`PENDING_CLAIM_INTERVAL_SECONDS` (default 30),
`CLICKHOUSE_CONNECT_TIMEOUT_SECONDS` (default 5),
`CLICKHOUSE_SEND_RECEIVE_TIMEOUT_SECONDS` (default 30),
`CLICKHOUSE_SYNC_REQUEST_TIMEOUT_SECONDS` (default 5),
`SHUTDOWN_TIMEOUT_SECONDS` (default 10), and `PROJECT_IDS` (optional
comma-separated allowlist). The writer owns the three native-driver timeout
query parameters and replaces conflicting values embedded in the URL.

## ClickHouse schema

`clickhouse/migrations/001_initial_schema.sql` is the canonical fresh-install
schema. It creates the final tables and projections directly; it contains no
shadow-table exchanges, legacy upgrades, data copies, or prototype retirement
steps. Future ClickHouse schema changes start at `002_*.sql`.

The migrator validates a contiguous `001...N` filename sequence, records each
file's exact SHA-256 and name in `apdl_schema_migrations`, and rejects missing,
reordered, renamed, or modified history. ClickHouse DDL remains restart-safe,
so baseline objects use `IF NOT EXISTS` in case a process stops after DDL
succeeds but before its ledger row is recorded.

The baseline creates these retained analytics tables:

- `events` — the canonical raw event stream, deduplicated by
  `(project_id, message_id)` using `ReplacingMergeTree(received_at)`.
- `sessions` — session rollups anchored to server receipt time.
- `experiment_event_deliveries` — stream-provenance projection used for
  experiment completeness.
- `feature_flag_exposures` — strict flag-evaluation projection.
- `frontend_health_events` — frontend error and web-vital projection.
- `identity_alias_assertions` — retained, append-only identify assertions.

Materialized views project those derived tables from `events`. The
`resolved_identity_aliases` view resolves an alias only when all retained
assertions agree; conflicting claims remain visible and unresolved. Every
identity-bearing table uses the same 12-month server-receipt retention boundary.
Supported Query Service reads use `FINAL` where retry deduplication is required.

`clickhouse/backfills/` is intentionally empty for the baseline. Future
retained-data transformations belong there rather than in replayable DDL. The
initializer records any future backfill's name and checksum in
`apdl_schema_backfills` and executes it exactly once under the maintenance
fence.

Every file in `clickhouse/migrations/` must be executable ClickHouse SQL. The
runner rejects PostgreSQL markers and removed prototype-schema operations.

## PostgreSQL schema

`postgres/migrations/001_initial_schema.sql` is the canonical fresh-install
schema. It creates the final tables, indexes, constraints, functions, triggers,
registry rows, and grants directly. Historical row repairs and compatibility
archives are intentionally absent. Future PostgreSQL changes start at
`002_*.sql`.

The runner applies the strict, contiguous sequence under an advisory lock. Each
migration and its immutable `apdl_schema_migrations` ledger entry commit in the
same transaction. Renaming, editing, deleting, or inserting an older migration
fails closed. An empty ledger beside existing public tables is rejected: this
release supports fresh databases and exact ledger prefixes, not adoption of an
unversioned schema.

The cluster bootstrap creates the fixed migration, runtime, and audited-operator
roles before the baseline runs. The baseline grants runtime access explicitly,
keeps audit-purge authority separated behind a constrained `SECURITY DEFINER`
function, and seeds the execution and analysis table registries before proving
their triggers are installed.

Config, Agents, Codegen, Query, the ClickHouse writer, and the analytics deletion
workflow all gate readiness on `001_initial_schema.sql` plus their required
columns, constraints, or engines. Applications never create or alter tables at
startup. Docker Compose gates PostgreSQL consumers on the one-shot
`postgres-migrate` service; use `make dev-core` or `make dev-all` so the
independently coordinated ClickHouse baseline is also applied.

## Running locally

```bash
make dev                 # start Redis, ClickHouse, PostgreSQL (Docker)
make migrate-clickhouse  # apply ClickHouse-only migrations
make migrate-postgres    # transactionally apply the PostgreSQL sequence
make run-pipeline        # start the ClickHouse writer
```

These commands operate on a fresh local development stack. Multi-replica
operation, in-place upgrades, backup, restore, Kubernetes, and Terraform are
outside the 0.3.0 support boundary.

## Tests

```bash
make test-writer            # pytest for the Redis ClickHouse writer
make lint-writer            # ruff for the writer and its tests
make test-database-baseline # fresh PostgreSQL/ClickHouse baseline and drift proof
```
