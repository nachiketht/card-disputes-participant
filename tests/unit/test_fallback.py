"""The configured model is tried first. Haiku is asked once only when that call cannot be used."""

import json

import httpx

from dispute_copilot.classifiers.engine import run_approach
from dispute_copilot.fields import AUTH_DENIED, AUTH_NOT_SUPPLIED, Classification, Routing
from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.llm.factory import build_critic, build_drafter
from dispute_copilot.llm.fallback import FallbackProvider
from dispute_copilot.llm.schema import ClassifierReply, complete_json
from dispute_copilot.review_flow.agent_review import _ask_critic
from dispute_copilot.rules.classify import classify

WORDING = "I do not recognize this purchase."
CRITIC_REASON = "Citations are present. No outcome is promised."


class Primary:
    def __init__(self, text: str | None = None, error: str | None = None) -> None:
        self.model = "gpt-6 luna"
        self.text = text
        self.error = error
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        self.calls += 1
        if self.error:
            raise LLMError(self.error)
        return Completion(self.text or "", 5, 6, self.model)


class Secondary:
    def __init__(self, text: str | None = None, error: str | None = None) -> None:
        self.model = "claude-haiku-5-5"
        self.text = text
        self.error = error
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        self.calls += 1
        if self.error:
            raise LLMError(self.error)
        return Completion(self.text or "", 2, 3, self.model)


def _class_json(label: str, evidence: str) -> str:
    return json.dumps({"class": label, "reason": "The complaint supports one class.", "evidence": evidence})


def _critic_json() -> str:
    return json.dumps({"verdict": "pass", "reason": CRITIC_REASON})


def test_a_successful_primary_does_not_call_the_fallback():
    primary = Primary(_class_json("unauthorized", WORDING))
    fallback = Secondary(_class_json("service_not_received", WORDING))
    result = complete_json(FallbackProvider(primary, fallback), ClassifierReply, system="s", user="u")
    assert primary.calls == 1
    assert fallback.calls == 0
    assert result.value is not None
    assert result.last.model == "gpt-6 luna"
    assert result.last.fallback_used is False
    assert result.last.primary_failure == ""


def test_a_failed_primary_records_the_fallback_answer():
    primary = Primary(error="provider status 503: secret body and " + WORDING)
    fallback = Secondary(_class_json("unauthorized", WORDING))
    client = FallbackProvider(primary, fallback)
    rules = classify(WORDING, AUTH_NOT_SUPPLIED, [], None, None, None)
    outcome = run_approach(
        approach="2",
        rules=rules,
        complaint=WORDING,
        authorization=AUTH_NOT_SUPPLIED,
        promised_service_date=None,
        purchase_receipt=None,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=client,
    )
    assert primary.calls == 1
    assert fallback.calls == 1
    assert outcome.path == "haiku_assigned"
    assert outcome.haiku["model"] == "claude-haiku-5-5"
    assert outcome.haiku["fallback_used"] is True
    assert outcome.haiku["primary_failure"] == "503"
    saved = json.dumps(outcome.haiku)
    assert "secret body" not in saved
    assert WORDING not in saved


def test_a_failed_primary_and_failed_fallback_keep_the_rules_path():
    primary = Primary(error="provider status 503: secret body")
    fallback = Secondary(error="provider timeout")
    client = FallbackProvider(primary, fallback)
    rules = classify(WORDING, AUTH_NOT_SUPPLIED, [], None, None, None)
    outcome = run_approach(
        approach="2",
        rules=rules,
        complaint=WORDING,
        authorization=AUTH_NOT_SUPPLIED,
        promised_service_date=None,
        purchase_receipt=None,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=client,
    )
    assert fallback.calls == 1
    assert outcome.path == "haiku_rejected"
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.haiku["fallback_used"] is True
    assert outcome.haiku["primary_failure"] == "503"
    assert outcome.haiku["failure"] == "timeout"
    assert outcome.haiku["model"] == ""
    assert "secret body" not in json.dumps(outcome.haiku)


def test_a_schema_miss_asks_the_fallback_once():
    primary = Primary("not json")
    fallback = Secondary(_class_json("unauthorized", WORDING))
    result = complete_json(FallbackProvider(primary, fallback), ClassifierReply, system="s", user="u")
    assert primary.calls == 2
    assert fallback.calls == 1
    assert result.value is not None
    assert result.last.model == "claude-haiku-5-5"
    assert result.last.fallback_used is True
    assert result.last.primary_failure == "unusable"


