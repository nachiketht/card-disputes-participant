"""Model classes are checked against the labeled authorization."""

import json

import pytest

from dispute_copilot.classifiers.eligibility import accept_proposed_class
from dispute_copilot.classifiers.engine import run_approach
from dispute_copilot.fields import (
    AUTH_CONFIRMED,
    AUTH_DENIED,
    AUTH_NOT_SUPPLIED,
    Classification,
    MissingId,
    Routing,
)
from dispute_copilot.llm.base import Completion
from dispute_copilot.rules.classify import Decision, classify

NEUTRAL = "Please review the posted amount."
WORDING = "I do not recognize this purchase."
PROMISED = "2026-08-20"
RECEIPT = "attached"


class ScriptedModel:
    def __init__(self, label: str, evidence: str) -> None:
        self.label = label
        self.evidence = evidence
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        self.calls += 1
        body = {
            "class": self.label,
            "reason": "The complaint supports one class.",
            "evidence": self.evidence,
        }
        return Completion(text=json.dumps(body), tokens_in=4, tokens_out=8, model="scripted")


class ScriptedJev:
    def __init__(self, label: str) -> None:
        self.model = "scripted-jev"
        self.label = label
        self.calls = 0

    def ask(self, state: str) -> dict:
        self.calls += 1
        return {
            "answers": {
                "class": {
                    "probabilities": {self.label: 0.91, "ambiguous_or_unclear": 0.09},
                    "confidence": 0.91,
                    "choice": self.label,
                }
            }
        }


class Boom:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        self.calls += 1
        raise AssertionError("model was called")

    def ask(self, state: str) -> dict:
        self.calls += 1
        raise AssertionError("ranker was called")


def _rules(complaint: str, authorization: str, promised: str | None = None, receipt: str | None = None) -> Decision:
    return classify(complaint, authorization, [], promised, receipt, None)


def _run(
    approach: str,
    rules: Decision,
    complaint: str,
    authorization: str,
    *,
    model=None,
    jev=None,
    promised: str | None = None,
    receipt: str | None = None,
):
    return run_approach(
        approach=approach,
        rules=rules,
        complaint=complaint,
        authorization=authorization,
        promised_service_date=promised,
        purchase_receipt=receipt,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=model,
        jev=jev,
    )


def test_proposed_class_follows_the_labeled_authorization():
    assert accept_proposed_class(Classification.UNAUTHORIZED, AUTH_NOT_SUPPLIED) == Classification.UNAUTHORIZED
    assert accept_proposed_class(Classification.UNAUTHORIZED, AUTH_DENIED) == Classification.UNAUTHORIZED
    assert accept_proposed_class(Classification.UNAUTHORIZED, AUTH_CONFIRMED) is None
    assert accept_proposed_class(Classification.SERVICE_NOT_RECEIVED, AUTH_CONFIRMED) == Classification.SERVICE_NOT_RECEIVED
    assert (
        accept_proposed_class(Classification.SERVICE_NOT_RECEIVED, AUTH_NOT_SUPPLIED)
        == Classification.SERVICE_NOT_RECEIVED
    )
    assert accept_proposed_class(Classification.DUPLICATE_CHARGE, AUTH_CONFIRMED) == Classification.DUPLICATE_CHARGE
    assert accept_proposed_class(Classification.DUPLICATE_CHARGE, AUTH_NOT_SUPPLIED) == Classification.DUPLICATE_CHARGE


@pytest.mark.parametrize(
    "label",
    [item for item in Classification if item != Classification.UNAUTHORIZED],
)
def test_a_denied_label_asserts_on_every_other_class(label: Classification):
    with pytest.raises(AssertionError):
        accept_proposed_class(label, AUTH_DENIED)


def test_rules_only_unauthorized_from_wording_asks_for_confirmation():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("1", rules, WORDING, AUTH_NOT_SUPPLIED)
    assert outcome.path == "rules_only"
    assert outcome.called_model is False
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)


def test_rules_only_service_with_no_authorization_asks_for_it_first():
    rules = _rules(
        "I paid for a service. It has not been delivered.",
        AUTH_NOT_SUPPLIED,
        None,
        None,
    )
    complaint = "I paid for a service. It has not been delivered."
    outcome = _run("1", rules, complaint, AUTH_NOT_SUPPLIED)
    assert outcome.decision.classification == Classification.SERVICE_NOT_RECEIVED
    assert outcome.decision.missing[0] == MissingId.AUTHORIZATION_CONFIRMATION
    assert MissingId.PROMISED_SERVICE_DATE in outcome.decision.missing
    assert MissingId.PURCHASE_RECEIPT in outcome.decision.missing


@pytest.mark.parametrize("approach", ["2", "3"])
def test_model_agreement_keeps_unauthorized_and_the_confirmation_field(approach: str):
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    model = ScriptedModel(Classification.UNAUTHORIZED.value, WORDING)
    jev = ScriptedJev(Classification.UNAUTHORIZED.value)
    watcher = Boom()
    outcome = _run(
        approach,
        rules,
        WORDING,
        AUTH_NOT_SUPPLIED,
        model=model if approach == "2" else watcher,
        jev=jev if approach == "3" else None,
    )
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)
    if approach == "2":
        assert outcome.path == "haiku_assigned"
        assert model.calls == 1
    else:
        assert outcome.path == "jev_agreed"
        assert jev.calls == 1
        assert watcher.calls == 0


