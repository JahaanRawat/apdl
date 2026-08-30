#!/usr/bin/env bash
# collect.sh -- Stage 1 of the APDL PR review. Deterministic: no LLM, no network
# beyond `git fetch`.
#
# Refreshes the Graphify knowledge graph and assembles everything a reviewing
# agent needs about the change set into ./review-context/.
#
# Usage:
#   scripts/pr-review/collect.sh [BASE_REF]      # default BASE_REF: origin/main
#
# Env:
#   PR_REVIEW_MODE=worktree    review uncommitted work instead of commits vs base
#   PR_REVIEW_SKIP_GRAPH=1     reuse graphify-out/ as-is (skip re-extraction)
#   PR_REVIEW_MAX_SYMBOLS=N    cap blast-radius queries (default 60)
#   PR_REVIEW_QUERY_BUDGET=N   per-symbol graphify token budget (default 1200)
#
# Writes review-context/:
#   MODE                 what was diffed, against what
#   diff.patch           the change set
#   diff.stat            file-level summary
#   changed_files.txt    paths touched
#   changed_symbols.txt  definitions touched (heuristic -- NOT exhaustive)
#   blast/<symbol>.txt   graphify query output, per symbol the graph knows
#   GRAPH_REPORT.md      module structure + god nodes
#   tripwires.txt        APDL invariants this diff steps on
#   SKIPPED              anything this stage could not do (read it -- no silent gaps)

set -euo pipefail

BASE_REF="${1:-origin/main}"
OUT="review-context"
MAX_SYMBOLS="${PR_REVIEW_MAX_SYMBOLS:-60}"
QUERY_BUDGET="${PR_REVIEW_QUERY_BUDGET:-1200}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

rm -rf "$OUT"
mkdir -p "$OUT/blast"
: > "$OUT/SKIPPED"

note_skip() { echo "$1" >> "$OUT/SKIPPED"; echo "    (skipped) $1"; }

# ---------------------------------------------------------------- graph ------
# The graph is generated, never committed (graphify-out/ is gitignored), so a
# fresh clone has nothing to query until this runs.
if ! command -v graphify >/dev/null 2>&1; then
  note_skip "graphify not on PATH -- no blast radius, no GRAPH_REPORT.md. Install: uv tool install 'graphifyy[sql]'"
elif [ -n "${PR_REVIEW_SKIP_GRAPH:-}" ]; then
  note_skip "PR_REVIEW_SKIP_GRAPH set -- graph reused as-is, may be stale"
elif [ -f graphify-out/graph.json ]; then
  echo "==> Refreshing knowledge graph (incremental, AST-only)..."
  graphify update . || note_skip "graphify update failed -- blast radius computed from a stale graph"
else
  echo "==> Building knowledge graph (first run, local AST parse, ~1min)..."
  # --no-cluster/--no-label keep this stage LLM-free; communities stay unnamed
  # placeholders. Run `graphify cluster-only . --backend=claude-cli` by hand for
  # named communities in GRAPH_REPORT.md.
  graphify extract . --code-only --no-cluster \
    && graphify cluster-only . --no-label --no-viz \
    || note_skip "graph build failed -- no blast radius available"
fi

# ----------------------------------------------------------------- diff ------
MODE=""
DIFF_SPEC=""
if [ "${PR_REVIEW_MODE:-}" = "worktree" ]; then
  MODE="uncommitted working tree (staged + unstaged) vs HEAD"
  DIFF_SPEC="HEAD"
else
  git fetch --quiet origin "${BASE_REF#origin/}" 2>/dev/null || true
  if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
    echo "error: base ref '$BASE_REF' not found" >&2
    exit 1
  fi
  MODE="commits on HEAD since merge-base with $BASE_REF"
  DIFF_SPEC="$BASE_REF...HEAD"

  # A branch with no commits yet is almost always someone asking for a review
  # before they push. Fall back rather than reporting "nothing to review".
  if [ -z "$(git diff --name-only "$DIFF_SPEC")" ] && [ -n "$(git status --porcelain)" ]; then
    MODE="no commits vs $BASE_REF; fell back to uncommitted working tree vs HEAD"
    DIFF_SPEC="HEAD"
  fi
fi

