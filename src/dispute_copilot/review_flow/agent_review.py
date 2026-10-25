"""Step 1 checklist on the saved draft. The optional critic can only block.

This module does not post a review and does not create a record. It does not
read the tool APIs again, so a check does not add mock attempts.
"""

import json
import os
import re
import uuid
from pathlib import Path

from dispute_copilot.contract import validate_draft
from dispute_copilot.errors import ContractError
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.evidence.untrusted import instruction_flags
from dispute_copilot.fields import (
    CASE_ID,
    CITATION_QUOTE,
    CITATION_SOURCE_ID,
    CITATIONS,
    CLASSIFICATION,
    CONTRACT_VERSION,
    DEADLINE,
    FACT_AMOUNT,
    FACT_CURRENCY,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    FACTS,
    MISSING_INFORMATION,
    NARRATIVE,
    ROUTING,
)
from dispute_copilot.llm.base import LLMError
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.prompts import PromptRegistry
from dispute_copilot.observability.cost import note_model_span
from dispute_copilot.observability.tracing import current
from dispute_copilot.serialization import digest

from .handles import handle_path, read_handle, write_handle

UNUSABLE_REASON = "The model reply was not usable."


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


def interpret_critic_text(text: str) -> tuple[str, str]:
    """A usable reply is pass or block plus two to four sentences. Anything else blocks."""
    payload = _json_object(text)
    if payload is None:
        return "block", UNUSABLE_REASON
    verdict = payload.get("verdict")
    reason = payload.get("reason")
    if verdict not in {"pass", "block"} or not isinstance(reason, str):
        return "block", UNUSABLE_REASON
    lines = reason.strip().splitlines()
    sentences = _sentence_count(reason)
    if not lines or len(lines) > 4 or any(not line.strip() for line in lines):
        return "block", UNUSABLE_REASON
    if sentences < 2 or sentences > 4:
        return "block", UNUSABLE_REASON
    return verdict, reason.strip()


def format_reviewer_note(draft: dict, approval: dict) -> str:
    """Plain note for the person who approves. It is not sent to the mock."""
    facts = draft.get(FACTS)
    if not isinstance(facts, dict):
        facts = {}
    amount = facts.get(FACT_AMOUNT)
    currency = facts.get(FACT_CURRENCY)
    if amount is None:
        amount_text = "empty"
    else:
        amount_text = f"{amount} {currency}".strip() if currency else str(amount)
    review = approval.get("llm_review") if isinstance(approval, dict) else None
    lines = [f"Reviewer note for {draft.get(CASE_ID, '')}"]
    if isinstance(review, dict) and review.get("verdict"):
        lines.append(f"Verdict: {review.get('verdict')}")
        lines.append(f"Reason: {review.get('reason')}")
    else:
        lines.append("No model reason. The checklist result still stands.")
    lines.extend(
        [
            f"Type: {draft.get(CLASSIFICATION, '')}",
            f"Purchase: {facts.get(FACT_TRANSACTION_ID, '')}",
            f"Amount: {amount_text}",
            f"Route: {draft.get(ROUTING, '')}",
            f"Deadline: {draft.get(DEADLINE, '')}",
        ]
    )
    note = approval.get("injection_note") if isinstance(approval, dict) else None
    if isinstance(note, str) and note.strip():
        lines.append(note.strip())
    lines.append("A pass does not approve the case.")
    return "\n".join(lines)


def injection_note(case_id: str, draft: dict, trace_dir: Path | None = None) -> str | None:
    """Short checklist line. Pattern names only; the untrusted text is not copied."""
    categories: set[str] = set(instruction_flags(json.dumps(draft, ensure_ascii=False)))
    if trace_dir is not None:
        path = trace_dir / f"{case_id}.jsonl"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("name") != "injection_detected":
                    continue
                attrs = record.get("attrs") if isinstance(record.get("attrs"), dict) else {}
                found = attrs.get("category")
                if isinstance(found, list):
                    categories.update(str(item) for item in found)
                elif isinstance(found, str) and found:
                    categories.add(found)
    if not categories:
        return None
    listed = ", ".join(sorted(categories))
    sentence = f"Untrusted text matched instruction patterns ({listed}) and was treated as data."
    return Redactor().mask(sentence)


