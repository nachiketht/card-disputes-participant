"""Duplicate-request demo against the running mock.

Step 1 (prepare) drafts, runs the step-1 agent review and submits the review
for a case and for its redelivered twin, shows that create-record is refused
while the review is pending, and prints the approve command. It never
approves. A person runs that command.

Step 2 (duplicate) runs after the human approval. It creates the record once,
then sends the same create again in four ways and shows that only one record
exists.

    uv run --locked python scripts/idempotency_demo.py prepare
    uv run --locked python scripts/idempotency_demo.py duplicate
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dispute_copilot.adapters.case_system import HttpCaseSystem  # noqa: E402
from dispute_copilot.serialization import idempotency_key  # noqa: E402

PORT = os.environ.get("MOCK_PORT", "8766")
URL = f"http://127.0.0.1:{PORT}"
STATE = os.environ.get("MOCK_STATE", ".state/week10.sqlite")
SUBMISSIONS = Path(os.environ.get("SUBMISSIONS_DIR", ".state/week10/submissions"))


def copilot(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LLM_PROVIDER": "null", "FEATURE_LLM_REVIEW": "off"}
    command = [sys.executable, "-m", "dispute_copilot", "--mock-url", URL, "--submissions", str(SUBMISSIONS), *args]
    print("$ dispute-copilot " + " ".join(args))
    done = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=env)
    out = (done.stdout + done.stderr).strip()
    if out:
        print("  " + out.replace("\n", "\n  "))
    print(f"  exit={done.returncode}")
    return done


def review_id(case_id: str) -> str:
    handle = json.loads((SUBMISSIONS / "reviews" / f"{case_id}.json").read_text(encoding="utf-8"))
    return str(handle["review_id"])


def audit_events(case_ids: set[str]) -> list[dict]:
    done = subprocess.run(
        [sys.executable, "tools/runtime.py", "audit", "--state", STATE], cwd=ROOT, text=True, capture_output=True, check=True
    )
    return [row for row in json.loads(done.stdout) if row["case_id"] in case_ids and row["event"] != "tool_read"]


def approve_command(rid: str) -> str:
    return f"uv run --locked python tools/runtime.py approve {rid} --reviewer <your-name> --state {STATE}"


def prepare(case_id: str, twin: str) -> int:
    for case in (case_id, twin):
        for step in ("run", "agent-review", "submit-review"):
            if copilot(step, case).returncode != 0:
                return 1
    print("\nCreate before approval must be refused:")
    refused = copilot("create-record", case_id)
    events = audit_events({case_id})
    created = [e for e in events if e["event"] == "record_created"]
    print(f"  records created for {case_id}: {len(created)}")
    if refused.returncode == 0 or created:
        print("UNEXPECTED: create was not refused")
        return 1
    print("\nHUMAN STEP. Approve the review, then run the duplicate step:")
    print("  " + approve_command(review_id(case_id)))
    print("  scripts/week10_duplicate.sh")
    return 0


def duplicate(case_id: str, twin: str) -> int:
    rid = review_id(case_id)
    with httpx.Client(base_url=URL, timeout=5.0, trust_env=False) as http:
        ledger = HttpCaseSystem(http)
        review = ledger.get_review(rid)
        if review.status != "approved":
            print(f"Review {rid} is {review.status}. A person approves it first:")
            print("  " + approve_command(rid))
            return 3
        print(f"Review {rid} is approved by {review.reviewer}.\n")
        print("1) First create through the normal path:")
        first = copilot("create-record", case_id)
        if first.returncode != 0:
            return 1
        record = json.loads(first.stdout)["record_id"]
        print("\n2) Same command again (operator double-click). Pre-check finds the record; nothing is posted:")
        copilot("create-record", case_id)
        draft = json.loads(review.draft_raw)
        key = idempotency_key(case_id, rid, review.draft_hash)
        print("\n3) Raw replay of the same POST with the same Idempotency-Key (network retry):")
        replay = ledger.create_record(case_id, rid, draft, key)
        print(f"  HTTP {replay.http_status} record_id={replay.record_id} body={json.dumps(replay.body)}")
        print("\n4) Same Idempotency-Key, changed draft:")
        changed = {**draft, "narrative": draft["narrative"] + " Changed."}
        conflict = ledger.create_record(case_id, rid, changed, key)
        print(f"  HTTP {conflict.http_status} body={json.dumps(conflict.body)}")
        print("\n5) New Idempotency-Key, same case (duplicate case creation):")
        fresh = ledger.create_record(case_id, rid, draft, key + "-new")
        print(f"  HTTP {fresh.http_status} existing_record_id={fresh.existing_record_id} body={json.dumps(fresh.body)}")
        print(f"\n6) Redelivered complaint {twin} (duplicate webhook/intake). Pre-check resolves it to the same record:")
        copilot("create-record", twin)
        lookup = ledger.get_record(twin)
        print(f"  GET /v1/records/{twin} -> {lookup.http_status} id={lookup.record_id}")
    events = audit_events({case_id, twin})
    print("\nMock audit (writes only):")
    for row in events:
        print(f"  {row['at']}  {row['event']:<24} {row['case_id']}  {row['detail']}")
    created = [e for e in events if e["event"] == "record_created"]
    ok = (
        len(created) == 1
        and replay.http_status == 200
        and replay.record_id == record
        and conflict.http_status == 409
        and fresh.http_status == 409
        and lookup.record_id == record
    )
    print(f"\nrecords created: {len(created)} (expected 1). {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["prepare", "duplicate"])
    parser.add_argument("--case", default="CARD-D02")
    parser.add_argument("--twin", default="CARD-D09")
    args = parser.parse_args()
    if args.step == "prepare":
        return prepare(args.case, args.twin)
    return duplicate(args.case, args.twin)


if __name__ == "__main__":
    sys.exit(main())
