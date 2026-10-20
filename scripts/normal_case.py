"""Thin normal case for CARD-D01 only.

Days 1–2 slice. One unauthorized complaint, template text, no other case types.

Assumptions used here:
- Policy version is chosen from the intake Reported date, not today's date.
- Authorization: denied means unauthorized, nothing missing, ready_for_review.
- Deadline is Reported date plus the policy's unauthorized day count.
  The report day is day zero. Weekends do not shift the date.
- Amount and currency come from the transactions tool for the named id.
- Merchant text is read and then ignored except the display name.
- This process never approves a review. A person runs tools/runtime.py approve.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_ID = "CARD-D01"
DRAFT_PATH = ROOT / "submissions" / "drafts" / CASE_ID / "v1.json"
REVIEW_PATH = ROOT / "submissions" / "reviews" / f"{CASE_ID}.json"
RESULTS_PATH = ROOT / "submissions" / "results.json"


def request(method, url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload


def labels(text, wanted):
    found = {}
    for line in text.splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key in wanted:
            found[key] = value.strip()
    return found


def load_text(data_dir, source_id, kind):
    path = data_dir / kind / f"{source_id}.md"
    if not path.is_file():
        sys.exit(f"Missing {kind} file for {source_id}")
    return path.read_text()


def select_policy(data_dir, reported):
    chosen = None
    for path in sorted((data_dir / "policies").glob("CARD-POLICY-V*.md")):
        text = path.read_text()
        fields = labels(text, {"Effective from", "Effective through", "Unauthorized deadline days"})
        start = date.fromisoformat(fields["Effective from"])
        end = date.fromisoformat(fields["Effective through"])
        if start <= reported <= end:
            chosen = (path.stem, text, int(fields["Unauthorized deadline days"]))
    if chosen is None:
        sys.exit(f"No policy covers {reported.isoformat()}; this slice does not guess.")
    return chosen


FORM_LABELS = {
    "Source ID",
    "Reported date",
    "Transaction ID",
    "Customer reference",
    "Authorization",
    "Promised service date",
    "Purchase receipt",
    "Submission reference",
}


def complaint_text(intake):
    """Free-text complaint, skipping the heading and the labeled form lines."""
    kept = []
    for line in intake.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if kept:
                break
            continue
        key = stripped.split(": ", 1)[0] if ": " in stripped else None
        if key in FORM_LABELS:
            if kept:
                break
            continue
        kept.append(stripped)
    if not kept:
        sys.exit("Intake has no complaint text.")
    quote = "\n".join(kept)
    if quote not in intake:
        sys.exit("Complaint text is not an excerpt of the intake.")
    return quote


def line_starting(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    sys.exit(f"No line starts with {prefix!r}")


def cite(claim, source_id, quote, source_text):
    if not quote or quote not in source_text:
        sys.exit(f"Quote for {claim} is not in {source_id}")
    return {"claim": claim, "source_id": source_id, "quote": quote}


def build_draft(data_dir, base_url):
    status, health = request("GET", f"{base_url}/health")
    if status != 200 or health.get("status") != "ok":
        sys.exit(f"Mock is not healthy: {status} {health}")

    status, listing = request("GET", f"{base_url}/v1/cases")
    if status != 200:
        sys.exit(f"Could not list cases: {status} {listing}")
    case = next((item for item in listing["cases"] if item["case_id"] == CASE_ID), None)
    if case is None:
        sys.exit(f"{CASE_ID} is not in the mock case list")

    intake_id = case["intake_source"]
    intake = load_text(data_dir, intake_id, "documents")
    intake_fields = labels(intake, {"Reported date", "Transaction ID", "Authorization"})
    if intake_fields.get("Authorization") != "denied":
        sys.exit("This slice only handles Authorization: denied.")

    reported = date.fromisoformat(intake_fields["Reported date"])
    policy_id, policy_text, unauthorized_days = select_policy(data_dir, reported)
    procedure = load_text(data_dir, "CARD-PROCEDURE-01", "policies")
    deadline = (reported + timedelta(days=unauthorized_days)).isoformat()

    status, transactions = request("GET", f"{base_url}/v1/cases/{CASE_ID}/transactions")
    if status != 200:
        sys.exit(f"Transactions tool failed ({status}). Retry is later work.")
    named = next(
        (row for row in transactions["transactions"] if row["transaction_id"] == intake_fields["Transaction ID"]),
        None,
    )
    if named is None:
        sys.exit("Named transaction was not in the tool result.")

    status, merchant = request("GET", f"{base_url}/v1/cases/{CASE_ID}/merchant")
    if status != 200:
        sys.exit(f"Merchant tool failed ({status}).")
    display_name = merchant.get("display_name") or "unavailable"
    txn_text = json.dumps(transactions, sort_keys=True)
    complaint = complaint_text(intake)
    amount_quote = f'"amount": {json.dumps(named["amount"])}'
    draft = {
        "case_id": CASE_ID,
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "policy_ids": [policy_id, "CARD-PROCEDURE-01"],
        "facts": {
            "transaction_id": intake_fields["Transaction ID"],
            "amount": named["amount"],
            "currency": named["currency"],
            "reported_date": intake_fields["Reported date"],
        },
        "missing_information": [],
        "deadline": deadline,
        "citations": [
            cite("deadline", policy_id, f"Unauthorized deadline days: {unauthorized_days}", policy_text),
            cite("routing", "CARD-PROCEDURE-01", line_starting(procedure, "Routing precedence:"), procedure),
            cite("customer_evidence", intake_id, complaint, intake),
            cite("transaction_amount", transactions["source_id"], amount_quote, txn_text),
        ],
        "follow_up_questions": [],
        "narrative": (
            f"Draft for human review: unauthorized complaint for {intake_fields['Transaction ID']}, "
            f"reported {intake_fields['Reported date']}. Merchant display name from the tool: {display_name}. "
            f"Recommended routing: ready_for_review. The supplied training-policy deadline is {deadline}. "
            "Customer message draft: We have prepared your complaint for review. "
            "We cannot confirm an outcome. No case has been created yet; human approval remains required."
        ),
    }
    blob = json.dumps(draft)
    if "SYNTHETIC-PRIVATE" in blob or "CUST-" in blob or "private_token" in blob:
        sys.exit("Draft would leak a restricted value.")
    return draft


def cmd_run(args):
    draft = build_draft(args.data_dir, args.base_url)
    DRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DRAFT_PATH.write_text(json.dumps(draft, indent=2) + "\n")
    RESULTS_PATH.write_text(json.dumps([draft], indent=2) + "\n")

    status, review = request(
        "POST",
        f"{args.base_url}/v1/reviews",
        {"case_id": CASE_ID, "draft": draft},
    )
    if status != 201:
        sys.exit(f"Review was not accepted: {status} {review}")
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PATH.write_text(json.dumps({
        "review_id": review["review_id"],
        "draft_hash": review["draft_hash"],
        "idempotency_key": f"create-{CASE_ID}-v1",
    }, indent=2) + "\n")
    print(json.dumps({
        "draft": str(DRAFT_PATH.relative_to(ROOT)),
        "review_id": review["review_id"],
        "status": review["status"],
        "next": "A person approves this review. This script will not.",
    }, indent=2))


def cmd_create(args):
    if not REVIEW_PATH.is_file() or not DRAFT_PATH.is_file():
        sys.exit("Run the case first.")
    handle = json.loads(REVIEW_PATH.read_text())
    draft = json.loads(DRAFT_PATH.read_text())
    status, review = request("GET", f"{args.base_url}/v1/reviews/{handle['review_id']}")
    if status != 200:
        sys.exit(f"Could not read review: {status} {review}")
    if review["status"] != "approved":
        sys.exit(
            f"Review {handle['review_id']} is {review['status']}. "
            "Nothing was written. Ask a person to approve that exact draft."
        )
    status, created = request(
        "POST",
        f"{args.base_url}/v1/records",
        {"case_id": CASE_ID, "review_id": handle["review_id"], "draft": draft},
        {"Idempotency-Key": handle["idempotency_key"]},
    )
    if status not in (200, 201):
        sys.exit(f"Create failed: {status} {created}")
    print(json.dumps({"http_status": status, **created}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "dev")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("create")
    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    else:
        cmd_create(args)


if __name__ == "__main__":
    main()
