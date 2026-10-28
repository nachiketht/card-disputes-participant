"""Human-review gate. False means the caller does not post.

The checklist is optional. The human review must name this case and this
server hash. A missing reviewer, a pending or rejected review, or any
exception fails closed.
"""

from dispute_copilot.fields import CASE_ID


def approval_gate(agent_approval: object, review: object, *, case_id: str, server_hash: str) -> bool:
    try:
        _ = agent_approval
        if not isinstance(case_id, str) or not isinstance(server_hash, str):
            return False
        if getattr(review, CASE_ID, None) != case_id:
            return False
        if getattr(review, "status", None) != "approved":
            return False
        if getattr(review, "draft_hash", None) != server_hash:
            return False
        reviewer = getattr(review, "reviewer", None)
        if not isinstance(reviewer, str) or not reviewer.strip():
            return False
        return True
    except Exception:
        return False
