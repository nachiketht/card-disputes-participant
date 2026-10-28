"""Rules, then the configured model. The reply is strict JSON."""

import hashlib
import re
import time

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import LLMError, failure_kind
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
    return quote_mark(quote, complaint)["matched"]


def quote_mark(quote: str, complaint: str) -> dict:
    """Match result for a quote. The quote text is not part of the result."""
    folded = " ".join(quote.split())
    source = " ".join(complaint.split())
    if not folded:
        return {"matched": False, "start": None, "end": None, "sha256": ""}
    digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()
    index = source.find(folded)
    if index < 0:
        return {"matched": False, "start": None, "end": None, "sha256": digest}
    return {"matched": True, "start": index, "end": index + len(folded), "sha256": digest}


def without_complaint_text(text: str, complaint: str) -> str:
    """Remove the complaint and its sentences from a saved string."""
    if not text or not complaint:
        return text
    cleaned = text
    for piece in complaint_pieces(complaint):
        if piece and piece in cleaned:
            cleaned = cleaned.replace(piece, " ")
    return " ".join(cleaned.split())


def complaint_pieces(complaint: str) -> list[str]:
    folded = " ".join(complaint.split())
    if not folded:
        return []
    pieces = [folded]
    for part in re.split(r"(?<=[.!?])\s+", folded):
        part = part.strip()
        if len(part) >= 12 and part not in pieces:
            pieces.append(part)
    return pieces


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
    except (LLMError, ValueError, TypeError) as exc:
        return _failed(started, failure_kind(exc), model)
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
        "evidence_sha256": "",
        "evidence_start": None,
        "evidence_end": None,
        "unclear": False,
        "fallback_used": bool(getattr(result.last, "fallback_used", False)),
        "primary_failure": _kept_failure(getattr(result.last, "primary_failure", "")),
    }
    if parsed is None or rejects(parsed["reason"]):
        base["unusable"] = True
        _trace_evidence(base)
        return base
    mark = quote_mark(parsed["evidence"], complaint)
    chosen = known_class(parsed["class"])
    base["evidence_ok"] = mark["matched"]
    base["evidence_sha256"] = mark["sha256"]
    base["evidence_start"] = mark["start"]
    base["evidence_end"] = mark["end"]
    base["reason"] = without_complaint_text(parsed["reason"], complaint)
    if parsed["class"] == UNCLEAR:
        base["unclear"] = True
        base["class"] = UNCLEAR
        _trace_evidence(base)
        return base
    if chosen is None or not mark["matched"]:
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


def _failed(started: float, detail: str, model=None) -> dict:
    used = bool(getattr(model, "fallback_used", False))
    primary = _kept_failure(getattr(model, "primary_failure", "") or "")
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "tokens_in": None,
        "tokens_out": None,
        "cost": {"status": "unpriced", "usd": None},
        "accepted": False,
        "unusable": True,
        "evidence_ok": False,
        "class": None,
        "reason": "",
        "evidence_sha256": "",
        "evidence_start": None,
        "evidence_end": None,
        "unclear": False,
        "failure": detail or "unusable",
        "fallback_used": used,
        "primary_failure": primary if used else "",
        "model": str(getattr(model, "model", "") or "") if used else "",
    }


def _kept_failure(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return failure_kind(LLMError(value))


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
