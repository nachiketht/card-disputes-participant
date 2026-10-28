"""Run one approach. Model calls are not reused across approaches."""

import time
from dataclasses import dataclass

from dispute_copilot.classifiers.clef import call_clef
from dispute_copilot.classifiers.eligibility import accept_proposed_class, model_allowed
from dispute_copilot.classifiers.guard import apply_duplicate_guard
from dispute_copilot.classifiers.haiku import call_model, known_class, redact_complaint
from dispute_copilot.classifiers.jev import call_jev
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import LLMError, failure_kind
from dispute_copilot.rules.classify import Decision, missing_for
from dispute_copilot.rules.routing import choose_routing


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    path: str
    rules: Decision
    jev: dict | None
    haiku: dict | None
    called_model: bool
    latency_ms: float
    route: str
    clef: dict | None = None


def run_approach(
    *,
    approach: str,
    rules: Decision,
    complaint: str,
    authorization: str,
    promised_service_date: str | None,
    purchase_receipt: str | None,
    attachment_status: str | None,
    transactions: dict | None,
    transactions_unavailable: bool,
    model=None,
    jev=None,
    jev_threshold: float = 0.70,
    clef=None,
) -> Outcome:
    started = time.perf_counter()
    fields = (authorization, promised_service_date, purchase_receipt, attachment_status)
    if approach == "1" or not model_allowed(rules, authorization):
        return _close(
            rules,
            "rules_only",
            rules,
            None,
            None,
            False,
            started,
            transactions,
            transactions_unavailable,
            fields,
        )
    cleaned = redact_complaint(complaint)
    if approach == "2":
        return _approach_two(
            rules,
            cleaned,
            fields,
            transactions,
            transactions_unavailable,
            model,
            started,
        )
    return _approach_three(
        rules,
        cleaned,
        fields,
        transactions,
        transactions_unavailable,
        model,
        jev,
        clef,
        jev_threshold,
        started,
    )


def _approach_two(rules, cleaned, fields, transactions, unavailable, model, started):
    if cleaned is None or model is None:
        return _close(rules, "rules_only", rules, None, None, False, started, transactions, unavailable, fields)
    return _from_haiku(rules, cleaned, fields, transactions, unavailable, model, started, None, manual_on_reject=False)


def _approach_three(
    rules,
    cleaned,
    fields,
    transactions,
    unavailable,
    model,
    jev,
    clef,
    threshold,
    started,
):
    if cleaned is None:
        return _close(rules, "rules_only", rules, None, None, False, started, transactions, unavailable, fields)
    jev_answer = call_jev(jev, cleaned, threshold) if jev is not None else None
    if jev_answer is not None and not jev_answer.get("failed"):
        return _after_ranker(
            rules,
            cleaned,
            fields,
            transactions,
            unavailable,
            model,
            started,
            jev_answer,
            None,
            "jev_agreed",
            "jev_ambiguous",
        )
    clef_answer = call_clef(clef, cleaned, threshold) if clef is not None else None
    if clef_answer is not None and not clef_answer.get("failed"):
        return _after_ranker(
            rules,
            cleaned,
            fields,
            transactions,
            unavailable,
            model,
            started,
            jev_answer,
            clef_answer,
            "clef_agreed",
            "clef_ambiguous",
        )
    failed = bool(jev_answer and jev_answer.get("failed")) or bool(clef_answer and clef_answer.get("failed"))
    return _from_haiku(
        rules,
        cleaned,
        fields,
        transactions,
        unavailable,
        model,
        started,
        jev_answer,
        clef_answer=clef_answer,
        manual_on_reject=True,
        quiet_path="model_failed" if failed else "rules_only",
    )


def _after_ranker(
    rules,
    cleaned,
    fields,
    transactions,
    unavailable,
    model,
    started,
    jev_answer,
    clef_answer,
    path_agree,
    path_amb,
):
    answer = clef_answer if clef_answer is not None else jev_answer
    if answer.get(Classification.AMBIGUOUS.value):
        return _manual(rules, path_amb, jev_answer, None, True, started, transactions, unavailable, fields, clef_answer)
    chosen = known_class(str(answer.get("class") or ""))
    if chosen is not None and chosen == rules.classification:
        return _accept_model(
            chosen, path_agree, rules, jev_answer, None, started, transactions, unavailable, fields, clef_answer
        )
    if model is None:
        return _manual(rules, "model_failed", jev_answer, None, True, started, transactions, unavailable, fields, clef_answer)
    reply = call_model(model, cleaned)
    picked = known_class(str(reply.get("class") or "")) if reply.get("accepted") else None
    ranker_class = known_class(str(answer.get("class") or ""))
    allowed = {rules.classification, ranker_class}
    if picked is None or picked not in allowed:
        return _manual(
            rules, "haiku_rejected", jev_answer, reply, True, started, transactions, unavailable, fields, clef_answer
        )
    return _accept_model(
        picked, "haiku_assigned", rules, jev_answer, reply, started, transactions, unavailable, fields, clef_answer
    )


