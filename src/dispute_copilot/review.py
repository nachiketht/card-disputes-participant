"""Submit the saved draft, then create a record only from the server's copy.

run writes a file and stops. This module posts that file unchanged. create
re-reads the review, checks the server hash, and posts the decoded server
draft. The local file is not the create body. A person approves outside this
process; this module never does.
"""

import json
from pathlib import Path

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.errors import CreateRejected
from dispute_copilot.fields import CASE_ID
from dispute_copilot.serialization import digest, idempotency_key
from dispute_copilot.storage import latest_draft

Ledger = HttpCaseSystem | FakeCaseSystem


def load_handle(path: Path) -> dict:
    if not path.is_file():
        raise CreateRejected(400, {"error": "review_not_submitted"})
    handle = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(handle, dict) or not isinstance(handle.get("review_id"), str):
        raise CreateRejected(400, {"error": "review_handle_unusable"})
    return handle


def handle_path(submissions: Path, case_id: str) -> Path:
    return submissions / "reviews" / f"{case_id}.json"


def submit_review(case_id: str, submissions: Path, case_system: Ledger) -> dict:
    _path, draft, version = latest_draft(submissions, case_id)
    submitted = case_system.submit_review(case_id, draft)
    handle = {
        "review_id": submitted.review_id,
        "draft_hash": submitted.draft_hash,
        "draft_version": version,
    }
    destination = handle_path(submissions, case_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(handle, indent=2) + "\n", encoding="utf-8")
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
    if review.case_id != case_id or review.status != "approved":
        raise CreateRejected(403, {"error": "not_approved", "status": review.status})
    try:
        draft = json.loads(review.draft_raw)
    except json.JSONDecodeError as exc:
        raise CreateRejected(400, {"error": "draft_not_json"}) from exc
    if not isinstance(draft, dict) or draft.get(CASE_ID) != case_id:
        raise CreateRejected(400, {"error": "draft_case_mismatch"})
    if digest(draft) != review.draft_hash:
        raise CreateRejected(403, {"error": "hash_mismatch"})
    stored = handle.get("draft_hash")
    if isinstance(stored, str) and stored != review.draft_hash:
        raise CreateRejected(403, {"error": "stored_hash_mismatch"})
    key = idempotency_key(case_id, review.review_id, review.draft_hash)
    result = case_system.create_record(case_id, review.review_id, draft, key)
    return {
        "http_status": result.http_status,
        "record_id": result.record_id or result.existing_record_id,
        "existing_record_id": result.existing_record_id,
        "posted": True,
    }
