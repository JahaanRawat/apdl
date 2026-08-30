# PR review workflow — APDL

Canonical review workflow for the APDL monorepo. Follow this when asked to
review a PR, review a diff, review changes before pushing, or check whether a
branch is safe to merge. It is the canonical APDL review workflow for all coding
agents.

APDL is open source, so most review comments land on someone who does not have
the whole monorepo in their head — sometimes a first-time contributor. Two goals
follow from that:

- **Catch what a careful reader of one file cannot see.** Six services, two
  SDKs, and a pipeline talk to each other across HTTP hops, a Redis stream, and
  a migration ledger. The breakage that matters is usually two modules away from
  the line that changed.
- **Post nothing that has not survived independent verification.** A wrong
  comment costs a volunteer their evening and costs the project credibility. A
  missed nit costs almost nothing. When in doubt, stay quiet.

The workflow has four stages: a deterministic collector, a judge, an independent
evaluator that checks the judge, and a report containing only what survived.

---

## Stage 1 — Collect (deterministic, no LLM)

```bash
scripts/pr-review/collect.sh [BASE_REF]     # BASE_REF defaults to origin/main
```

Useful environment variables:

| Variable | Effect |
|---|---|
| `PR_REVIEW_MODE=worktree` | review uncommitted work instead of commits vs base |
| `PR_REVIEW_SKIP_GRAPH=1` | reuse `graphify-out/` as-is (fast; may be stale) |
| `PR_REVIEW_MAX_SYMBOLS=N` | cap blast-radius queries (default 60) |
| `PR_REVIEW_QUERY_BUDGET=N` | per-symbol graphify token budget (default 1200) |

The script refreshes the Graphify knowledge graph — which is generated, never
committed, so a fresh clone has nothing to query until this runs — and writes
`review-context/`:

| File | What it holds |
|---|---|
| `MODE` | what was diffed, against what |
| `diff.patch`, `diff.stat` | the change set |
| `changed_files.txt` | paths touched |
| `changed_symbols.txt` | definitions touched — heuristic, **not exhaustive**, production symbols ranked ahead of `test_*` |
| `blast/<symbol>.txt` | `graphify query` output per symbol the graph knows |
| `GRAPH_REPORT.md` | module structure and god nodes |
| `tripwires.txt` | APDL invariants this diff steps on |
| `SKIPPED` | anything the stage could not do — **read it**, there are no silent gaps |

If it writes `review-context/EMPTY`, stop and report that there is nothing to
review. If it writes `SKIPPED`, carry those caveats into your final summary; a
review run without a graph is a weaker review and should say so.

## Stage 2 — Judge (you)

Read `review-context/` in this order: `MODE`, then `tripwires.txt`, then
`GRAPH_REPORT.md`, then `diff.patch`, then the `blast/` files. For any changed
symbol without a blast file, run `graphify query "<symbol>"` yourself. Use
`graphify path "<A>" "<B>"` to trace a suspicious connection into a module the
diff never touched.

Priorities, in order:

1. **Correctness across module boundaries.** Callers broken by a signature,
   behavior, or contract change. This is the whole point of the graph — spend
   your effort here.
2. **Breaking API, schema, or wire-format changes.** Published SDK surface, HTTP
   routes, event envelopes, migration sequence.
3. **The tripwires in `tripwires.txt`,** and the domains in
   `docs/agent-workflows/secure-coding.md` that the diff actually touches.
4. **Violations of the conventions in `CLAUDE.md`.**
5. **Bugs local to the diff.**

Skip anything CI already catches: `ruff` (E4/E7/E9/F only — import order, line
length, and docstring style are deliberately not enforced), `tsc --noEmit` in
strict mode, and the per-service pytest suites.

### What the graph will not tell you

The graph is built `--code-only` from the AST. It does not see:

- **HTTP hops between services.** The Admin Console → Admin API → core service
  path, and every SDK → Ingestion/Config call, are invisible. Before concluding
  "nothing calls this route", grep for the path string and the client method.
- **SQL migrations and `docs/`.** Not indexed. Read them directly.
- **Correctness of `INFERRED` edges.** Every edge is tagged `EXTRACTED`
  (explicit in source) or `INFERRED` (heuristic). Open the file before relying
  on an `INFERRED` edge.

If a query returns a symbol that no longer exists, the graph is stale: run
`graphify update .` and retry before concluding the code is wrong.

### Write the findings

Write `review-context/findings.json`. Use `[]` if you found nothing — that is a
perfectly good outcome and far better than padding.

```json
[
  {
    "id": "F1",
    "file": "services/query/app/routers/funnels.py",
    "line": 42,
    "severity": "high",
    "claim": "one-sentence problem statement",
    "why": "impact, naming the affected callers or modules — readable by someone new to the repo",
    "evidence": ["services/agents/app/graphs/insight.py:88", "blast/run_funnel.txt: <the relevant line>"],
    "fix": "concrete suggested fix, naming what to change"
  }
]
```

Every finding must cite at least one thing you actually opened. A claim resting
only on a graph edge, with no confirmation from the source file, does not go in
the file — verify it or drop it.

## Stage 3 — Evaluate (independent judge-of-the-judge)

```bash
scripts/pr-review/evaluate.sh
```

This starts an isolated `claude -p` session with a fresh context: it sees
`scripts/pr-review/evaluator-rubric.md` and your findings, never your reasoning,
so it cannot inherit your assumptions. It re-opens every cited file and writes
`review-context/verdicts.json` with keep / downgrade / drop per finding, and
fails loudly rather than emitting a verdict set that does not line up 1:1 with
the findings.

Set `PR_REVIEW_EVAL_MODEL` to run the evaluator on a different model than the
judge — a cheap way to buy real independence. `PR_REVIEW_EVAL_BUDGET_USD` caps
the spend (default 5).

If the script fails — no `claude` CLI, invalid JSON — fall back to
self-evaluation: re-open every cited file yourself, apply the same rubric, and
record verdicts in the same format. **Say in your summary that evaluation was
not independent.**

## Stage 4 — Report only what survived

Apply the verdicts. Dropped findings are never posted. Downgraded findings use
the evaluator's severity and carry its note.

- **Locally:** print the surviving findings as a numbered list ordered by
  severity, each with a `file:line` reference, followed by the counts
  (N findings: X kept, Y downgraded, Z dropped) and anything from `SKIPPED`.
- **On a PR,** when an inline-comment tool is available: one inline comment per
  surviving finding — claim, why (naming the affected callers), fix — then a
  short summary comment with the same counts.

Never post a dropped finding "just in case". Never summarize the PR
file-by-file; the diff already does that. Never comment on code the PR did not
change, unless the PR broke it.

---

## Principles

- The counts are part of the report. "6 findings, 2 kept" tells a maintainer
  more about the review than six comments do.
- Finding nothing is a valid result. Do not manufacture findings to look useful.
- Severity is a promise. `high` means a named broken caller, a real data-loss or
  security path, or a break in a published contract — not a strong hunch.
- Review the change, not the author's style. If the repo's linters allow it, it
  is allowed.
