"""Rules, then the configured model. The reply is strict JSON."""

import json
import time

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.prompts import PromptRegistry
from dispute_copilot.observability.cost import note_model_span, price_call
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
    folded = " ".join(quote.split())
    if not folded:
        return False
    return folded in " ".join(complaint.split())


def parse_reply(text: str) -> dict | None:
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    chosen = body.get("class")
    reason = body.get("reason")
    evidence = body.get("evidence")
    if not isinstance(chosen, str) or not isinstance(reason, str) or not isinstance(evidence, str):
        return None
    return {"class": chosen.strip(), "reason": reason.strip(), "evidence": evidence.strip()}


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
        with current().span("model.call") as span:
            completion = model.complete(system=rendered.system, user="Return the JSON object now.")
            note_model_span(span, completion)
    except (LLMError, ValueError, TypeError):
        return _failed(started, "")
    if not isinstance(completion, Completion):
        return _failed(started, "")
    latency = round((time.perf_counter() - started) * 1000, 3)
    priced = price_call(completion.model, completion.tokens_in, completion.tokens_out)
    parsed = parse_reply(completion.text)
    base = {
        "latency_ms": latency,
        "tokens_in": completion.tokens_in,
        "tokens_out": completion.tokens_out,
        "cost": priced,
        "model": completion.model,
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
        return base
    found = quote_found(parsed["evidence"], complaint)
    chosen = known_class(parsed["class"])
    base["evidence_ok"] = found
    base["evidence"] = parsed["evidence"]
    base["reason"] = parsed["reason"]
    if parsed["class"] == UNCLEAR:
        base["unclear"] = True
        base["class"] = UNCLEAR
        return base
    if chosen is None or not found:
        base["class"] = chosen.value if chosen is not None else None
        return base
    base["class"] = chosen.value
    base["accepted"] = True
    return base


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
