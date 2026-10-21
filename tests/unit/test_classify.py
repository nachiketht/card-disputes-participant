"""The decision order is the rule. A later signal does not override an earlier row."""

from dispute_copilot.fields import AUTH_CONFIRMED, AUTH_DENIED, AUTH_NOT_SUPPLIED, Classification, MissingId, Routing
from dispute_copilot.rules.classify import classify
from dispute_copilot.rules.routing import choose_routing


def test_cash_not_dispensed_beats_a_denied_authorization():
    decision = classify(
        "The ATM did not dispense cash.",
        AUTH_DENIED,
        [],
        "2026-08-20",
        "attached",
        None,
    )
    assert decision.classification == Classification.UNSUPPORTED
    assert decision.missing == ()


def test_a_followup_disagreement_beats_an_unrecognized_purchase():
    decision = classify(
        "I do not recognize this purchase.",
        AUTH_DENIED,
        [AUTH_CONFIRMED],
        AUTH_NOT_SUPPLIED,
        AUTH_NOT_SUPPLIED,
        "missing",
    )
    assert decision.classification == Classification.AMBIGUOUS
    assert decision.conflict is True
    assert decision.missing == ()


def test_a_denied_purchase_beats_a_service_sentence_and_ignores_blank_service_fields():
    decision = classify(
        "I paid for a service. It has not been delivered.",
        AUTH_DENIED,
        [],
        AUTH_NOT_SUPPLIED,
        AUTH_NOT_SUPPLIED,
        "missing",
    )
    assert decision.classification == Classification.UNAUTHORIZED
    assert decision.missing == ()


def test_an_unrecognized_purchase_with_no_authorization_asks_only_for_that():
    decision = classify(
        "I do not recognize this purchase.",
        AUTH_NOT_SUPPLIED,
        [],
        AUTH_NOT_SUPPLIED,
        AUTH_NOT_SUPPLIED,
        "missing",
    )
    assert decision.classification == Classification.UNAUTHORIZED
    assert decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)


def test_a_second_charge_with_no_authorization_does_not_ask_for_one():
    decision = classify(
        "I was charged twice. The statement shows TX-1 and TX-1-B.",
        AUTH_NOT_SUPPLIED,
        [],
        None,
        None,
        None,
    )
    assert decision.classification == Classification.DUPLICATE_CHARGE
    assert decision.missing == ()


def test_two_transaction_ids_are_a_duplicate_without_the_word_twice():
    decision = classify(
        "Please compare TX-9 and TX-9-B.",
        AUTH_CONFIRMED,
        [],
        None,
        None,
        None,
    )
    assert decision.classification == Classification.DUPLICATE_CHARGE


def test_a_service_reads_its_fields_only_after_that_row_matches():
    confirmed = classify(
        "I paid for a service. It has not been delivered.",
        AUTH_CONFIRMED,
        [],
        AUTH_NOT_SUPPLIED,
        "attached",
        "missing",
    )
    assert confirmed.classification == Classification.SERVICE_NOT_RECEIVED
    assert confirmed.missing == (MissingId.PROMISED_SERVICE_DATE, MissingId.PURCHASE_RECEIPT)
    unconfirmed = classify(
        "I paid for a service. It has not been delivered.",
        AUTH_NOT_SUPPLIED,
        [],
        "2026-08-20",
        "attached",
        None,
    )
    assert unconfirmed.missing[0] == MissingId.AUTHORIZATION_CONFIRMATION


def test_manual_review_beats_a_request_but_the_missing_fields_remain():
    missing = (MissingId.PROMISED_SERVICE_DATE,)
    assert choose_routing(Classification.SERVICE_NOT_RECEIVED, missing, True) == Routing.MANUAL_REVIEW
    assert choose_routing(Classification.SERVICE_NOT_RECEIVED, missing, False) == Routing.REQUEST_INFORMATION
    assert choose_routing(Classification.UNSUPPORTED, (), False) == Routing.MANUAL_REVIEW