def record_agent_review(
    submissions: Path,
    case_id: str,
    draft: dict,
    version: int,
    *,
    critic=None,
    trace_dir: Path | None = None,
) -> dict:
    checks = checklist(draft)
    noted = injection_note(case_id, draft, trace_dir)
    mode = os.environ.get("FEATURE_LLM_REVIEW", "off").strip().lower()
    llm_review = None
    if mode == "block" and all(checks.values()):
        verdict, reason = _ask_critic(draft, critic)
        checks = apply_critic(checks, verdict=verdict, errored=False)
        llm_review = {"mode": mode, "verdict": verdict, "reason": reason}
    approval = {
        CASE_ID: case_id,
        "draft_version": version,
        "draft_hash": digest(draft),
        "passed": all(checks.values()),
        "checks": checks,
        "run_id": uuid.uuid4().hex,
        "llm_review": llm_review,
        "contract_version": CONTRACT_VERSION,
        "injection_note": noted,
    }
    current().event(
        "review.agent_checklist",
        level="info" if approval["passed"] else "warning",
        passed=approval["passed"],
        failed_checks=sorted(name for name, ok in checks.items() if not ok),
        llm_mode=mode,
        draft_version=version,
    )
    path = handle_path(submissions, case_id)
    handle = read_handle(path)
    handle["agent_approval"] = approval
    write_handle(path, handle)
    return approval


def _ask_critic(draft: dict, critic) -> tuple[str, str]:
    if critic is None:
        return "block", UNUSABLE_REASON
    try:
        system, user = _critic_messages(draft)
        with current().span("model.call") as span:
            completion = critic.complete(system=system, user=user)
            note_model_span(span, completion)
    except (LLMError, OSError, ValueError, TypeError):
        return "block", UNUSABLE_REASON
    text = completion.text if isinstance(getattr(completion, "text", None), str) else ""
    return interpret_critic_text(text)


def _critic_messages(draft: dict) -> tuple[str, str]:
    citations = draft.get(CITATIONS)
    facts = draft.get(FACTS)
    if not isinstance(facts, dict):
        facts = {}
    missing = draft.get(MISSING_INFORMATION)
    if isinstance(missing, list) and missing:
        missing_text = ", ".join(str(item) for item in missing)
    else:
        missing_text = "none"
    amount = facts.get(FACT_AMOUNT)
    rendered = PromptRegistry().render(
        "critic",
        {
            CLASSIFICATION: str(draft.get(CLASSIFICATION, "")),
            ROUTING: str(draft.get(ROUTING, "")),
            "citation_count": str(len(citations) if isinstance(citations, list) else 0),
            "redaction_clean": "true",
            NARRATIVE: str(draft.get(NARRATIVE, "")),
            FACT_TRANSACTION_ID: str(facts.get(FACT_TRANSACTION_ID) or ""),
            FACT_AMOUNT: "empty" if amount is None else str(amount),
            FACT_CURRENCY: str(facts.get(FACT_CURRENCY) or ""),
            FACT_REPORTED_DATE: str(facts.get(FACT_REPORTED_DATE) or ""),
            DEADLINE: str(draft.get(DEADLINE) or ""),
            "missing_fields": missing_text,
        },
    )
    return rendered.system, "Return one JSON object only."


def _sentence_count(reason: str) -> int:
    parts = re.split(r"[.!?]+", reason.strip())
    return len([part for part in parts if part.strip()])


def _json_object(text: str) -> dict | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].casefold() == "json":
            raw = raw[4:].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return value
