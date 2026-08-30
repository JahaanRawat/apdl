#!/usr/bin/env bash
# evaluate.sh -- Stage 3 of the APDL PR review: judge the judge.
#
# Runs an ISOLATED `claude -p` session over findings.json. That session has a
# fresh context: it sees the rubric and the findings, never the reasoning that
# produced them, so it cannot inherit the first pass's assumptions. It re-opens
# every cited file and returns keep / downgrade / drop per finding.
#
# Usage:
#   scripts/pr-review/evaluate.sh [REVIEW_CONTEXT_DIR]     # default: review-context
#
# Env:
#   PR_REVIEW_EVAL_MODEL=<model>   run the evaluator on a different model than
#                                  the judge (cheap way to buy real independence)
#   PR_REVIEW_EVAL_BUDGET_USD=<n>  spend cap for the evaluator session (default 5)
#
# Reads:  <dir>/findings.json
# Writes: <dir>/verdicts.json   (and <dir>/verdicts.raw on failure, for debugging)
#
# Auth: whatever the claude CLI is already logged in with.

set -euo pipefail

CTX="${1:-review-context}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUBRIC="$SCRIPT_DIR/evaluator-rubric.md"
BUDGET_USD="${PR_REVIEW_EVAL_BUDGET_USD:-5}"

[ -f "$CTX/findings.json" ] || {
  echo "error: $CTX/findings.json not found -- run the judge stage first" >&2
  exit 1
}
[ -f "$RUBRIC" ] || { echo "error: rubric not found at $RUBRIC" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || {
  echo "error: claude CLI not found -- fall back to self-evaluation and say so in the summary" >&2
  exit 1
}

# Nothing to evaluate is a valid, common outcome.
if [ "$(tr -d '[:space:]' < "$CTX/findings.json")" = "[]" ]; then
  echo "[]" > "$CTX/verdicts.json"
  echo "==> No findings to evaluate."
  exit 0
fi

echo "==> Evaluating findings in an isolated session..."

set -- -p \
  --allowedTools "Read,Grep,Glob,Bash(graphify query:*),Bash(graphify path:*),Bash(graphify explain:*),Bash(git log:*),Bash(git diff:*)" \
  --max-budget-usd "$BUDGET_USD"
[ -n "${PR_REVIEW_EVAL_MODEL:-}" ] && set -- "$@" --model "$PR_REVIEW_EVAL_MODEL"

claude "$@" "$(cat "$RUBRIC")

Here are the findings to evaluate:

$(cat "$CTX/findings.json")" < /dev/null > "$CTX/verdicts.raw" || {
  echo "error: evaluator session failed -- see $CTX/verdicts.raw" >&2
  exit 1
}

# The evaluator is told to return bare JSON, but models wrap it in prose or
# fences often enough that parsing has to be defensive. Validate hard: a verdict
# set that does not line up 1:1 with the findings is worse than no verdict set,
# because Stage 4 would post findings nobody actually checked.
python3 - "$CTX" <<'PY'
import json, sys, pathlib

ctx = pathlib.Path(sys.argv[1])
raw = (ctx / "verdicts.raw").read_text()

start, end = raw.find("["), raw.rfind("]")
if start == -1 or end == -1:
    sys.exit("error: no JSON array found in evaluator output -- see verdicts.raw")
try:
    verdicts = json.loads(raw[start:end + 1])
except json.JSONDecodeError as exc:
    sys.exit(f"error: evaluator output is not valid JSON ({exc}) -- see verdicts.raw")

findings = json.loads((ctx / "findings.json").read_text())
want = [f["id"] for f in findings]
got = [v.get("id") for v in verdicts]
if sorted(map(str, got)) != sorted(map(str, want)):
    sys.exit(f"error: verdict ids {got} do not match finding ids {want} -- see verdicts.raw")

allowed = {"keep", "downgrade", "drop"}
bad = [v for v in verdicts if v.get("verdict") not in allowed]
if bad:
    sys.exit(f"error: unrecognised verdict value(s): {bad} -- see verdicts.raw")

(ctx / "verdicts.json").write_text(json.dumps(verdicts, indent=2) + "\n")

counts = {k: sum(1 for v in verdicts if v["verdict"] == k) for k in ("keep", "downgrade", "drop")}
print(f"==> Verdicts: {counts['keep']} keep / {counts['downgrade']} downgrade / "
      f"{counts['drop']} drop  ->  {ctx / 'verdicts.json'}")
PY