def test_no_fallback_configuration_returns_the_primary_client(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("AZURE_BASE_URL", "https://primary.example")
    monkeypatch.setenv("LLM_MODEL", "gpt-6 luna")
    monkeypatch.setenv("LLM_API_STYLE", "openai")
    client = build_critic()
    assert client is not None
    assert type(client).__name__ == "OpenAIProvider"
    assert client.model == "gpt-6 luna"
    drafter = build_drafter()
    assert drafter is not None
    assert drafter.primary is client or type(drafter.primary).__name__ == "OpenAIProvider"
    assert drafter.primary.model == "gpt-6 luna"


def test_openai_primary_and_anthropic_fallback_use_separate_calls(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(500, json={"error": {"message": WORDING}})
        body = {
            "content": [{"type": "text", "text": _critic_json()}],
            "usage": {"input_tokens": 4, "output_tokens": 5},
        }
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_API_KEY", "primary-key")
    monkeypatch.setenv("AZURE_BASE_URL", "https://primary.example")
    monkeypatch.setenv("LLM_MODEL", "gpt-6 luna")
    monkeypatch.setenv("LLM_API_STYLE", "openai")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "fallback-key")
    monkeypatch.setenv("LLM_FALLBACK_BASE_URL", "https://fallback.example")
    monkeypatch.setenv("LLM_FALLBACK_API_STYLE", "anthropic")
    monkeypatch.delenv("LLM_FALLBACK_MODEL", raising=False)
    client = build_critic(transport=transport, fallback_transport=transport)
    assert client.model == "gpt-6 luna"
    assert client.fallback.model == "claude-haiku-5-5"
    verdict, reason = _ask_critic({}, client)
    assert verdict == "pass"
    assert reason == CRITIC_REASON
    assert client.last_meta["model"] == "claude-haiku-5-5"
    assert client.last_meta["fallback_used"] is True
    assert client.last_meta["primary_failure"] == "500"
    assert seen[0].endswith("/chat/completions")
    assert seen[1].endswith("/v1/messages")
    assert WORDING not in json.dumps(client.last_meta)


def test_a_ranker_disagreement_can_be_settled_by_the_fallback():
    primary = Primary(error="provider status 400: hidden")
    fallback = Secondary(_class_json("unauthorized", WORDING))
    rules = classify(WORDING, AUTH_NOT_SUPPLIED, [], None, None, None)
    outcome = run_approach(
        approach="3",
        rules=rules,
        complaint=WORDING,
        authorization=AUTH_NOT_SUPPLIED,
        promised_service_date=None,
        purchase_receipt=None,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=FallbackProvider(primary, fallback),
        jev=_Jev("service_not_received"),
    )
    assert outcome.path == "haiku_assigned"
    assert outcome.decision.classification == Classification.UNAUTHORIZED
    assert outcome.haiku["fallback_used"] is True
    assert outcome.haiku["model"] == "claude-haiku-5-5"


def test_the_duplicate_guard_still_runs_after_a_fallback_answer():
    primary = Primary(error="timeout")
    fallback = Secondary(_class_json("duplicate_charge", WORDING))
    rules = classify(WORDING, AUTH_NOT_SUPPLIED, [], None, None, None)
    outcome = run_approach(
        approach="2",
        rules=rules,
        complaint=WORDING,
        authorization=AUTH_NOT_SUPPLIED,
        promised_service_date=None,
        purchase_receipt=None,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=FallbackProvider(primary, fallback),
    )
    assert outcome.path == "duplicate_unmatched"
    assert outcome.decision.classification == Classification.AMBIGUOUS
    assert outcome.route == Routing.MANUAL_REVIEW.value


def test_a_denied_label_does_not_call_either_model():
    primary = Primary(error="timeout")
    fallback = Secondary(_class_json("unauthorized", WORDING))
    rules = classify(WORDING, AUTH_DENIED, [], None, None, None)
    outcome = run_approach(
        approach="2",
        rules=rules,
        complaint=WORDING,
        authorization=AUTH_DENIED,
        promised_service_date=None,
        purchase_receipt=None,
        attachment_status=None,
        transactions={"transactions": []},
        transactions_unavailable=False,
        model=FallbackProvider(primary, fallback),
    )
    assert primary.calls == 0
    assert fallback.calls == 0
    assert outcome.path == "rules_only"
    assert outcome.called_model is False


class _Jev:
    def __init__(self, label: str) -> None:
        self.model = "scripted-jev"
        self.label = label
        self.calls = 0

    def ask(self, state: str) -> dict:
        self.calls += 1
        del state
        return {
            "answers": {
                "class": {
                    "probabilities": {self.label: 0.91, "ambiguous_or_unclear": 0.09},
                    "confidence": 0.91,
                    "choice": self.label,
                }
            }
        }