git diff "$DIFF_SPEC" > "$OUT/diff.patch"
git diff --stat "$DIFF_SPEC" > "$OUT/diff.stat"
echo "$MODE" > "$OUT/MODE"
echo "==> Reviewing: $MODE"

if [ ! -s "$OUT/diff.patch" ]; then
  echo "nothing to review: $MODE produced an empty diff" > "$OUT/EMPTY"
  echo "==> Nothing to review."
  exit 0
fi

git diff --name-only "$DIFF_SPEC" | sort -u > "$OUT/changed_files.txt"

# -------------------------------------------------------------- symbols ------
# Two heuristics, unioned: the enclosing symbol git puts after the second @@,
# and definition lines the diff added or removed. Neither is exhaustive -- the
# judge is told to add symbols it spots that these missed.
{
  grep -E '^@@' "$OUT/diff.patch" \
    | sed -E 's/^@@[^@]*@@ ?//' \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?(def|class|function|interface|type|enum|const|let|var|struct|impl)[[:space:]]+//' \
    | grep -oE '^[A-Za-z_][A-Za-z0-9_]*' || true

  grep -E '^[+-]' "$OUT/diff.patch" \
    | sed -E 's/^[+-][[:space:]]*//' \
    | grep -E '^(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?(def|class|function|interface|type|enum|const|let|var)[[:space:]]+[A-Za-z_]' \
    | sed -E 's/^(export[[:space:]]+)?(default[[:space:]]+)?(async[[:space:]]+)?(def|class|function|interface|type|enum|const|let|var)[[:space:]]+//' \
    | grep -oE '^[A-Za-z_][A-Za-z0-9_]*' || true
} | sort -u | grep -vxE 'if|else|elif|for|while|return|import|from|try|except|finally|with|pass|raise|await|self|cls|None|True|False|null|undefined|new|this|super|and|or|not|in|is|as|do|switch|case|break|continue|default|export|async|await|public|private|protected|static|void|int|str|bool|float|dict|list|set|tuple|Any' \
  > "$OUT/changed_symbols.raw" || true

# Rank production symbols first: a test function has no callers, so tracing its
# blast radius wastes the query cap that public API changes need.
{
  grep -vE '^(test_|_)' "$OUT/changed_symbols.raw" || true
  grep -E '^_' "$OUT/changed_symbols.raw" | grep -vE '^_test' || true
  grep -E '^test_' "$OUT/changed_symbols.raw" || true
} > "$OUT/changed_symbols.txt"
rm -f "$OUT/changed_symbols.raw"

SYM_TOTAL=$(wc -l < "$OUT/changed_symbols.txt" | tr -d ' ')

# ------------------------------------------------------------ blast radius ---
if [ -f graphify-out/graph.json ] && command -v graphify >/dev/null 2>&1; then
  if [ "$SYM_TOTAL" -gt "$MAX_SYMBOLS" ]; then
    note_skip "$SYM_TOTAL symbols changed; blast radius traced for the first $MAX_SYMBOLS only (raise PR_REVIEW_MAX_SYMBOLS, or query the rest by hand)"
  fi
  echo "==> Tracing blast radius for up to $MAX_SYMBOLS of $SYM_TOTAL symbols..."
  COUNT=0
  while IFS= read -r sym; do
    [ -z "$sym" ] && continue
    COUNT=$((COUNT + 1))
    [ "$COUNT" -gt "$MAX_SYMBOLS" ] && break
    graphify query "$sym" --budget "$QUERY_BUDGET" > "$OUT/blast/${sym}.txt" 2>/dev/null || true
    # graphify exits 0 when it finds nothing; drop those so the judge only sees
    # symbols the graph actually knows about.
    if [ -s "$OUT/blast/${sym}.txt" ]; then
      if grep -q "No unique node match\|0 nodes found" "$OUT/blast/${sym}.txt"; then
        rm -f "$OUT/blast/${sym}.txt"
      fi
    else
      rm -f "$OUT/blast/${sym}.txt"
    fi
  done < "$OUT/changed_symbols.txt"
  cp -f graphify-out/GRAPH_REPORT.md "$OUT/GRAPH_REPORT.md" 2>/dev/null \
    || note_skip "graphify-out/GRAPH_REPORT.md missing -- no module map"
