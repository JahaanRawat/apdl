# Experiment finality and delivery recovery

- **Status:** Accepted for the APDL 0.3.0 single-node developer preview
- **Decision owners:** Query, Config, and data-pipeline maintainers
**Schema authority:** PostgreSQL migrations `033`, `036`, `038`, `041`, and
`044`; ClickHouse migration `014`

This document is both the design record for experiment data completeness and
the operator runbook for its two adjacent durable-delivery systems. It does not
turn statistical evidence into deployment authorization:
`deployment_readiness` remains `not_assessed`.

## Decision

An elapsed settlement timer is not proof that an experiment's data is
complete. Query may return `analysis_status: decision_snapshot` and
`data_completeness: verified` only when all of these conditions hold:

1. Config reports an authoritative completed experiment, immutable analysis
   window, enrollment mode, minimum exposure version, variants, and statistical
   plan.
2. The settlement hold has elapsed, every declared arm meets its planned
   sample size, statistics are finite, and no unknown variant or ambiguous
   identity contaminates the population.
3. PostgreSQL contains one immutable boundary identity for the exact
   project, experiment key, config version, and analysis window.
4. The single authoritative writer has published that boundary marker into the
   project's Redis event stream, durably projected all deliveries through the
   marker into ClickHouse, and advanced a contiguous PostgreSQL watermark only
   after exact Redis finalization.
5. ClickHouse provenance shows that every analyzed exposure, metric event, and
   identity assertion is inside the covered delivery interval.
6. PostgreSQL accepts one canonical, SHA-256-bound immutable snapshot for that
   exact boundary.

The supported topology is exactly one `clickhouse-writer` process and one
`clickhouse-writer` consumer group. A second consumer can observe delivery
progress the process-local frontier did not finalize, so horizontal writer
scaling requires a different shared-frontier design.

Watermark degradation is deliberately irreversible. A dead-lettered event,
lost pending entry, unverifiable stream reset, or provenance gap means APDL cannot
prove what should have been analyzed. Resetting a flag in place would turn
unknown data loss into a false verified claim. Boundary publication has a
separate monotone `pending` → `published | quarantined` state machine; failed
tenants cannot starve later projects.

## Finality states and response

| Query reason | Meaning | Operator response |
|---|---|---|
| `awaiting_data_settlement` | The authored post-window hold has not elapsed | Wait; do not shorten the hold retrospectively |
| `awaiting_pipeline_boundary` | The marker is pending publication or the contiguous watermark has not covered it | Inspect writer, Redis, PostgreSQL, and ClickHouse health; repair the dependency and let normal retry continue |
| `pipeline_boundary_failed` | Marker publication reached terminal quarantine | Preserve the row and evidence; the affected experiment/version cannot become verified in place |
| `pipeline_degraded` | Project watermark recorded a known delivery-integrity failure | Treat the project completeness epoch as permanently unverified |
| `pipeline_provenance_unavailable` | ClickHouse rows cannot be tied to the covered stream interval | Do not publish a decision snapshot; retain the non-final response |
| `data_completeness_unverified` | The authority or immutable snapshot could not be persisted/verified | Repair PostgreSQL/schema availability and retry the read; never synthesize a snapshot |
| `unknown_variant_exposures` / `identity_alias_conflicts` | The analyzed population is not canonical | Investigate the source data; these are not queue-retry states |
| `underpowered_arms` / `non_finite_statistics` | Statistical preconditions are not met | Keep the result non-final; pipeline recovery cannot fix the statistical contract |

### Inspect a stuck boundary

Use a read-only PostgreSQL session first:

```sql
SELECT project_id, experiment_key, config_version,
       marker_publish_state, marker_publish_attempts,
       marker_publish_failure_code, marker_publish_next_attempt_at,
       marker_publish_observed_stream_id, marker_stream_id,
       requested_at, marked_at
FROM experiment_analysis_boundaries
WHERE project_id = 'demo'
  AND experiment_key = 'checkout-test'
ORDER BY config_version DESC;

SELECT project_id, stream_key, provenance_start_stream_id,
       contiguous_stream_id, consumer_group_entries_read,
       status, failure_reason, updated_at
FROM event_pipeline_watermarks
WHERE project_id = 'demo';
```

