"""Step 1 checklist on the saved draft. The optional critic can only block.

This module does not post a review and does not create a record. It does not
read the tool APIs again, so a check does not add mock attempts.
"""

import json
import os
import uuid
from pathlib import Path

from dispute_copilot.contract import validate_draft
from dispute_copilot.errors import ContractError
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import (
    CASE_ID,
    CITATION_QUOTE,
    CITATION_SOURCE_ID,
    CITATIONS,
    CONTRACT_VERSION,
    NARRATIVE,
)
from dispute_copilot.llm.guard import rejects
from dispute_copilot.serialization import digest

from .handles import handle_path, read_handle, write_handle


def checklist(draft: dict) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    try:
        validate_draft(draft)
        checks["contract_valid"] = True
    except (TypeError, ValueError, ContractError):
        checks["contract_valid"] = False
    rendered = json.dumps(draft, ensure_ascii=False)
    checks["redaction_clean"] = not Redactor().contains_restricted(rendered)
    narrative = draft.get(NARRATIVE, "")
    checks["commitment_guard"] = isinstance(narrative, str) and not rejects(narrative)
    citations = draft.get(CITATIONS)
    checks["citations_present"] = (
        isinstance(citations, list)
        and len(citations) > 0
        and all(
            isinstance(item, dict) and item.get(CITATION_QUOTE) and item.get(CITATION_SOURCE_ID) for item in citations
        )
    )
    checks["narrative_marked_draft"] = isinstance(narrative, str) and "draft" in narrative.casefold()
    return checks


def apply_critic(checks: dict[str, bool], *, verdict: str | None, errored: bool) -> dict[str, bool]:
    """A pass leaves the other checks alone. A block or an error fails the gate."""
    updated = dict(checks)
    if errored or verdict == "block":
        updated["llm_critic"] = False
    else:
        updated["llm_critic"] = True
    return updated


def record_agent_review(
    submissions: Path,
    case_id: str,
    draft: dict,
    version: int,
    *,
    critic_verdict: str | None = None,
    critic_errored: bool = False,
) -> dict:
    checks = checklist(draft)
    mode = os.environ.get("FEATURE_LLM_REVIEW", "off").strip().lower()
    llm_review = None
    if mode == "block":
        checks = apply_critic(checks, verdict=critic_verdict, errored=critic_errored)
        llm_review = {"mode": mode, "verdict": "error" if critic_errored else critic_verdict}
    approval = {
        CASE_ID: case_id,
        "draft_version": version,
        "draft_hash": digest(draft),
        "passed": all(checks.values()),
        "checks": checks,
        "run_id": uuid.uuid4().hex,
        "llm_review": llm_review,
        "contract_version": CONTRACT_VERSION,
    }
    path = handle_path(submissions, case_id)
    handle = read_handle(path)
    handle["agent_approval"] = approval
    write_handle(path, handle)
    return approval
