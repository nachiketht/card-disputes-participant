"""Submit the saved draft, then create a record only from the server's copy.

run writes a file and stops. submit-review posts that file only after a passed
step-1 agent approval for the same hash. create re-reads the human review,
calls approval_gate, and posts the decoded server draft. The local file is
not the create body. A person approves outside this process.
"""

import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.errors import CreateRejected
from dispute_copilot.fields import CASE_ID
from dispute_copilot.review_flow.gate import approval_gate
from dispute_copilot.review_flow.handles import handle_path, read_handle, write_handle
from dispute_copilot.serialization import digest, idempotency_key
from dispute_copilot.storage import latest_draft

Ledger = HttpCaseSystem | FakeCaseSystem


def load_handle(path: Path) -> dict:
    if not path.is_file():
        raise CreateRejected(400, {"error": "review_not_submitted"})
    handle = read_handle(path)
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
    existing = read_handle(destination) if destination.is_file() else {}
    if not agent_approval_matches(existing, case_id, draft):
        raise CreateRejected(400, {"error": "agent_approval_required"})
    submitted = case_system.submit_review(case_id, draft)
    handle = dict(existing)
    handle["review_id"] = submitted.review_id
    handle["draft_hash"] = submitted.draft_hash
    handle["draft_version"] = version
    write_handle(destination, handle)
    return handle


def create_record(case_id: str, submissions: Path, case_system: Ledger) -> dict:
    handle = load_handle(handle_path(submissions, case_id))
    existing = case_system.get_record(case_id)
    if existing.found:
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
    if not approval_gate(handle.get("agent_approval"), review, case_id=case_id, server_hash=server_hash):
        raise CreateRejected(403, {"error": "approval_gate", "status": review.status})
    if digest(draft) != server_hash:
        raise CreateRejected(403, {"error": "hash_mismatch"})
    key = idempotency_key(case_id, review.review_id, server_hash)
    result = case_system.create_record(case_id, review.review_id, draft, key)
    return {
        "http_status": result.http_status,
        "record_id": result.record_id or result.existing_record_id,
        "existing_record_id": result.existing_record_id,
        "posted": True,
    }