For a pending marker, correlate the row with writer logs, PostgreSQL and
ClickHouse readiness, Redis memory/disk pressure, and
`XINFO GROUPS events:raw:demo`. Fix the underlying dependency and restart only
the singleton writer if needed. Transient publication retries are automatic
and bounded.

Do not manually update a quarantined boundary or degraded watermark. There is
no supported endpoint or SQL reset because the missing-history claim cannot be
reconstructed from the flag itself. Recovery requires either a verified
restore of the entire event pipeline and its authority from before the loss,
or a new project/data epoch after the root cause is fixed. Preserve the old
experiment as non-final evidence.

## Config outbox recovery

Config's `/ready` response keeps database/cache dependency readiness separate
from delivery degradation. When PostgreSQL and Redis are usable, outbox lag or
quarantine remains visible in the nested `outbox.status: degraded` payload but
does not itself return 503 and trigger a restart loop. Defaults are:

- pending-age degradation after 300 seconds;
- degradation after any quarantined row;
- processed-row retention of 7 days;
- quarantined-row retention of 90 days; and
- exposure-receipt retention of 400 days, longer than the maximum analytics
  retention boundary.

Set `CONFIG_OUTBOX_DEGRADED_MAX_PENDING_AGE_SECONDS` and
`CONFIG_OUTBOX_DEGRADED_MAX_QUARANTINED_ROWS` to explicit non-negative values
when a deployment needs different alert thresholds. These values change
degradation reporting, not delivery semantics.

Readiness reads oldest-row ages through partial indexes, reports PostgreSQL
statistics as explicitly named estimated counts, and checks the quarantine
threshold by reading at most the configured limit plus one row. It does not
run an unbounded exact outbox count on every probe.

The Config operator function provides project-scoped, keyset-paginated
recovery endpoints. They require an authenticated credential for the same
project with `config:write`.

Inspect terminal rows:

```bash
curl -sS "$CONFIG_URL/v1/admin/outbox/quarantine?limit=50" \
  -H "x-api-key: $PROJECT_API_KEY"
```

If `next_before_id` is non-null, request the next page with
`before_id=<next_before_id>`. The response deliberately includes the
quarantined payload so access and logs around this endpoint must be treated as
sensitive.

After correcting the recorded dependency or contract failure, replay one
quarantined **exposure** row:

```bash
curl -sS -X POST \
  "$CONFIG_URL/v1/admin/outbox/quarantine/123/replay" \
  -H "x-api-key: $PROJECT_API_KEY" \
  -H "content-type: application/json" \
  --data '{"reason":"dependency repaired under incident INC-123"}'
```

Exposure replay resets attempts and terminal failure fields, makes the row
immediately available, and preserves a payload digest plus actor/reason in
`config_outbox_operator_log`. Confirm downstream convergence before replaying
another row.

`flag_change` and `experiment_change` rows cannot be replayed by this endpoint:
their historical `project_version` may be lower than a later delivered row, so
replay could violate monotone SSE delivery. Such a request returns
`409 unsafe_outbox_replay` before writing operator evidence or changing the
row. Reconcile clients and caches from the current authoritative snapshot,
record that incident evidence, and then use the discard path for the obsolete
intent.

Discard only when the operator explicitly accepts that the delivery must never
occur and has a separate convergence or incident plan:

```bash
curl -sS -X POST \
  "$CONFIG_URL/v1/admin/outbox/quarantine/123/discard" \
  -H "x-api-key: $PROJECT_API_KEY" \
  -H "content-type: application/json" \
  --data '{"reason":"obsolete intent superseded by reviewed snapshot INC-124"}'
```

