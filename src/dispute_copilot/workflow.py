"""Draft one case from the procedure's decision order.

The amount and currency come from the named transaction, or stay empty when
that read fails. They are never taken from the complaint or from a different
transaction. Merchant prose is not copied. The display name is. A date outside
every loaded policy still stops the case.
"""

import json
from datetime import date

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus, MemoryCorpus
from dispute_copilot.adapters.tools import FakeToolClient, HttpToolClient
from dispute_copilot.contract import validate_draft
from dispute_copilot.drafting import Drafter
from dispute_copilot.errors import BreakerOpen, LeakError, OutsideCheckpoint, ToolFailed
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.evidence.untrusted import instruction_flags, payload_flags
from dispute_copilot.fields import (
    CITATION_CLAIM,
    CITATION_QUOTE,
    CITATION_SOURCE_ID,
    CASE_ID,
    CITATIONS,
    CLASSIFICATION,
    Classification,
    DEADLINE,
    FACT_AMOUNT,
    FACT_CURRENCY,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    FACTS,
    FOLLOW_UP_QUESTIONS,
    MISSING_INFORMATION,
    NARRATIVE,
    POLICY_IDS,
    ROUTING,
    MissingId,
    Routing,
)
from dispute_copilot.retrieval.policy import PolicyResolver, PolicySelection, deadline_line
from dispute_copilot.rules.classify import ATTACHMENT_MISSING, Decision, classify
from dispute_copilot.rules.deadline import add_calendar_days
from dispute_copilot.rules.documents import line_starting, parse_attachment, parse_followup, parse_intake
from dispute_copilot.rules.questions import follow_up_questions
from dispute_copilot.rules.routing import choose_routing
from dispute_copilot.observability.tracing import current
from dispute_copilot.serialization import citation_source_text

Corpus = FileCorpus | MemoryCorpus
Tools = HttpToolClient | FakeToolClient
Ledger = HttpCaseSystem | FakeCaseSystem

_NO_DEADLINE = frozenset({Classification.UNSUPPORTED, Classification.AMBIGUOUS})
_PROCEDURE_PREFIX = {
    Classification.UNAUTHORIZED: "Use unauthorized when",
    Classification.DUPLICATE_CHARGE: "Use duplicate_charge for",
    Classification.SERVICE_NOT_RECEIVED: "Use service_not_received for",
    Classification.AMBIGUOUS: "If customer accounts conflict",
    Classification.UNSUPPORTED: "Cash withdrawal disputes are unsupported",
}
_MISSING_PROCEDURE = {
    MissingId.AUTHORIZATION_CONFIRMATION: "use the provisional",
    MissingId.PROMISED_SERVICE_DATE: "If no promised date",
    MissingId.PURCHASE_RECEIPT: "request purchase_receipt",
}
_MISSING_INTAKE = {
    MissingId.AUTHORIZATION_CONFIRMATION: "Authorization: ",
    MissingId.PROMISED_SERVICE_DATE: "Promised service date: ",
    MissingId.PURCHASE_RECEIPT: "Purchase receipt: ",
}


