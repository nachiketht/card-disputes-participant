"""The decision order is the rule. A later signal does not override an earlier row."""

import pytest

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


@pytest.mark.parametrize(
    "complaint",
    [
        "The cash not dispensed.",
        "No cash came out.",
        "Cash did not come out.",
        "Cash didn't come out.",
        "Cash was not released.",
        "No money was dispensed.",
        "The withdrawal was not paid.",
        "The machine failed to dispense cash.",
        "The cash machine did not dispense.",
        "The cashpoint did not dispense.",
        "It did not dispense cash.",
        "It didn't dispense cash.",
    ],
)
def test_added_cash_phrases_are_unsupported(complaint: str):
    decision = classify(complaint, AUTH_CONFIRMED, [], "2026-08-20", "attached", None)
    assert decision.classification == Classification.UNSUPPORTED
    assert decision.missing == ()


@pytest.mark.parametrize(
    "complaint",
    [
        "I do not recognise this purchase.",
        "I don't recognise this purchase.",
        "This is not my purchase.",
        "This is not my transaction.",
        "I did not make this purchase.",
        "I didn't make this purchase.",
        "I never authorized this.",
        "I never authorised this.",
        "This is an unauthorized charge.",
        "This is an unauthorised charge.",
        "I do not recall this charge.",
        "I don't recall this charge.",
    ],
)
def test_added_recognition_phrases_ask_only_for_authorization(complaint: str):
    decision = classify(complaint, AUTH_NOT_SUPPLIED, [], None, None, None)
    assert decision.classification == Classification.UNAUTHORIZED
    assert decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)


@pytest.mark.parametrize(
    "complaint",
    [
        "I was charged twice.",
        "I was billed twice.",
        "It posted twice.",
        "There is a double charge.",
        "I was double charged.",
        "There is a duplicate charge.",
        "There is a second charge.",
        "They charged me twice.",
        "It is the same purchase twice.",
        "It posted two times.",
        "There is a duplicate transaction.",
        "They billed me twice.",
    ],
)
def test_added_duplicate_phrases_are_duplicate_charges(complaint: str):
    decision = classify(complaint, AUTH_CONFIRMED, [], None, None, None)
    assert decision.classification == Classification.DUPLICATE_CHARGE
    assert decision.missing == ()


@pytest.mark.parametrize(
    "complaint",
    [
        "I paid for a service. It has not been supplied.",
        "The service wasn't delivered.",
        "The service was never delivered.",
        "The service was never supplied.",
        "The service wasn't supplied.",
        "I have not received the service.",
        "I haven't received the service.",
        "I did not receive the service.",
        "I didn't receive the service.",
        "I never received the service.",
        "The service did not arrive.",
        "The goods were never delivered.",
        "The merchandise was not delivered.",
        "The item was never delivered.",
        "The shipment did not arrive.",
        "The order was never supplied.",
        "The delivery was never delivered.",
    ],
)
def test_added_service_phrases_are_service_not_received(complaint: str):
    decision = classify(complaint, AUTH_CONFIRMED, [], "2026-08-20", "attached", None)
    assert decision.classification == Classification.SERVICE_NOT_RECEIVED
    assert decision.missing == ()


def test_nearby_wording_stays_unclassified_or_on_the_earlier_row():
    cash = classify("I paid cash for a service.", AUTH_CONFIRMED, [], None, None, None)
    assert cash.classification == Classification.AMBIGUOUS
    refund = classify("I did not receive a refund.", AUTH_CONFIRMED, [], None, None, None)
    assert refund.classification == Classification.AMBIGUOUS
    late = classify("The item arrived on time.", AUTH_CONFIRMED, [], None, None, None)
    assert late.classification == Classification.AMBIGUOUS
    owned = classify("This is not my purchase.", AUTH_CONFIRMED, [], None, None, None)
    assert owned.classification == Classification.AMBIGUOUS
    again = classify("Please look at this purchase again.", AUTH_CONFIRMED, [], None, None, None)
    assert again.classification == Classification.AMBIGUOUS
    earlier = classify(
        "I was charged twice for a service that was not delivered.",
        AUTH_CONFIRMED,
        [],
        None,
        None,
        None,
    )
    assert earlier.classification == Classification.DUPLICATE_CHARGE


def test_manual_review_beats_a_request_but_the_missing_fields_remain():
    missing = (MissingId.PROMISED_SERVICE_DATE,)
    assert choose_routing(Classification.SERVICE_NOT_RECEIVED, missing, True) == Routing.MANUAL_REVIEW
    assert choose_routing(Classification.SERVICE_NOT_RECEIVED, missing, False) == Routing.REQUEST_INFORMATION
    assert choose_routing(Classification.UNSUPPORTED, (), False) == Routing.MANUAL_REVIEW
