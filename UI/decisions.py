"""Record a human decision on a pending mock review.

This writes the same state file the operator command uses. It changes the
status and the reviewer name only. The stored draft and its hash stay put.
"""

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dispute_copilot.adapters import ledger as ledger_api
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.serialization import hash_canonical

_RUNTIME = None
_CUSTOMER_MARK = "Customer message draft:"


class DecisionRefused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def open_ledger(state: Path):
    """Open the mock state file, creating its tables when the file is new."""
    return _runtime().connect(state)


def list_reviews(state: Path | None) -> list[dict]:
    """Pending reviews first. A missing file is an empty list."""
    if not _postgres_backend() and (state is None or not Path(state).is_file()):
        return []
    with ledger_api.open_ledger(state).transaction(immediate=False) as tx:
        rows = tx.list_reviews()
    listed = [_row(row) for row in rows]
    listed.sort(key=lambda item: (item["status"] != "pending", item["case_id"], item["id"]))
    return listed


def record_decision(state: Path, review_id: str, reviewer: str, *, accepted: bool) -> str:
    """Mark one pending review accepted or declined.

    The draft text and the hash column are not written.
    """
    if state is None:
        raise DecisionRefused("unavailable")
    if not _safe_id(review_id):
        raise DecisionRefused("pending")
    name = reviewer.strip()
    if not name:
        raise DecisionRefused("name")
    status = "approved" if accepted else "rejected"
    with ledger_api.open_ledger(state).transaction() as tx:
        row = tx.get_review(review_id)
        if not row or row["status"] != "pending":
            raise DecisionRefused("pending")
        before_draft = row["draft"]
        before_hash = row["hash"]
        tx.set_review_status(review_id, status, name)
        payload = {"review_id": review_id, "reviewer": name, "draft_hash": before_hash}
        tx.append_audit(
            datetime.now(timezone.utc).isoformat(),
            "human_" + status,
            row["case_id"],
            hash_canonical(payload),
            payload,
        )
        saved = tx.get_review(review_id)
        if saved is None or saved["draft"] != before_draft or saved["hash"] != before_hash:
            raise DecisionRefused("unavailable")
    return status


def _row(row) -> dict:
    message, withheld = _message(row["draft"])
    reviewer = row["reviewer"] if isinstance(row["reviewer"], str) else ""
    digest = row["hash"] if isinstance(row["hash"], str) else ""
    label, tone = _status_label(row["status"])
    return {
        "id": row["id"],
        "case_id": row["case_id"],
        "status": row["status"],
        "status_label": label,
        "tone": tone,
        "reviewer": reviewer,
        "hash_short": digest[:12],
        "pending": row["status"] == "pending",
        "message": message,
        "withheld": withheld,
    }


def _status_label(status: object) -> tuple[str, str]:
    if status == "pending":
        return "Pending review", "ask"
    if status == "approved":
        return "Approved", "ready"
    if status == "rejected":
        return "Declined", "hold"
    return str(status or ""), ""


def _message(draft: object) -> tuple[str, bool]:
    if not isinstance(draft, str) or not draft:
        return "", False
    try:
        payload = json.loads(draft)
    except json.JSONDecodeError:
        return "", False
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    if not isinstance(narrative, str) or _CUSTOMER_MARK not in narrative:
        return "", False
    text = narrative.split(_CUSTOMER_MARK, 1)[1].strip()
    redactor = Redactor()
    if not text or redactor.contains_restricted(text):
        return "", True
    return text, False


def _postgres_backend() -> bool:
    return os.environ.get("LEDGER_BACKEND") == "postgres"


def _safe_id(review_id: str) -> bool:
    if not isinstance(review_id, str) or not review_id or review_id in {".", ".."}:
        return False
    if len(review_id) > 128:
        return False
    return "/" not in review_id and "\\" not in review_id and ".." not in review_id


def _runtime():
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    path = Path(__file__).resolve().parents[1] / "tools" / "runtime.py"
    spec = importlib.util.spec_from_file_location("card_mock_state", path)
    if spec is None or spec.loader is None:
        raise DecisionRefused("unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _RUNTIME = module
    return module
