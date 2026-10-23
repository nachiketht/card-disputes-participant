"""Model boundary: one provider, one hop back to templates, scored fields untouched."""

import json
from pathlib import Path

import httpx

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import FakeToolClient
from dispute_copilot.drafting import Drafter
from dispute_copilot.fields import CLASSIFICATION
from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.null import NullProvider
from dispute_copilot.llm.openai_compat import HAIKU_MODEL, OpenAIProvider
from dispute_copilot.llm.prompts import PromptRegistry
from dispute_copilot.workflow import prepare

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "dev"


class _Boom:
    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        raise TimeoutError("too slow")


class _Scripted:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        self.calls += 1
        return Completion(self.text, 2, 3, "fake")


def _variables() -> dict[str, str]:
    return {
        "classification": "unauthorized",
        "routing": "ready_for_review",
        "transaction_id": "TX-1",
        "reported_date": "2026-10-01",
        "deadline": "2026-10-31",
        "policy_id": "CARD-POLICY-V2",
        "missing_fields": "none",
        "transaction_attempts": "1",
        "quotes": "[]",
    }


def test_a_provider_timeout_falls_back_to_templates_in_one_hop():
    drafter = Drafter(_Boom(), NullProvider())
    message = drafter.customer_message(_variables())
    assert message.downgraded is True
    assert "cannot confirm an outcome" in message.text


def test_a_second_model_cannot_be_the_fallback():
    try:
        Drafter(NullProvider(), _Scripted("nope"))  # type: ignore[arg-type]
    except TypeError:
        return
    raise AssertionError("a non-template fallback was accepted")


def test_guard_rejection_uses_templates():
    drafter = Drafter(_Scripted("You will receive a full refund tomorrow."), NullProvider())
    message = drafter.customer_message(_variables())
    assert message.downgraded is True
    assert rejects("full refund")
    assert "refund" not in message.text.casefold()


def test_unknown_prompt_variable_is_rejected():
    registry = PromptRegistry()
    variables = _variables()
    variables["complaint"] = "raw prose"
    try:
        registry.render("drafter", variables)
    except ValueError:
        return
    raise AssertionError("raw prose variable was rendered")


def test_haiku_request_uses_the_bedrock_messages_api():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        assert request.headers.get("x-api-key") == "test-key"
        assert "authorization" not in {name.lower() for name in request.headers}
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Draft for human review: a person still has to review this."}],
                "usage": {"input_tokens": 5, "output_tokens": 6},
            },
        )

    provider = OpenAIProvider(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com/anthropic",
        model=HAIKU_MODEL,
        api_key="test-key",
        style="anthropic",
        transport=httpx.MockTransport(handler),
    )
    completion = provider.complete(system="system", user="user")
    assert seen["body"]["model"] == HAIKU_MODEL
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert seen["url"].endswith("/anthropic/v1/messages")
    assert completion.tokens_in == 5
    assert completion.text.startswith("Draft for human review:")


def test_provider_http_error_is_an_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    provider = OpenAIProvider(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com/anthropic",
        model=HAIKU_MODEL,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    try:
        provider.complete(system="system", user="user")
    except LLMError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("an http error was treated as prose")


def test_model_prose_does_not_change_scored_fields():
    scripted = _Scripted("Draft for human review: a person still has to review this complaint before any case exists.")
    case_id = "CARD-D01"
    payload = {
        "source_id": f"{case_id}-TRANSACTIONS",
        "transactions": [
            {
                "transaction_id": "TX-D01",
                "amount": 117,
                "currency": "XCR",
                "merchant_id": "MER-MORROW",
                "posted_date": "2026-08-19",
            }
        ],
    }
    merchant = {"source_id": f"{case_id}-MERCHANT", "display_name": "Morrow Supplies"}
    draft = prepare(
        case_id,
        corpus=FileCorpus(DATA),
        tools=FakeToolClient(
            cases=[],
            transactions={case_id: [(200, payload)]},
            merchants={case_id: [(200, merchant)]},
        ),
        case_system=FakeCaseSystem(),
        case={
            "case_id": case_id,
            "intake_source": f"{case_id}-INTAKE",
            "documents": [f"{case_id}-INTAKE", f"{case_id}-ATTACHMENT"],
        },
        drafter=Drafter(scripted, NullProvider()),
    )
    assert draft[CLASSIFICATION] == "unauthorized"
    assert draft["facts"]["amount"] == 117
    assert "configured model" in draft["narrative"]
    assert scripted.calls == 1