def prepare(
    case_id: str,
    *,
    corpus: Corpus,
    tools: Tools,
    case_system: Ledger,
    case: dict | None = None,
    drafter: Drafter | None = None,
) -> dict:
    tracer = current()
    if case is None:
        case = _find_case(tools.list_cases(), case_id)
    _require_case_shape(case_id, case)
    intake_id = str(case["intake_source"])
    with tracer.span("intake.parse", source=intake_id) as span:
        intake_text = _document(corpus, case_id, intake_id)
        try:
            intake = parse_intake(intake_text)
        except ValueError as exc:
            raise OutsideCheckpoint(f"{case_id} intake could not be read: {exc}") from exc
        span.set(labeled_lines=len(intake))
    _flag_untrusted(intake_id, intake_text)
    with tracer.span("documents.followups") as span:
        followups = _followups(corpus, case_id, case, intake_id)
        attachment_status = _attachment_status(corpus, case_id, case)
        span.set(followups=len(followups), attachment_status=attachment_status)
    for source_id, _authorization, text in followups:
        _flag_untrusted(source_id, text)
    with tracer.span("rules.classify") as span:
        decision = classify(
            intake["complaint"],
            intake["authorization"],
            [item[1] for item in followups],
            intake.get(MissingId.PROMISED_SERVICE_DATE.value),
            intake.get(MissingId.PURCHASE_RECEIPT.value),
            attachment_status,
        )
        span.set(decision=decision.classification, missing_ids=list(decision.missing), conflict=decision.conflict)
    reported = _reported_date(case_id, intake.get(FACT_REPORTED_DATE))
    transaction_id = intake.get(FACT_TRANSACTION_ID)
    if not isinstance(transaction_id, str) or not transaction_id:
        raise OutsideCheckpoint(f"{case_id} intake has no transaction id")
    transactions, transaction_attempts, transactions_down, transactions_skipped = _transactions(tools, case_id)
    merchant_note, merchant_attempts = _merchant(tools, case_id)
    with tracer.span("policy.select", reported=reported.isoformat()) as span:
        try:
            resolver = PolicyResolver(corpus)
            selection = resolver.select(reported)
        except (ValueError, KeyError) as exc:
            raise OutsideCheckpoint(f"{case_id} policy lookup failed: {exc}") from exc
        span.set(policy=selection.policy_id, in_range=selection.in_range)
    if not selection.in_range or selection.policy_id is None or selection.policy_text is None:
        tracer.event("policy.out_of_range", level="warning", reported=reported.isoformat())
        raise OutsideCheckpoint(
            f"{case_id} reported date {reported.isoformat()} is outside every loaded policy. Nothing was written."
        )
    named, amount, currency, transactions_unavailable = _named_facts(transactions, transaction_id, transactions_down)
    with tracer.span("rules.routing") as span:
        routing = choose_routing(decision.classification, decision.missing, transactions_unavailable)
        deadline = _deadline(case_id, decision, selection, reported)
        span.set(route=routing, transactions_unavailable=transactions_unavailable, deadline_set=deadline is not None)
    if transactions_unavailable:
        tracer.event("degradation.applied", level="warning", kind="transactions_unavailable", route=routing)
    with tracer.span("citations.build") as span:
        citations = _citations(
            case_id,
            decision,
            intake_id,
            intake_text,
            intake,
            followups,
            selection,
            deadline,
            transactions,
            transaction_id,
            amount,
            transactions_unavailable,
        )
        span.set(count=len(citations))
    with tracer.span("ledger.lookup") as span:
        existing = case_system.get_record(case_id)
        span.set(found=existing.found, http_status=existing.http_status)
    narrative = _narrative(
        classification=decision.classification,
        routing=routing,
        transaction_id=transaction_id,
        reported=reported.isoformat(),
        deadline=deadline,
        policy_id=selection.policy_id,
        missing=decision.missing,
        conflict=decision.conflict,
        transactions_unavailable=transactions_unavailable,
        transaction_attempts=transaction_attempts,
        merchant_attempts=merchant_attempts,
        merchant_note=merchant_note,
        ledger_id=existing.record_id if existing.found else None,
        posted_split=_posted_split(named, resolver, selection.policy_id),
        transactions_skipped=transactions_skipped,
    )
    # Callers may still pass a drafter. The saved customer sentence stays the template.
    del drafter
    payload = {
        CASE_ID: case_id,
        CLASSIFICATION: decision.classification,
        ROUTING: routing,
        POLICY_IDS: [selection.policy_id, selection.procedure_id],
        FACTS: {
            FACT_TRANSACTION_ID: transaction_id,
            FACT_AMOUNT: amount,
            FACT_CURRENCY: currency,
            FACT_REPORTED_DATE: reported.isoformat(),
        },
        MISSING_INFORMATION: list(decision.missing),
        DEADLINE: deadline,
        CITATIONS: citations,
        FOLLOW_UP_QUESTIONS: follow_up_questions(decision.missing),
        NARRATIVE: narrative,
    }
    with tracer.span("contract.validate"):
        draft = validate_draft(payload)
    with tracer.span("redaction.check"):
        rendered = json.dumps(draft, ensure_ascii=False)
        Redactor().assert_clean(rendered)
    return draft


