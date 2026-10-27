"""Model classes are checked against the labeled authorization."""

import json
from pathlib import Path

import httpx
import pytest

from dispute_copilot.classifiers.eligibility import accept_proposed_class, model_allowed
from dispute_copilot.classifiers.assign import assign_approach
from dispute_copilot.classifiers.config import ClassifierSettings
from dispute_copilot.classifiers.guard import apply_duplicate_guard, duplicate_pair
from dispute_copilot.classifiers.haiku import quote_found
from dispute_copilot.classifiers.engine import run_approach
from dispute_copilot.classifiers.jev import JevClient, build_jev, call_jev, read_answer
from dispute_copilot.llm.base import LLMError
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


def test_evidence_must_be_exact_complaint_text():
    complaint = "Please review the posted amount."
    assert quote_found(complaint, complaint) is True
    assert quote_found("Please   review\nthe posted amount.", complaint) is True
    assert quote_found("Please look at the posted amount.", complaint) is False
    prompt = (Path(__file__).resolve().parents[2] / "prompts" / "classifier" / "v1.md").read_text(encoding="utf-8")
    assert "exact text copied from the complaint" in prompt
    assert "Do not paraphrase" in prompt


def test_a_service_class_is_not_kept_for_an_unknown_authorization_label():
    assert accept_proposed_class(Classification.SERVICE_NOT_RECEIVED, "") is None
    assert accept_proposed_class(Classification.AMBIGUOUS, AUTH_NOT_SUPPLIED) == Classification.AMBIGUOUS
    assert model_allowed(Decision(Classification.UNAUTHORIZED, (), True), AUTH_NOT_SUPPLIED) is False
    assert model_allowed(Decision(Classification.DUPLICATE_CHARGE, (), False), AUTH_NOT_SUPPLIED) is False


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


def test_the_ranker_sends_questions_as_a_choice_map():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "answers": {
                    "class": {
                        "type": "choice",
                        "choice": "unauthorized",
                        "probabilities": {"unauthorized": 0.91, "ambiguous_or_unclear": 0.09},
                        "confidence": 0.91,
                    }
                }
            },
        )

    client = JevClient(
        base_url="https://ranker.example/v1/systemone",
        api_key="k",
        model="jev-latest",
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )
    client.ask("I do not recognize this purchase.")
    questions = captured["body"]["questions"]
    assert isinstance(questions, dict)
    question = questions["class"]
    assert question["type"] == "choice"
    assert isinstance(question["criteria"], dict)
    assert "unauthorized" in question["criteria"]
    assert "ambiguous_or_unclear" in question["criteria"]


def test_ranker_setup_and_bad_replies_fail_closed(monkeypatch):
    try:
        JevClient(base_url=" ", api_key="k", model="m", timeout_s=1)
    except LLMError:
        pass
    else:
        raise AssertionError("an empty ranker url was accepted")
    try:
        JevClient(base_url="https://ranker.example", api_key="", model="m", timeout_s=1)
    except LLMError:
        pass
    else:
        raise AssertionError("an empty ranker key was accepted")

    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    assert build_jev() is None
    monkeypatch.setenv("JEV_BASE_URL", "https://ranker.example")
    monkeypatch.setenv("JEV_API_KEY", "k")
    monkeypatch.setenv("JEV_MODEL", "jev-latest")
    assert build_jev() is not None

    def timeout(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.TimeoutException("slow")

    def connect(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("down")

    def status(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"error": "nope"})

    def not_json(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="nope")

    def not_object(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=["x"])

    for handler in (timeout, connect, status, not_json, not_object):
        client = JevClient(
            base_url="https://ranker.example",
            api_key="k",
            model="m",
            timeout_s=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            client.ask("state")
        except LLMError:
            continue
        raise AssertionError("a bad ranker reply was accepted")


def test_ranker_answers_without_probabilities_fail():
    try:
        read_answer({}, 0.7)
    except LLMError:
        pass
    else:
        raise AssertionError("a missing choice was accepted")
    try:
        read_answer({"answers": {"class": {"probabilities": {}}}}, 0.7)
    except LLMError:
        pass
    else:
        raise AssertionError("empty probabilities were accepted")
    try:
        read_answer({"answers": {"class": {"probabilities": {1: 0.9}}}}, 0.7)
    except LLMError:
        pass
    else:
        raise AssertionError("non-string probability keys were accepted")


def test_a_failed_ranker_call_is_ambiguous():
    class BoomJev:
        model = "m"

        def ask(self, state: str) -> dict:
            del state
            raise LLMError("down")

    parsed = call_jev(BoomJev(), "I do not recognize this purchase.", 0.7)
    assert parsed["failed"] is True
    assert parsed["class"] is None
    assert parsed["ambiguous"] is True


def test_an_unclear_haiku_reply_does_not_reassign():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("2", rules, WORDING, AUTH_NOT_SUPPLIED, model=ScriptedModel("multiple_or_unclear", WORDING))
    assert outcome.path == "haiku_unclear"
    assert outcome.decision.classification == Classification.AMBIGUOUS


def test_a_missing_haiku_quote_keeps_the_rules_class():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("2", rules, WORDING, AUTH_NOT_SUPPLIED, model=ScriptedModel("unauthorized", "not in the complaint"))
    assert outcome.path == "haiku_rejected"
    assert outcome.decision.classification == Classification.UNAUTHORIZED


def test_approach_two_without_a_model_stays_on_rules():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("2", rules, WORDING, AUTH_NOT_SUPPLIED, model=None)
    assert outcome.path == "haiku_rejected"
    assert outcome.called_model is False


def test_approach_three_without_a_ranker_goes_to_manual_review():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("3", rules, WORDING, AUTH_NOT_SUPPLIED, jev=None)
    assert outcome.path == "model_failed"
    assert outcome.decision.classification == Classification.AMBIGUOUS


def test_an_ambiguous_ranker_choice_goes_to_manual_review():
    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    outcome = _run("3", rules, WORDING, AUTH_NOT_SUPPLIED, jev=ScriptedJev("ambiguous_or_unclear"))
    assert outcome.path == "jev_ambiguous"


def test_a_failed_ranker_goes_to_manual_review():
    class BoomJev:
        model = "m"
        calls = 0

        def ask(self, state: str) -> dict:
            self.calls += 1
            raise LLMError("down")

    rules = _rules(WORDING, AUTH_NOT_SUPPLIED)
    jev = BoomJev()
    outcome = _run("3", rules, WORDING, AUTH_NOT_SUPPLIED, jev=jev)
    assert jev.calls == 1
    assert outcome.path == "model_failed"


def test_zero_assignment_weights_stay_on_approach_one():
    settings = ClassifierSettings(
        jev_threshold=0.7,
        jev_model="jev-latest",
        jev_timeout_s=1.0,
        haiku_timeout_s=1.0,
        ab_seed=1,
        ab_weights={"1": 0.0, "2": 0.0, "3": 0.0},
        min_sample=1,
    )
    assert assign_approach("CARD-D01", settings) == "1"


def test_duplicate_guard_ignores_rows_that_are_not_charges():
    assert duplicate_pair(None) is False
    assert duplicate_pair({"transactions": "nope"}) is False
    assert duplicate_pair({"transactions": [{"merchant_id": "M"}]}) is False
    decision, withdrawn = apply_duplicate_guard(
        Decision(Classification.UNAUTHORIZED, (), False),
        {"transactions": []},
        False,
    )
    assert withdrawn is False
    assert decision.classification == Classification.UNAUTHORIZED