else
  note_skip "no graph.json -- blast radius not traced for any of the $SYM_TOTAL changed symbols"
fi

# ----------------------------------------------------------- APDL tripwires --
# Path-based and deterministic. These are the invariants that cross module
# boundaries in ways a reviewer reading one file at a time cannot see. Grouped
# by category so a 40-file migration diff yields one warning, not forty.
: > "$OUT/tripwires.txt"

emit_trip() {
  # emit_trip <pattern> <headline> <body>
  matched=$(grep -E "$1" "$OUT/changed_files.txt" || true)
  [ -z "$matched" ] && return 0
  n=$(printf '%s\n' "$matched" | wc -l | tr -d ' ')
  {
    echo "## $2 ($n file(s))"
    echo "$3"
    printf '%s\n' "$matched" | head -8 | sed 's/^/    /'
    [ "$n" -gt 8 ] && echo "    ... and $((n - 8)) more"
    echo
  } >> "$OUT/tripwires.txt"
}

emit_trip '^(sdk/javascript/src/flags/hash\.ts|sdk/python/apdl/flags/hash\.py|services/config/app/flags/evaluator\.py)$' \
  "BUCKETING PARITY" \
  "Three byte-identical FNV-1a implementations (JS SDK, Python SDK, Config Service) decide which variant a user sees. A change to one that does not land in all three silently buckets the same user differently per client. Verify against sdk/python/tests/test_parity.py, services/admin/__tests__/core/evaluator/parity.test.ts, and fixtures/gates/parity.json."

emit_trip '^pipeline/.*/migrations/.*\.sql$' \
  "MIGRATION LEDGER" \
  "These files are an immutable, checksum-verified sequence (pipeline/postgres/migrate.py). Editing or renumbering one that has already been applied breaks every deployed environment; new schema work needs a new numbered file. If this diff consolidates or removes migrations, confirm the ledger and baseline handle databases that already applied the old sequence."

emit_trip '^\.github/workflows/' \
  "ACTION PINS" \
  "Every action must be pinned to a full commit SHA -- the workflow-policy CI job (scripts/check_github_action_pins.py) fails the build otherwise."

emit_trip '^release-manifest\.json$' \
  "RELEASE GATE" \
  "release-manifest.json must match the release tag; see .github/workflows/release.yml."

emit_trip '^services/admin-api/app/' \
  "CONSOLE GATEWAY" \
  "The only path from the browser console to the core services. Hashed sessions, CSRF/origin enforcement, project-scoped credential selection, and proxy audit records are load-bearing -- see docs/agent-workflows/secure-coding.md sections 1, 2 and 11."

emit_trip '^services/[^/]+/app/routers/' \
  "HTTP CONTRACT" \
  "Cross-service boundaries. The graph is built from AST only, so HTTP hops are invisible: callers in other services and in the SDKs will NOT show up in blast radius. Grep for the route path and the client method name before concluding nothing calls it."

emit_trip '^sdk/(javascript/src|python/apdl)/' \
  "PUBLIC SDK SURFACE" \
  "This ships to users. Signature, default, or wire-format changes are breaking for installed clients that will not upgrade in lockstep with the services."

emit_trip '^ruff\.toml$' \
  "LINT CONFIG" \
  "ruff.toml is resolved by every Python package via ancestor lookup, and ruff is pinned to an exact version per package. Bump the pin and the config together, never rely on a >= floor."

[ -s "$OUT/tripwires.txt" ] || echo "(none)" > "$OUT/tripwires.txt"

# ---------------------------------------------------------------- report -----
[ -s "$OUT/SKIPPED" ] || rm -f "$OUT/SKIPPED"

echo "==> Collection complete:"
echo "    $(wc -l < "$OUT/changed_files.txt" | tr -d ' ') files, $SYM_TOTAL symbols, $(find "$OUT/blast" -type f | wc -l | tr -d ' ') blast files, $(grep -c '^## ' "$OUT/tripwires.txt" 2>/dev/null || echo 0) tripwire categor$([ "$(grep -c '^## ' "$OUT/tripwires.txt" 2>/dev/null || echo 0)" = "1" ] && echo y || echo ies)"
find "$OUT" -type f | sed 's/^/    /'