def _flag_untrusted(source_id: str, text: object, flags: list[str] | None = None) -> None:
    found = instruction_flags(text) if flags is None else flags
    if found:
        current().event(
            "security.untrusted_instruction",
            level="warning",
            source=source_id,
            patterns=found,
            action="treated_as_data",
        )


def _find_case(cases: list[dict], case_id: str) -> dict:
    for item in cases:
        if item.get(CASE_ID) == case_id:
            return item
    raise OutsideCheckpoint(f"{case_id} is not in the mock case list")


def _require_case_shape(case_id: str, case: dict) -> None:
    documents = case.get("documents")
    intake = case.get("intake_source")
    if not isinstance(intake, str) or not isinstance(documents, list) or intake not in documents:
        raise OutsideCheckpoint(f"{case_id} case listing has no intake document list")


def _document(corpus: Corpus, case_id: str, source_id: str) -> str:
    try:
        return corpus.get(source_id)
    except KeyError:
        raise OutsideCheckpoint(f"{case_id} document {source_id} is not in the corpus") from None


def _followups(corpus: Corpus, case_id: str, case: dict, intake_id: str) -> list[tuple[str, str, str]]:
    found = []
    for item in case["documents"]:
        source_id = str(item)
        if source_id == intake_id or source_id.endswith("-ATTACHMENT"):
            continue
        text = _document(corpus, case_id, source_id)
        found.append((source_id, parse_followup(text)["authorization"], text))
    return found


def _attachment_status(corpus: Corpus, case_id: str, case: dict) -> str | None:
    status = None
    for item in case["documents"]:
        source_id = str(item)
        if not source_id.endswith("-ATTACHMENT"):
            continue
        parsed = parse_attachment(_document(corpus, case_id, source_id))
        if parsed.get("attachment_status") == ATTACHMENT_MISSING:
            status = ATTACHMENT_MISSING
    return status


def _reported_date(case_id: str, raw: object) -> date:
    if not isinstance(raw, str):
        raise OutsideCheckpoint(f"{case_id} intake has no reported date")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise OutsideCheckpoint(f"{case_id} reported date is not an ISO date") from None


def _transactions(tools: Tools, case_id: str) -> tuple[dict | None, int, bool, bool]:
    try:
        payload, attempts = tools.transactions(case_id)
    except ToolFailed as exc:
        return None, exc.attempts, True, isinstance(exc, BreakerOpen)
    _flag_untrusted(str(payload.get(CITATION_SOURCE_ID, "transactions")), None, payload_flags(payload))
    return payload, attempts, False, False


def _named_facts(
    payload: dict | None,
    transaction_id: str,
    transactions_down: bool,
) -> tuple[dict | None, int | float | None, str | None, bool]:
    if transactions_down or payload is None:
        return None, None, None, True
    named = _find_named(payload, transaction_id)
    if named is None:
        return None, None, None, True
    amount = named.get(FACT_AMOUNT)
    currency = named.get(FACT_CURRENCY)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not isinstance(currency, str) or not currency:
        return None, None, None, True
    return named, amount, currency, False


def _find_named(payload: dict, transaction_id: str) -> dict | None:
    rows = payload.get("transactions")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get(FACT_TRANSACTION_ID) == transaction_id:
            return row
    return None


def _deadline(case_id: str, decision: Decision, selection: PolicySelection, reported: date) -> str | None:
    if decision.classification in _NO_DEADLINE:
        return None
    try:
        days = selection.days[decision.classification]
    except KeyError:
        raise OutsideCheckpoint(f"{case_id} selected policy has no day count for this classification") from None
    return add_calendar_days(reported, days).isoformat()


def _merchant(tools: Tools, case_id: str) -> tuple[str, int]:
    try:
        payload, attempts = tools.merchant(case_id)
    except ToolFailed as exc:
        return "Merchant context is unavailable.", exc.attempts
    _flag_untrusted(str(payload.get(CITATION_SOURCE_ID, "merchant")), None, payload_flags(payload))
    display = payload.get("display_name")
    if not isinstance(display, str) or not display.strip():
        return "Merchant context is unavailable.", attempts
    return f"Merchant display name from the tool: {display.strip()}.", attempts


