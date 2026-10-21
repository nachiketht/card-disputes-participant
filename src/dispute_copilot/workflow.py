"""Draft one case when the evidence supports a denied card purchase.

This checkpoint does not classify the other dispute types. A follow-up, a
different authorization value, a date with no policy, or a transaction service
that does not return the named purchase stops the case. Nothing is written for
that case, and no amount is filled in from somewhere else.

The draft's amount, currency, and any policy-period note come from the tool
payload. Merchant prose is not copied. The display name is.
"""

import json
from datetime import date

from dispute_copilot.adapters.case_system import FakeCaseSystem, HttpCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus, MemoryCorpus
from dispute_copilot.adapters.tools import FakeToolClient, HttpToolClient
from dispute_copilot.contract import validate_draft
from dispute_copilot.errors import LeakError, OutsideCheckpoint, ToolFailed
from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import (
    AUTH_DENIED,
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
    Routing,
)
from dispute_copilot.retrieval.policy import PolicyResolver, deadline_line
from dispute_copilot.rules.deadline import add_calendar_days
from dispute_copilot.rules.documents import line_starting, parse_intake
from dispute_copilot.serialization import citation_source_text

Corpus = FileCorpus | MemoryCorpus
Tools = HttpToolClient | FakeToolClient
Ledger = HttpCaseSystem | FakeCaseSystem


def prepare(case_id: str, *, corpus: Corpus, tools: Tools, case_system: Ledger, case: dict | None = None) -> dict:
    if case is None:
        case = _find_case(tools.list_cases(), case_id)
    _require_case_shape(case_id, case)
    intake_id = str(case["intake_source"])
    extra = [item for item in case["documents"] if item != intake_id and not str(item).endswith("-ATTACHMENT")]
    try:
        intake_text = corpus.get(intake_id)
    except KeyError:
        raise OutsideCheckpoint(f"{case_id} intake {intake_id} is not in the document corpus") from None
    try:
        intake = parse_intake(intake_text)
    except ValueError as exc:
        raise OutsideCheckpoint(f"{case_id} intake could not be read: {exc}") from exc
    transactions, transaction_attempts = tools.transactions(case_id)
    merchant_note, merchant_attempts = _merchant(tools, case_id)
    if extra:
        joined = ", ".join(str(item) for item in extra)
        raise OutsideCheckpoint(
            f"{case_id} has another customer statement ({joined}). "
            "Conflict handling is not in this checkpoint, so nothing was written."
        )
    authorization = intake["authorization"]
    if authorization != AUTH_DENIED:
        raise OutsideCheckpoint(
            f"{case_id} authorization is {authorization!r}. "
            f"This checkpoint only drafts {AUTH_DENIED!r}, so nothing was written."
        )
    reported = _reported_date(case_id, intake.get(FACT_REPORTED_DATE))
    transaction_id = intake.get(FACT_TRANSACTION_ID)
    if not isinstance(transaction_id, str) or not transaction_id:
        raise OutsideCheckpoint(f"{case_id} intake has no transaction id")
    try:
        resolver = PolicyResolver(corpus)
        selection = resolver.select(reported)
    except (ValueError, KeyError) as exc:
        raise OutsideCheckpoint(f"{case_id} policy lookup failed: {exc}") from exc
    if not selection.in_range or selection.policy_id is None or selection.policy_text is None:
        raise OutsideCheckpoint(
            f"{case_id} reported date {reported.isoformat()} is outside every loaded policy. Nothing was written."
        )
    named = _named_transaction(case_id, transactions, transaction_id)
    amount = named[FACT_AMOUNT]
    currency = named[FACT_CURRENCY]
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not isinstance(currency, str):
        raise OutsideCheckpoint(f"{case_id} named transaction has no usable amount and currency")
    days = selection.days[Classification.UNAUTHORIZED]
    deadline = add_calendar_days(reported, days).isoformat()
    policy_quote = deadline_line(selection.policy_text, Classification.UNAUTHORIZED)
    routing_quote = line_starting(selection.procedure_text, "Routing precedence:")
    if routing_quote is None:
        raise OutsideCheckpoint(f"{case_id} procedure has no routing line to cite")
    source_id = transactions.get(CITATION_SOURCE_ID)
    if not isinstance(source_id, str) or not source_id:
        raise OutsideCheckpoint(f"{case_id} transaction payload has no source id")
    amount_quote = '"' + FACT_AMOUNT + '": ' + _json_number(amount)
    transaction_text = citation_source_text(transactions)
    citations = [
        _cite(DEADLINE, selection.policy_id, policy_quote, selection.policy_text),
        _cite(ROUTING, selection.procedure_id, routing_quote, selection.procedure_text),
        _cite("customer_evidence", intake_id, intake["complaint"], intake_text),
        _cite("transaction_amount", source_id, amount_quote, transaction_text),
    ]
    existing = case_system.get_record(case_id)
    narrative = _narrative(
        classification=Classification.UNAUTHORIZED,
        routing=Routing.READY_FOR_REVIEW,
        transaction_id=transaction_id,
        reported=reported.isoformat(),
        deadline=deadline,
        transaction_attempts=transaction_attempts,
        merchant_attempts=merchant_attempts,
        merchant_note=merchant_note,
        ledger_id=existing.record_id if existing.found else None,
        posted_split=_posted_split(named, resolver, selection.policy_id),
    )
    payload = {
        CASE_ID: case_id,
        CLASSIFICATION: Classification.UNAUTHORIZED,
        ROUTING: Routing.READY_FOR_REVIEW,
        POLICY_IDS: [selection.policy_id, selection.procedure_id],
        FACTS: {
            FACT_TRANSACTION_ID: transaction_id,
            FACT_AMOUNT: amount,
            FACT_CURRENCY: currency,
            FACT_REPORTED_DATE: reported.isoformat(),
        },
        MISSING_INFORMATION: [],
        DEADLINE: deadline,
        CITATIONS: citations,
        FOLLOW_UP_QUESTIONS: [],
        NARRATIVE: narrative,
    }
    draft = validate_draft(payload)
    rendered = json.dumps(draft, ensure_ascii=False)
    Redactor().assert_clean(rendered)
    return draft


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


