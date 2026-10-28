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
from dispute_copilot.llm.base import LLMError, failure_kind
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.prompts import PromptRegistry
from dispute_copilot.llm.schema import CriticReply, complete_json, parse_model
from dispute_copilot.guardrails import record
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
    parsed = parse_model(CriticReply, text)
    if parsed is None:
        return "block", UNUSABLE_REASON
    return parsed.verdict, parsed.reason


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
    judge_skip: str | None = None,
) -> dict:
    checks = checklist(draft)
    if checks.get("commitment_guard") is False:
        record("output", reason="commitment")
    if checks.get("redaction_clean") is False:
        record("output", reason="redaction")
    if checks.get("contract_valid") is False:
        record("output", reason="contract")
    noted = injection_note(case_id, draft, trace_dir)
    mode = os.environ.get("FEATURE_LLM_REVIEW", "off").strip().lower()
    llm_review = None
    if judge_skip:
        llm_review = {"mode": "skipped", "verdict": "skipped", "reason": judge_skip}
    elif mode == "block" and all(checks.values()):
        verdict, reason = _ask_critic(draft, critic)
        meta = _critic_meta(critic)
        if verdict == "skipped":
            llm_review = {"mode": "skipped", "verdict": "skipped", "reason": reason, **meta}
        else:
            checks = apply_critic(checks, verdict=verdict, errored=False)
            llm_review = {"mode": mode, "verdict": verdict, "reason": reason, **meta}
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
        result = complete_json(critic, CriticReply, system=system, user=user)
    except (LLMError, OSError):
        _remember_critic(critic, None)
        return "skipped", "The judge step was skipped because the model call failed."
    except (ValueError, TypeError):
        _remember_critic(critic, None)
        return "block", UNUSABLE_REASON
    _remember_critic(critic, result.last)
    if not isinstance(result.value, CriticReply):
        return "block", UNUSABLE_REASON
    return result.value.verdict, result.value.reason


def _remember_critic(critic, completion) -> None:
    if critic is None:
        return
    if completion is None:
        used = bool(getattr(critic, "fallback_used", False))
        failure = _short_failure(getattr(critic, "primary_failure", ""))
        model = str(getattr(critic, "model", "") or "") if used else ""
    else:
        used = bool(getattr(completion, "fallback_used", False))
        failure = _short_failure(getattr(completion, "primary_failure", ""))
        model = str(getattr(completion, "model", "") or "")
    try:
        critic.last_meta = {
            "model": model,
            "fallback_used": used,
            "primary_failure": failure if used else "",
        }
    except (AttributeError, TypeError):
        return


def _short_failure(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return failure_kind(LLMError(value))


def _critic_meta(critic) -> dict:
    meta = getattr(critic, "last_meta", None)
    if not isinstance(meta, dict):
        return {"model": "", "fallback_used": False, "primary_failure": ""}
    failure = meta.get("primary_failure") if meta.get("fallback_used") else ""
    return {
        "model": str(meta.get("model") or ""),
        "fallback_used": bool(meta.get("fallback_used")),
        "primary_failure": str(failure or ""),
    }


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
