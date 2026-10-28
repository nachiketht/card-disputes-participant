"""Submit the saved draft, then create a record only from the server's copy.

run writes a file and stops. submit-review posts that file. The checklist is
optional. create re-reads the human review, calls approval_gate, and posts
the decoded server draft. The local file is not the create body. A person
approves outside this process.
"""

import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.errors import CreateRejected
from dispute_copilot.fields import CASE_ID
from dispute_copilot.guardrails import record
from dispute_copilot.observability.tracing import current
from dispute_copilot.review_flow.gate import approval_gate
from dispute_copilot.review_flow.handles import handle_path, read_handle, write_handle
from dispute_copilot.serialization import digest, idempotency_key
from dispute_copilot.storage import latest_draft

Ledger = HttpCaseSystem | FakeCaseSystem


def load_handle(path: Path) -> dict:
    handle = read_handle(path)
    if not handle and not path.is_file():
        raise CreateRejected(400, {"error": "review_not_submitted"})
    if not isinstance(handle.get("review_id"), str):
        raise CreateRejected(400, {"error": "review_handle_unusable"})
    return handle


def agent_approval_matches(handle: dict, case_id: str, draft: dict) -> bool:
    approval = handle.get("agent_approval")
    if not isinstance(approval, dict):
        return False
    if approval.get("passed") is not True:
        return False
    if approval.get(CASE_ID) != case_id:
        return False
    return approval.get("draft_hash") == digest(draft)


def submit_review(case_id: str, submissions: Path, case_system: Ledger) -> dict:
    _path, draft, version = latest_draft(submissions, case_id)
    destination = handle_path(submissions, case_id)
    existing = read_handle(destination)
    matched = agent_approval_matches(existing, case_id, draft)
    current().event("review.step1_check", passed=matched, draft_version=version)
    with current().span("review.submit") as span:
        submitted = case_system.submit_review(case_id, draft)
        span.set(review=submitted.review_id, status=submitted.status)
    handle = dict(existing)
    handle["review_id"] = submitted.review_id
    handle["draft_hash"] = submitted.draft_hash
    handle["draft_version"] = version
    write_handle(destination, handle)
    return handle


def create_record(case_id: str, submissions: Path, case_system: Ledger) -> dict:
    handle = load_handle(handle_path(submissions, case_id))
    tracer = current()
    with tracer.span("record.precheck") as span:
        existing = case_system.get_record(case_id)
        span.set(found=existing.found, http_status=existing.http_status, record=existing.record_id)
    if existing.found:
        tracer.event("idempotency.existing_record", record=existing.record_id, posted=False)
        return {
            "http_status": existing.http_status,
            "record_id": existing.record_id,
            "existing_record_id": existing.record_id,
            "posted": False,
        }
    review = case_system.get_review(str(handle["review_id"]))
    try:
        draft = json.loads(review.draft_raw)
    except json.JSONDecodeError as exc:
        raise CreateRejected(400, {"error": "draft_not_json"}) from exc
    if not isinstance(draft, dict) or draft.get(CASE_ID) != case_id:
        raise CreateRejected(400, {"error": "draft_case_mismatch"})
    server_hash = review.draft_hash
    passed = approval_gate(handle.get("agent_approval"), review, case_id=case_id, server_hash=server_hash)
    tracer.event(
        "review.gate",
        level="info" if passed else "warning",
        passed=passed,
        review=review.review_id,
        review_status=review.status,
        reviewer_present=bool(review.reviewer),
    )
    if not passed:
        record("approval", passed=False)
        raise CreateRejected(403, {"error": "approval_gate", "status": review.status})
    if digest(draft) != server_hash:
        tracer.event("review.hash_mismatch", level="warning", review=review.review_id)
        raise CreateRejected(403, {"error": "hash_mismatch"})
    key = idempotency_key(case_id, review.review_id, server_hash)
    with tracer.span("record.create", idempotency_key=key[:15] + "...") as span:
        result = case_system.create_record(case_id, review.review_id, draft, key)
        span.set(http_status=result.http_status, record=result.record_id or result.existing_record_id)
    return {
        "http_status": result.http_status,
        "record_id": result.record_id or result.existing_record_id,
        "existing_record_id": result.existing_record_id,
        "posted": True,
    }