def _from_haiku(
    rules,
    cleaned,
    fields,
    transactions,
    unavailable,
    model,
    started,
    jev_answer,
    *,
    manual_on_reject: bool,
    quiet_path: str = "rules_only",
    clef_answer=None,
):
    """Ask Haiku when a client exists. Otherwise keep the rules class."""
    if model is None:
        called = jev_answer is not None or clef_answer is not None
        return _close(
            rules, quiet_path, rules, jev_answer, None, called, started, transactions, unavailable, fields, clef_answer
        )
    reply = call_model(model, cleaned)
    if reply.get("unclear"):
        decision = Decision(Classification.AMBIGUOUS, (), False)
        return _close(
            decision, "haiku_unclear", rules, jev_answer, reply, True, started, transactions, unavailable, fields, clef_answer
        )
    chosen = known_class(str(reply.get("class") or "")) if reply.get("accepted") else None
    if chosen is None:
        if manual_on_reject:
            return _manual(
                rules, "haiku_rejected", jev_answer, reply, True, started, transactions, unavailable, fields, clef_answer
            )
        return _close(
            rules, "haiku_rejected", rules, jev_answer, reply, True, started, transactions, unavailable, fields, clef_answer
        )
    return _accept_model(
        chosen, "haiku_assigned", rules, jev_answer, reply, started, transactions, unavailable, fields, clef_answer
    )


def _accept_model(chosen, path, rules, jev, haiku, started, transactions, unavailable, fields, clef=None) -> Outcome:
    """Keep a model class only when the labeled authorization allows it."""
    authorization = fields[0]
    accepted = accept_proposed_class(chosen, authorization)
    if accepted is None:
        decision = Decision(Classification.AMBIGUOUS, (), False)
        path = "authorization_conflict"
    else:
        decision = Decision(accepted, (), False)
    return _close(decision, path, rules, jev, haiku, True, started, transactions, unavailable, fields, clef)


def _manual(rules, path, jev, haiku, called, started, transactions, unavailable, fields, clef=None) -> Outcome:
    decision = Decision(Classification.AMBIGUOUS, (), False)
    return _close(decision, path, rules, jev, haiku, called, started, transactions, unavailable, fields, clef)


def _close(decision, path, rules, jev, haiku, called, started, transactions, unavailable, fields, clef=None) -> Outcome:
    authorization, promised, receipt, attachment = fields
    refreshed = Decision(
        decision.classification,
        missing_for(decision.classification, authorization, promised, receipt, attachment),
        decision.conflict,
    )
    guarded, withdrawn = apply_duplicate_guard(refreshed, transactions, unavailable)
    if withdrawn:
        path = "duplicate_unmatched"
    route = choose_routing(guarded.classification, guarded.missing, unavailable)
    return Outcome(
        decision=guarded,
        path=path,
        rules=rules,
        jev=_public_ranker(jev),
        haiku=_public_haiku(haiku),
        called_model=called,
        latency_ms=round((time.perf_counter() - started) * 1000, 3),
        route=route.value,
        clef=_public_ranker(clef),
    )


def _public_ranker(answer: dict | None) -> dict | None:
    if not isinstance(answer, dict):
        return None
    public = {
        "class": answer.get("class"),
        "probabilities": answer.get("probabilities") or {},
        "confidence": answer.get("confidence"),
        "latency_ms": answer.get("latency_ms"),
        "tokens_in": answer.get("tokens_in"),
        "tokens_out": answer.get("tokens_out"),
        "cost": answer.get("cost") or {"status": "unpriced", "usd": None},
        "model": answer.get("model") or "",
    }
    if answer.get("failed"):
        public["failure"] = failure_kind(LLMError(str(answer.get("failure") or "")))
    return public


def _public_haiku(reply: dict | None) -> dict | None:
    if not isinstance(reply, dict):
        return None
    reason = reply.get("reason") if isinstance(reply.get("reason"), str) else ""
    if reply.get("unusable"):
        reason = ""
    start = reply.get("evidence_start")
    end = reply.get("evidence_end")
    digest = reply.get("evidence_sha256") if isinstance(reply.get("evidence_sha256"), str) else ""
    public = {
        "class": reply.get("class"),
        "reason": reason,
        "evidence_ok": bool(reply.get("evidence_ok")) and not bool(reply.get("unusable")),
        "evidence_sha256": digest,
        "evidence_start": start if isinstance(start, int) else None,
        "evidence_end": end if isinstance(end, int) else None,
        "unusable": bool(reply.get("unusable")),
        "latency_ms": reply.get("latency_ms"),
        "tokens_in": reply.get("tokens_in"),
        "tokens_out": reply.get("tokens_out"),
        "cost": reply.get("cost") or {"status": "unpriced", "usd": None},
        "model": reply.get("model") or "",
    }
    if reply.get("failure"):
        public["failure"] = failure_kind(LLMError(str(reply.get("failure"))))
    return public
