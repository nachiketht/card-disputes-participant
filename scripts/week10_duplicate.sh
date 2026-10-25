#!/usr/bin/env bash
# Run after a person approved the review printed by week10_evidence.sh.
# Restarts the mock on the same state and replays the create in four ways.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT=${MOCK_PORT:-8766}
STATE=${MOCK_STATE:-.state/week10.sqlite}
OUT=evidence/week10
export MOCK_PORT=$PORT MOCK_STATE=$STATE LLM_PROVIDER=null FEATURE_LLM_REVIEW=off
export SUBMISSIONS_DIR=.state/week10/submissions REPORTS_DIR=$OUT/idempotency
uv run --locked python tools/runtime.py serve --data data/dev --state "$STATE" --port "$PORT" >.state/week10/mock-dup.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null' EXIT
for _ in $(seq 1 50); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 0.1; done
{
  echo "# Idempotency: duplicate request handling"
  echo "# $(date '+%Y-%m-%d %H:%M:%S %Z')  commit $(git rev-parse --short HEAD)"
  echo
  echo "Key derivation (src/dispute_copilot/serialization.py):"
  sed -n '/def idempotency_key/,/return/p' src/dispute_copilot/serialization.py
  echo
  uv run --locked python scripts/idempotency_demo.py duplicate
  echo "[exit $?]"
  echo
  for f in "$REPORTS_DIR"/traces/CARD-D02.create-record.jsonl "$REPORTS_DIR"/traces/CARD-D09.create-record.jsonl; do
    [ -f "$f" ] && uv run --locked python scripts/trace_view.py "$f"
  done
} 2>&1 | tee "$OUT/09_idempotency.txt"