def _posted_split(row: dict | None, resolver: PolicyResolver, selected_id: str) -> str:
    if row is None:
        return ""
    raw = row.get("posted_date")
    if not isinstance(raw, str):
        return ""
    try:
        posted = date.fromisoformat(raw)
    except ValueError:
        return ""
    other = resolver.covering_id(posted)
    if other is None or other == selected_id:
        return ""
    return (
        f" The transaction posted date {posted.isoformat()} falls under {other}. "
        f"Selection stays {selected_id} because policy text selects by the reported date."
    )


def _citations(
    case_id: str,
    decision: Decision,
    intake_id: str,
    intake_text: str,
    intake: dict,
    followups: list[tuple[str, str, str]],
    selection: PolicySelection,
    deadline: str | None,
    transactions: dict | None,
    transaction_id: str,
    amount: int | float | None,
    transactions_unavailable: bool,
) -> list[dict]:
    if selection.policy_id is None or selection.policy_text is None:
        raise OutsideCheckpoint(f"{case_id} has no policy text to cite")
    citations: list[dict] = []
    policy_text = selection.policy_text
    procedure_text = selection.procedure_text
    opened = line_starting(policy_text, "Effective from: ")
    if opened is None:
        raise OutsideCheckpoint(f"{case_id} policy has no effective-from line to cite")
    _cite(citations, "policy_selection", selection.policy_id, opened, policy_text)
    if deadline is not None:
        try:
            counted = deadline_line(policy_text, decision.classification)
        except ValueError as exc:
            raise OutsideCheckpoint(str(exc)) from exc
        _cite(citations, DEADLINE, selection.policy_id, counted, policy_text)
    prefix = _PROCEDURE_PREFIX[decision.classification]
    typed = line_starting(procedure_text, prefix)
    if typed is not None:
        _cite(citations, CLASSIFICATION, selection.procedure_id, typed, procedure_text)
    routing_quote = line_starting(procedure_text, "Routing precedence:")
    if routing_quote is None:
        raise OutsideCheckpoint(f"{case_id} procedure has no routing line to cite")
    _cite(citations, ROUTING, selection.procedure_id, routing_quote, procedure_text)
    if transactions_unavailable:
        outage = line_starting(procedure_text, "Never treat service failure")
        if outage is not None:
            _cite(citations, "service_outage", selection.procedure_id, outage, procedure_text)
    _cite(citations, "customer_evidence", intake_id, intake["complaint"], intake_text)
    for label in ("Reported date: ", "Transaction ID: "):
        evidence = line_starting(intake_text, label)
        if evidence is not None:
            _cite(citations, "customer_evidence", intake_id, evidence, intake_text)
    if decision.conflict:
        _cite_authorization(citations, intake_id, intake_text)
        for source_id, _authorization, text in followups:
            _cite_authorization(citations, source_id, text)
    for field in decision.missing:
        procedure_line = line_starting(procedure_text, _MISSING_PROCEDURE[field])
        if procedure_line is not None:
            _cite(citations, MISSING_INFORMATION, selection.procedure_id, procedure_line, procedure_text)
        intake_line = line_starting(intake_text, _MISSING_INTAKE[field])
        if intake_line is not None:
            _cite(citations, MISSING_INFORMATION, intake_id, intake_line, intake_text)
    delivery = intake.get("delivery_note")
    if isinstance(delivery, str) and delivery:
        note = line_starting(intake_text, "Delivery note: ")
        if note is not None:
            _cite(citations, "duplicate_delivery", intake_id, note, intake_text)
    if not transactions_unavailable and transactions is not None and amount is not None:
        _cite_transactions(citations, case_id, decision, transactions, transaction_id, amount)
    if not citations:
        raise OutsideCheckpoint(f"{case_id} has no citation whose quote is in its source")
    return citations


def _cite_authorization(citations: list[dict], source_id: str, text: str) -> None:
    quote = line_starting(text, "Authorization: ")
    if quote is None:
        raise OutsideCheckpoint(f"{source_id} has no authorization line to cite for the conflict")
    _cite(citations, "conflicting_evidence", source_id, quote, text)


