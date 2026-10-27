"""Rules, then the configured model. The reply is strict JSON."""

import time

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import LLMError
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.prompts import PromptRegistry
from dispute_copilot.llm.schema import ClassifierReply, complete_json, parse_model
from dispute_copilot.observability.cost import price_call
from dispute_copilot.observability.tracing import current

UNCLEAR = "multiple_or_unclear"


def category_text() -> str:
    lines = [f"{item.value}: {_meaning(item)}" for item in Classification]
    lines.append(f"{UNCLEAR}: more than one class, or not clear")
    return "\n".join(lines)


def redact_complaint(complaint: str, redactor: Redactor | None = None) -> str | None:
    """Mask restricted text. None means the complaint cannot be made clean."""
    cleaner = redactor or Redactor()
    masked = cleaner.mask(complaint)
    if cleaner.contains_restricted(masked):
        return None
    return masked


def quote_found(quote: str, complaint: str) -> bool:
    """True when the quote is exact complaint text, with whitespace collapsed."""
    folded = " ".join(quote.split())
    if not folded:
        return False
    return folded in " ".join(complaint.split())


def parse_reply(text: str) -> dict | None:
    parsed = parse_model(ClassifierReply, text)
    if parsed is None:
        return None
    return {"class": parsed.label, "reason": parsed.reason, "evidence": parsed.evidence}


def known_class(name: str) -> Classification | None:
    for item in Classification:
        if name == item.value:
            return item
    return None


def call_model(model, complaint: str, registry: PromptRegistry | None = None) -> dict:
    """One model call. A failure dict has accepted False and stores no blocked reason."""
    started = time.perf_counter()
    prompts = registry or PromptRegistry()
    try:
        rendered = prompts.render("classifier", {"complaint": complaint, "categories": category_text()})
        result = complete_json(
            model,
            ClassifierReply,
            system=rendered.system,
            user="Return the JSON object now.",
        )
    except (LLMError, ValueError, TypeError):
        return _failed(started, "")
    latency = round((time.perf_counter() - started) * 1000, 3)
    priced = price_call(result.last.model, result.tokens_in, result.tokens_out)
    parsed = None
    if isinstance(result.value, ClassifierReply):
        parsed = {"class": result.value.label, "reason": result.value.reason, "evidence": result.value.evidence}
    base = {
        "latency_ms": latency,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost": priced,
        "model": result.last.model,
        "accepted": False,
        "unusable": False,
        "evidence_ok": False,
        "class": None,
        "reason": "",
        "evidence": "",
        "unclear": False,
    }
    if parsed is None or rejects(parsed["reason"]):
        base["unusable"] = True
        _trace_evidence(base)
        return base
    found = quote_found(parsed["evidence"], complaint)
    chosen = known_class(parsed["class"])
    base["evidence_ok"] = found
    base["evidence"] = parsed["evidence"]
    base["reason"] = parsed["reason"]
    if parsed["class"] == UNCLEAR:
        base["unclear"] = True
        base["class"] = UNCLEAR
        _trace_evidence(base)
        return base
    if chosen is None or not found:
        base["class"] = chosen.value if chosen is not None else None
        _trace_evidence(base)
        return base
    base["class"] = chosen.value
    base["accepted"] = True
    _trace_evidence(base)
    return base


def _trace_evidence(base: dict) -> None:
    """Record the quote check. The quote itself is not written."""
    current().event(
        "model.evidence_check",
        evidence_ok=base["evidence_ok"],
        accepted=base["accepted"],
        unusable=base["unusable"],
        unclear=base["unclear"],
        chosen=base["class"],
    )


def _failed(started: float, _detail: str) -> dict:
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "tokens_in": None,
        "tokens_out": None,
        "cost": {"status": "unpriced", "usd": None},
        "model": "",
        "accepted": False,
        "unusable": True,
        "evidence_ok": False,
        "class": None,
        "reason": "",
        "evidence": "",
        "unclear": False,
    }


def _meaning(item: Classification) -> str:
    if item == Classification.UNAUTHORIZED:
        return "the customer did not authorize the purchase"
    if item == Classification.DUPLICATE_CHARGE:
        return "the same purchase was charged more than once"
    if item == Classification.SERVICE_NOT_RECEIVED:
        return "goods or a service were not delivered"
    if item == Classification.AMBIGUOUS:
        return "the complaint does not fit one class"
    return "cash or ATM"