@pytest.mark.parametrize("approach", ["2", "3"])
def test_model_reassigns_into_unauthorized_and_asks_for_confirmation(approach: str):
    rules = _rules(NEUTRAL, AUTH_NOT_SUPPLIED)
    assert rules.classification == Classification.AMBIGUOUS
    outcome = _run(
        approach,
        rules,
        NEUTRAL,
        AUTH_NOT_SUPPLIED,
        model=ScriptedModel(Classification.UNAUTHORIZED.value, NEUTRAL),
        jev=ScriptedJev(Classification.UNAUTHORIZED.value),
    )
    assert outcome.path == "haiku_assigned"
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)
    assert outcome.route == Routing.REQUEST_INFORMATION.value


@pytest.mark.parametrize("approach", ["2", "3"])
def test_model_reassigns_unauthorized_wording_into_service(approach: str):
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    assert rules.classification == Classification.UNAUTHORIZED
    outcome = _run(
        approach,
        rules,
        WORDING,
        AUTH_NOT_SUPPLIED,
        model=ScriptedModel(Classification.SERVICE_NOT_RECEIVED.value, WORDING),
        jev=ScriptedJev(Classification.SERVICE_NOT_RECEIVED.value),
    )
    assert outcome.path == "haiku_assigned"
    assert outcome.decision.classification == Classification.SERVICE_NOT_RECEIVED
    assert outcome.decision.missing[0] == MissingId.AUTHORIZATION_CONFIRMATION
    assert outcome.route == Routing.REQUEST_INFORMATION.value


@pytest.mark.parametrize("approach", ["1", "2", "3"])
def test_labeled_denial_adds_no_confirmation_field(approach: str):
    stale = Decision(Classification.UNAUTHORIZED, (MissingId.AUTHORIZATION_CONFIRMATION,), False)
    model = Boom()
    jev = Boom()
    outcome = _run(approach, stale, WORDING, AUTH_DENIED, model=model, jev=jev)
    assert outcome.path == "rules_only"
    assert outcome.called_model is False
    assert model.calls == 0
    assert jev.calls == 0
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == ()


@pytest.mark.parametrize("approach", ["2", "3"])
def test_confirmed_authorization_rejects_a_model_unauthorized_class(approach: str):
    rules = _rules(NEUTRAL, AUTH_CONFIRMED)
    assert rules.classification == Classification.AMBIGUOUS
    outcome = _run(
        approach,
        rules,
        NEUTRAL,
        AUTH_CONFIRMED,
        model=ScriptedModel(Classification.UNAUTHORIZED.value, NEUTRAL),
        jev=ScriptedJev(Classification.UNAUTHORIZED.value),
    )
    assert outcome.called_model is True
    assert outcome.path == "authorization_conflict"
    assert outcome.decision.classification == Classification.AMBIGUOUS
    assert outcome.decision.missing == ()
    assert outcome.route == Routing.MANUAL_REVIEW.value


@pytest.mark.parametrize("approach", ["2", "3"])
def test_not_supplied_keeps_a_model_unauthorized_class(approach: str):
    rules = _rules(NEUTRAL, AUTH_NOT_SUPPLIED)
    outcome = _run(
        approach,
        rules,
        NEUTRAL,
        AUTH_NOT_SUPPLIED,
        model=ScriptedModel(Classification.UNAUTHORIZED.value, NEUTRAL),
        jev=ScriptedJev(Classification.UNAUTHORIZED.value),
    )
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == (MissingId.AUTHORIZATION_CONFIRMATION,)
    assert outcome.route == Routing.REQUEST_INFORMATION.value


@pytest.mark.parametrize("approach", ["2", "3"])
def test_confirmed_authorization_accepts_a_model_service_class(approach: str):
    rules = _rules(NEUTRAL, AUTH_CONFIRMED, PROMISED, RECEIPT)
    outcome = _run(
        approach,
        rules,
        NEUTRAL,
        AUTH_CONFIRMED,
        model=ScriptedModel(Classification.SERVICE_NOT_RECEIVED.value, NEUTRAL),
        jev=ScriptedJev(Classification.SERVICE_NOT_RECEIVED.value),
        promised=PROMISED,
        receipt=RECEIPT,
    )
    assert outcome.path == "haiku_assigned"
    assert outcome.decision.classification == Classification.SERVICE_NOT_RECEIVED
    assert outcome.decision.missing == ()
    assert outcome.route == Routing.READY_FOR_REVIEW.value


@pytest.mark.parametrize("approach", ["2", "3"])
@pytest.mark.parametrize("label", list(Classification))
def test_a_denied_label_never_reaches_a_model(approach: str, label: Classification):
    rules = _rules(NEUTRAL, AUTH_DENIED)
    assert rules.classification == Classification.UNAUTHORIZED
    model = ScriptedModel(label.value, NEUTRAL)
    jev = ScriptedJev(label.value)
    outcome = _run(approach, rules, NEUTRAL, AUTH_DENIED, model=model, jev=jev)
    assert model.calls == 0
    assert jev.calls == 0
    assert outcome.called_model is False
    assert outcome.path == "rules_only"
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.decision.missing == ()