def _reported_date(case_id: str, raw: object) -> date:
    if not isinstance(raw, str):
        raise OutsideCheckpoint(f"{case_id} intake has no reported date")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise OutsideCheckpoint(f"{case_id} reported date is not an ISO date") from None


def _named_transaction(case_id: str, payload: dict, transaction_id: str) -> dict:
    rows = payload.get("transactions")
    if not isinstance(rows, list):
        raise OutsideCheckpoint(f"{case_id} transaction payload has no transaction list")
    for row in rows:
        if isinstance(row, dict) and row.get(FACT_TRANSACTION_ID) == transaction_id:
            return row
    raise OutsideCheckpoint(
        f"{case_id} named transaction {transaction_id} was not returned. Nothing else was substituted."
    )


def _merchant(tools: Tools, case_id: str) -> tuple[str, int]:
    try:
        payload, attempts = tools.merchant(case_id)
    except ToolFailed as exc:
        return "Merchant context is unavailable.", exc.attempts
    display = payload.get("display_name")
    if not isinstance(display, str) or not display.strip():
        return "Merchant context is unavailable.", attempts
    return f"Merchant display name from the tool: {display.strip()}.", attempts


def _posted_split(row: dict, resolver: PolicyResolver, selected_id: str) -> str:
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


def _cite(claim: str, source_id: str, quote: str, source_text: str) -> dict:
    if quote not in source_text:
        raise OutsideCheckpoint(f"quote for {claim} is not in {source_id}")
    if Redactor().contains_restricted(quote):
        raise LeakError(f"quote for {claim} contains a restricted value")
    return {CITATION_CLAIM: claim, CITATION_SOURCE_ID: source_id, CITATION_QUOTE: quote}


def _json_number(amount: int | float) -> str:
    return json.dumps(amount)


def _narrative(
    *,
    classification: Classification,
    routing: Routing,
    transaction_id: str,
    reported: str,
    deadline: str,
    transaction_attempts: int,
    merchant_attempts: int,
    merchant_note: str,
    ledger_id: str | None,
    posted_split: str,
) -> str:
    if ledger_id:
        ledger = (
            f"Ledger at run time already has record {ledger_id}. "
            "That lookup is not the authoritative create check."
        )
    else:
        ledger = "Ledger at run time has no record for this case. The authoritative check is create-record."
    return (
        f"Case summary: {classification.value} complaint for {transaction_id}, reported {reported}. "
        f"Evidence reviewed: transactions tool attempts {transaction_attempts}; "
        f"merchant tool attempts {merchant_attempts}. {merchant_note} "
        "Missing or conflicting: none in the single customer statement. "
        f"Recommendation: routing {routing.value}. The supplied training-policy deadline is {deadline}. "
        f"Uncertainty and limitations: template text only, with no model. {ledger}{posted_split} "
        "Customer message draft: We have prepared your complaint for review. "
        "We cannot confirm an outcome. No case has been created yet; human approval remains required."
    )
