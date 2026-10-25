#!/usr/bin/env bash
# Week 10 hardening evidence. Writes terminal output to evidence/week10/.
# Uses its own mock on 127.0.0.1:8766 and its own state file, and resets that
# state between independent runs (docs/API.md). It never approves a review:
# it stops at the human step and prints the approve command.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=${MOCK_PORT:-8766}
URL="http://127.0.0.1:${PORT}"
STATE=${MOCK_STATE:-.state/week10.sqlite}
WORK=.state/week10
OUT=evidence/week10
export MOCK_PORT=$PORT MOCK_STATE=$STATE LLM_PROVIDER=null FEATURE_LLM_REVIEW=off
PY="uv run --locked python"
CLI="uv run --locked dispute-copilot --mock-url $URL"
MOCK_PID=""

mkdir -p "$OUT" "$WORK"
rm -rf "$OUT"/*.txt "$OUT"/traces "$OUT"/breaker_run "$WORK"/*

say() { printf '\n$ %s\n' "$*"; }
run() { say "$*"; "$@"; echo "[exit $?]"; }

start_mock() {
  $PY tools/runtime.py serve --data data/dev --state "$STATE" --port "$PORT" >"$WORK/mock.log" 2>&1 &
  MOCK_PID=$!
  for _ in $(seq 1 50); do curl -sf "$URL/health" >/dev/null && return 0; sleep 0.1; done
  echo "mock did not start"; cat "$WORK/mock.log"; exit 1
}
stop_mock() { [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null && wait "$MOCK_PID" 2>/dev/null; MOCK_PID=""; }
fresh_mock() { stop_mock; $PY tools/runtime.py reset --confirm --state "$STATE" >/dev/null; start_mock; }
trap stop_mock EXIT
trace() { $PY scripts/trace_view.py "$@"; }
header() { echo "# $1"; echo "# $(date '+%Y-%m-%d %H:%M:%S %Z')  commit $(git rev-parse --short HEAD)  branch $(git branch --show-current)"; }

# 1. Checks
{
  header "Package, lint gates and tests"
  run $PY tools/validate_package.py
  run $PY tools/lint_gates.py
  run uv run --locked pytest -q
  run uv run --locked pytest -v tests/unit/test_reliability.py tests/unit/test_security.py tests/unit/test_observability.py
} 2>&1 | tee "$OUT/01_checks.txt"

# 2. Simulated timeout: real client against a test double that hangs, then the supplied mock's own faults
fresh_mock
{
  header "Retry with backoff: recovery from a simulated timeout"
  echo "Policy file:"; cat config/rules/retry.yaml
  run $PY scripts/fault_demo.py timeout
  echo
  echo "Supplied mock faults (prompt 503 upstream_timeout). CARD-D10 is transient, CARD-D07 is persistent."
  for c in CARD-D10 CARD-D07; do
    run env SUBMISSIONS_DIR="$WORK/sub-timeout" REPORTS_DIR="$WORK/rep-timeout" $CLI run "$c"
    trace "$WORK/rep-timeout/traces/$c.jsonl" | grep -E "tool\.|retry\.|degradation|rules.routing"
  done
} 2>&1 | tee "$OUT/02_timeout_recovery.txt"

# 3. Circuit breaker: test double, then the supplied mock with a low threshold
fresh_mock
{
  header "Cross-case circuit breaker"
  echo "Breaker file:"; cat config/rules/breaker.yaml
  run $PY scripts/fault_demo.py breaker
  echo
  echo "Supplied mock, BREAKER_FAILURE_THRESHOLD=1 BREAKER_COOLDOWN_S=60 (demo setting, not the default)."
  echo "CARD-D07 exhausts its 3 attempts; the transactions breaker opens; later cases skip the call and degrade."
  say "BREAKER_FAILURE_THRESHOLD=1 BREAKER_COOLDOWN_S=60 dispute-copilot run-all"
  env BREAKER_FAILURE_THRESHOLD=1 BREAKER_COOLDOWN_S=60 SUBMISSIONS_DIR="$OUT/breaker_run/submissions" \
    REPORTS_DIR="$OUT/breaker_run/reports" $CLI run-all | $PY scripts/week10_summary.py run
  for c in CARD-D07 CARD-D08; do
    trace "$OUT/breaker_run/reports/traces/$c.jsonl" | grep -E "breaker|tool.call|degradation"
  done
} 2>&1 | tee "$OUT/03_breaker.txt"

# 4. Default run: three scenarios, evaluation, retry counts
fresh_mock
REP="$OUT/reports"
SUB="$WORK/submissions"
rm -rf "$REP"
{
  header "Default settings: full run, evaluation, and the three required scenarios"
  say "dispute-copilot run-all"
  env SUBMISSIONS_DIR="$SUB" REPORTS_DIR="$REP" $CLI run-all | $PY scripts/week10_summary.py run
  say "python tools/evaluate.py --data data/dev --gold feedback/dev_gold.json --submission $SUB/results.json --report $WORK/dev.json"
  $PY tools/evaluate.py --data data/dev --gold feedback/dev_gold.json --submission "$SUB/results.json" --report "$WORK/dev.json" >/dev/null
  echo "[exit $?]"
  $PY scripts/week10_summary.py eval "$WORK/dev.json"
  echo
  echo "Mock audit: tool attempts per case and tool (procedure: D07 transactions 3, D10 2)"
  $PY tools/runtime.py audit --state "$STATE" | $PY scripts/week10_summary.py audit
  echo
  echo "== Scenario 1: normal case CARD-D01 (unauthorized, ready_for_review) =="
  trace "$REP/traces/CARD-D01.jsonl"
  echo
  echo "== Scenario 2: ambiguous case CARD-D05 (conflicting statements, escalated to manual_review) =="
  trace "$REP/traces/CARD-D05.jsonl"
  echo
  echo "== Scenario 3a: failure case CARD-D07 (transactions outage, degraded to manual_review) =="
  trace "$REP/traces/CARD-D07.jsonl"
  echo
  echo "== Scenario 3b: attack case CARD-D08 (prompt injection in merchant metadata) =="
  trace "$REP/traces/CARD-D08.jsonl"
} 2>&1 | tee "$OUT/04_scenarios.txt"

# 5. Prompt injection
{
  header "Prompt injection in merchant metadata (CARD-D08)"
  say "curl -s $URL/v1/cases/CARD-D08/merchant   (private_token value masked here)"
  curl -s "$URL/v1/cases/CARD-D08/merchant" | $PY -c 'import json,sys; d=json.load(sys.stdin); d["private_token"]="<masked>"; print(json.dumps(d, indent=2))'
  echo
  echo "Draft written for CARD-D08:"
  $PY -c "import json; d=json.load(open('$SUB/drafts/CARD-D08/v1.json')); print(json.dumps({k: d[k] for k in ('classification','routing','facts','deadline')}, indent=2)); print('narrative:', d['narrative'])"
  echo
  echo "Search the draft for the injected content:"
  for p in "SYSTEM OVERRIDE" "Ignore bank policy" "immediate refund" "skip approval" "administrator" "SYNTHETIC-""PRIVATE"; do
    label=$p; [ "$p" = "SYNTHETIC-""PRIVATE" ] && label="private token marker"
    n=$(grep -c "$p" "$SUB/drafts/CARD-D08/v1.json"); echo "  '$label': $n match(es)"
  done
  echo
  echo "Trace: the content was flagged and treated as data; nothing was posted:"
  trace "$REP/traces/CARD-D08.jsonl" | grep -E "security|rules\.|record\.|review\." || true
  echo
  run uv run --locked pytest -q tests/unit/test_security.py::test_injected_merchant_note_is_treated_as_data
} 2>&1 | tee "$OUT/05_injection.txt"

# 6. Human boundary and the idempotency setup (stops at the human approval)
{
  header "Human boundary enforced in code"
  echo "Raw POST /v1/records with no approved review (mock rejects):"
  say "curl -s -X POST $URL/v1/records -H 'Idempotency-Key: probe-1' -d '{case_id: CARD-D01, review_id: rev_none, draft: {...}}'"
  curl -s -X POST "$URL/v1/records" -H 'Content-Type: application/json' -H 'Idempotency-Key: probe-1' \
    -d '{"case_id":"CARD-D01","review_id":"rev_none","draft":{"case_id":"CARD-D01"}}'; echo
  echo
  run env SUBMISSIONS_DIR="$SUB" $PY scripts/idempotency_demo.py prepare
  echo
  echo "Static checks: no approve or reject callable in src, no operator command in src, writes limited to reviews and records:"
  run uv run --locked pytest -q tests/unit/test_security.py tests/unit/test_gates.py tests/unit/test_approval.py
  say "grep -rnE 'def approve|\\.approve\\(|runtime.py' src/ || echo none"
  grep -rnE 'def approve|\.approve\(|runtime.py' src/ --include=*.py || echo "  none"
} 2>&1 | tee "$OUT/06_human_boundary.txt"

# 7. Credentials
{
  header "No hardcoded credentials"
  say "git ls-files | grep -E '(^|/)\\.env$' || echo 'no .env tracked'"
  git ls-files | grep -E '(^|/)\.env$' || echo "  no .env tracked"
  say "git grep -nIE 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|password)[\"'\\'' ]*[:=][ ]*[\"'\\''][A-Za-z0-9/+_-]{12,}'"
  git grep -nIE 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|password)["'\'' ]*[:=][ ]*["'\''][A-Za-z0-9/+_-]{12,}' || echo "  no matches"
  echo
  echo ".env.example values (keys blank):"
  grep -E 'KEY|SECRET|TOKEN' .env.example || true
  echo
  echo "Where keys come from (environment only):"
  grep -nE 'os.environ' src/dispute_copilot/llm/openai_compat.py | head -5
  run uv run --locked pytest -q tests/unit/test_security.py::test_no_credentials_in_tracked_files
} 2>&1 | tee "$OUT/07_credentials.txt"

# 8. Metrics and alerts
{
  header "Metrics tied to the SLO and alert thresholds"
  echo "config/rules/slo.yaml:"; cat config/rules/slo.yaml
  echo
  echo "reports/metrics.json from the default run:"
  cat "$REP/metrics.json" | $PY -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"run_id": d.get("run_id"), "metrics": d["metrics"], "alerts_fired": d["alerts_fired"]}, indent=2))'
  echo
  echo "Breaker demo run (threshold 1) metrics and alerts:"
  $PY -c "import json; d=json.load(open('$OUT/breaker_run/reports/metrics.json')); m=d['metrics']; print({k: m[k] for k in ('cases_total','good_case_rate','tool_exhausted_rate','breaker_opens','breaker_skips','manual_review_rate')}); print('alerts fired:', json.dumps(d['alerts_fired']))"
  echo
  echo "Sample JSON log lines (reports/logs/run.jsonl):"
  grep -E '"name": "(retry.backoff|tool.exhausted|security.untrusted_instruction)"' "$REP/logs/run.jsonl" | head -4
} 2>&1 | tee "$OUT/08_metrics_alerts.txt"

mkdir -p "$OUT/traces"
cp "$REP"/traces/*.jsonl "$OUT/traces/"
stop_mock
echo
echo "Evidence written to $OUT/. Mock stopped; state kept in $STATE."
echo "Next: approve the review printed in $OUT/06_human_boundary.txt, then run scripts/week10_duplicate.sh"