def _cite_transactions(
    citations: list[dict],
    case_id: str,
    decision: Decision,
    transactions: dict,
    transaction_id: str,
    amount: int | float,
) -> None:
    source_id = transactions.get(CITATION_SOURCE_ID)
    if not isinstance(source_id, str) or not source_id:
        raise OutsideCheckpoint(f"{case_id} transaction payload has no source id")
    text = citation_source_text(transactions)
    amount_quote = '"' + FACT_AMOUNT + '": ' + json.dumps(amount)
    if amount_quote not in text:
        raise OutsideCheckpoint(f"{case_id} amount quote is not in the transaction payload")
    _cite(citations, "transaction_amount", source_id, amount_quote, text)
    if decision.classification != Classification.DUPLICATE_CHARGE:
        return
    rows = transactions.get("transactions")
    if not isinstance(rows, list):
        return
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        other = row.get(FACT_TRANSACTION_ID)
        if not isinstance(other, str) or other == transaction_id or other in seen:
            continue
        seen.add(other)
        quote = '"' + FACT_TRANSACTION_ID + '": ' + json.dumps(other)
        if quote in text:
            _cite(citations, "duplicate_transaction", source_id, quote, text)


def _cite(citations: list[dict], claim: str, source_id: str, quote: str, source_text: str) -> None:
    if quote not in source_text:
        raise OutsideCheckpoint(f"quote for {claim} is not in {source_id}")
    if Redactor().contains_restricted(quote):
        raise LeakError(f"quote for {claim} contains a restricted value")
    citations.append({CITATION_CLAIM: claim, CITATION_SOURCE_ID: source_id, CITATION_QUOTE: quote})


def _with_customer_message(narrative: str, outcome) -> str:
    marker = "Customer message draft:"
    head, sep, _tail = narrative.partition(marker)
    if not sep:
        return narrative
    text = head + sep + " " + outcome.text
    if outcome.downgraded:
        return text.replace(
            "template text only, with no model.",
            "generated from templates after the model was unavailable.",
        )
    return text.replace(
        "template text only, with no model.",
        "the customer message used the configured model. Scored fields stayed in code.",
    )


def _narrative(
    *,
    classification: Classification,
    routing: Routing,
    transaction_id: str,
    reported: str,
    deadline: str | None,
    policy_id: str,
    missing: tuple[MissingId, ...],
    conflict: bool,
    transactions_unavailable: bool,
    transaction_attempts: int,
    merchant_attempts: int,
    merchant_note: str,
    ledger_id: str | None,
    posted_split: str,
    transactions_skipped: bool = False,
) -> str:
    if missing:
        needed = ", ".join(item.value for item in missing)
        missing_text = f"Fields still needed: {needed}."
    else:
        missing_text = "No required field is missing for this type."
    if conflict:
        conflict_text = "Customer statements disagree about authorization."
    else:
        conflict_text = "Customer statements do not disagree about authorization."
    if transactions_unavailable:
        purchase = (
            "The named purchase was not returned. Amount and currency stay empty. "
            "That is unknown evidence, not an empty history, and no other purchase was substituted."
        )
    else:
        purchase = "The named purchase was returned."
    if transactions_skipped:
        purchase += (
            " The transactions call was skipped because its circuit breaker was open after earlier cases"
            " exhausted every attempt."
        )
    if deadline:
        deadline_text = f"The supplied training-policy deadline is {deadline}."
    else:
        deadline_text = "The supplied training-policy deadline does not apply to this classification."
    if ledger_id:
        ledger = (
            f"Ledger at run time already has record {ledger_id}. "
            "That lookup is not the authoritative create check."
        )
    else:
        ledger = "Ledger at run time has no record for this case. The authoritative check is create-record."
    return (
        f"Case summary: {classification.value} complaint for {transaction_id}, reported {reported}. "
        f"Policy {policy_id} was selected from the reported date. "
        f"Evidence reviewed: transactions tool attempts {transaction_attempts}; "
        f"merchant tool attempts {merchant_attempts}. {purchase} {merchant_note} "
        "Untrusted merchant text was not copied and was not treated as an instruction. "
        f"Missing or conflicting: {missing_text} {conflict_text} "
        f"Recommendation: routing {routing.value}. {deadline_text} "
        f"Uncertainty and limitations: template text only, with no model. {ledger}{posted_split} "
        "Customer message draft: We have prepared your complaint for review. "
        "We cannot confirm an outcome. No case has been created yet; human approval remains required."
    )