Discard deletes the quarantined payload row. The immutable operator log retains
project, outbox ID, action, actor, reason, kind, failure code/class, and payload
SHA-256, but not a duplicate payload. For exposure intents, the separate
400-day receipt ledger remains the `message_id` idempotency/conflict authority.
Never discard merely to make the degraded metric green.

The PostgreSQL baseline keeps durable exposure conflict authority in
`config_exposure_receipts`, independently of prunable outbox rows. The receipt,
quarantine, and operator-log objects are tested as one final delivery contract.

## Privacy purge for experiment change snapshots

`experiment_audit_log` stores full before/after Config snapshots and has no
automatic expiry. The PostgreSQL baseline adds the database-operator-only function
`public.apdl_purge_experiment_audit(...)`. `PUBLIC` and `apdl_runtime` have no
execute privilege. The fresh-cluster bootstrap creates a `NOLOGIN`
`apdl_audit_operator` caller role and a separate, ungranted `NOLOGIN`
`apdl_audit_purge_definer` function-owner role. Grant only the caller role to a
named maintenance/privacy login through the deployment's reviewed access
workflow; never grant the definer role.

The purge is destructive and has no application-level undo. Confirm the
project, legal cutoff, backup policy, login identity, and ticket before
starting. The narrow `SECURITY DEFINER` function deletes only the selected
project/cutoff and writes purge evidence in the same transaction. Normal
callers retain no direct delete authority, and the immutable audit trigger
allows the delete only while `current_user` is the ungranted definer role.
There is no trigger-disable interval. The function takes a
`SHARE ROW EXCLUSIVE` lock, which retains ordinary reads but serializes
concurrent ledger writers; schedule a maintenance window for a large purge.

Preview the exact project and exclusive cutoff:

```sql
SELECT count(*) AS rows_to_delete,
       min(created_at) AS oldest,
       max(created_at) AS newest
FROM experiment_audit_log
WHERE project_id = 'demo'
  AND created_at < TIMESTAMPTZ '2025-07-01T00:00:00Z';
```

Execute with the exact confirmation phrase:

```sql
BEGIN;
SELECT public.apdl_purge_experiment_audit(
    'demo',
    TIMESTAMPTZ '2025-07-01T00:00:00Z',
    'approved retention request PRIV-123',
    'PURGE EXPERIMENT AUDIT'
);
COMMIT;
```

The recorded `actor` is derived from PostgreSQL `session_user`, not from
caller-supplied text. Connect with the named operator login rather than the
migration owner or a shared account. Keep the reason to a reviewed ticket or
policy reference; do not put raw user identifiers, event/config payloads,
secrets, email addresses, or other unnecessary personal data in this
non-expiring evidence.

Then verify both the remaining source rows and immutable purge evidence:

```sql
SELECT count(*)
FROM experiment_audit_log
WHERE project_id = 'demo'
  AND created_at < TIMESTAMPTZ '2025-07-01T00:00:00Z';

SELECT project_id, purge_before, deleted_rows, actor, reason, created_at
FROM experiment_audit_purge_log
WHERE project_id = 'demo'
ORDER BY id DESC
LIMIT 10;
```

The function is project-scoped and deletes only Config experiment change
history older than the exclusive cutoff. It does not delete behavior events,
derived analytics, exposure receipt authority, or immutable Query decision
snapshots. Use the separate
[analytics data deletion workflow](data-retention.md) for project/user
analytics. If a policy also requires removal of verified decision snapshots,
define a separate audited contract rather than extending this function
implicitly.

## Verification and change control

The release gate must exercise:

- fresh PostgreSQL migrations through `044` and ClickHouse migrations through
  `016`;
- the Config outbox quarantine/replay/discard and immutable operator-log tests;
- writer marker fairness, retry, singleton, provenance, and watermark tests;
- Query non-final reason precedence, exact boundary coverage, and immutable
  snapshot tests; and
- fresh experiment smoke against the final cumulative schema.

Applied migration files are checksum-bound history. Corrective changes use the
next numbered migration; they do not edit, move, or reorder `038`, `041`, or
`044`.
