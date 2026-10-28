"""Model boundary: one provider, one hop back to templates, scored fields untouched."""

import json
from pathlib import Path

import httpx
import pytest

from dispute_copilot.adapters.case_system import FakeCaseSystem
from dispute_copilot.adapters.corpus import FileCorpus
from dispute_copilot.adapters.tools import FakeToolClient
from dispute_copilot.drafting import Drafter
from dispute_copilot.fields import CLASSIFICATION
from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.null import NullProvider
from dispute_copilot.llm.factory import build_critic, build_drafter
from dispute_copilot.llm.openai_compat import HAIKU_MODEL, OpenAIProvider, default_base_url, resolve_api_key
from dispute_copilot.llm.prompts import PromptRegistry, allowed_variable_names
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


def test_api_key_is_only_the_azure_env_value(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "azure-key")
    monkeypatch.setenv("AWS_API_KEY", "aws-key")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "aws-bearer")
    assert resolve_api_key() == "azure-key"
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    assert default_base_url() == ""
    monkeypatch.setenv("AZURE_BASE_URL", "https://unit.example/anthropic")
    assert default_base_url() == "https://unit.example/anthropic"
    monkeypatch.delenv("LLM_API_KEY")
    assert resolve_api_key() == ""


def test_a_model_call_names_azure_base_url_when_it_is_empty(monkeypatch):
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    try:
        OpenAIProvider(base_url="  ", model=HAIKU_MODEL, api_key="test-key")
    except LLMError as exc:
        assert str(exc) == "AZURE_BASE_URL is empty"
    else:
        raise AssertionError("an empty host was accepted")
    try:
        build_critic()
    except LLMError as exc:
        assert str(exc) == "AZURE_BASE_URL is empty"
    else:
        raise AssertionError("a model client was built without a host")
    try:
        build_drafter()
    except LLMError as exc:
        assert str(exc) == "AZURE_BASE_URL is empty"
    else:
        raise AssertionError("a drafter was built without a host")
    monkeypatch.setenv("LLM_PROVIDER", "null")
    assert build_critic() is None
    assert build_drafter() is None


def test_haiku_request_uses_the_azure_messages_api():
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
        base_url="https://unit.example/anthropic",
        model=HAIKU_MODEL,
        api_key="test-key",
        style="anthropic",
        transport=httpx.MockTransport(handler),
    )
    completion = provider.complete(system="system", user="user")
    assert seen["body"]["model"] == HAIKU_MODEL
    assert seen["body"]["model"] == "claude-haiku-4-5"
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert seen["url"].endswith("/anthropic/v1/messages")
    assert "amazonaws.com" not in seen["url"]
    assert completion.tokens_in == 5
    assert completion.text.startswith("Draft for human review:")


def test_provider_http_error_is_an_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    provider = OpenAIProvider(
        base_url="https://unit.example/anthropic",
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
    assert (
        "Customer message draft: We have prepared your complaint for review. "
        "We cannot confirm an outcome. No case has been created yet; human approval remains required."
    ) in draft["narrative"]
    assert scripted.calls == 0


def test_template_provider_returns_the_fixed_customer_message():
    provider = NullProvider()
    assert provider.customer_message() == provider.complete(system="s", user="u").text
    assert "cannot confirm an outcome" in provider.customer_message()


def test_factory_uses_templates_when_the_key_is_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_BASE_URL", raising=False)
    assert build_critic() is None
    drafter = build_drafter()
    assert drafter is not None
    assert isinstance(drafter.primary, NullProvider)


def test_timeout_env_falls_back_when_it_is_not_a_positive_number(monkeypatch):
    from dispute_copilot.llm.factory import _timeout

    monkeypatch.setenv("LLM_TIMEOUT_S", "not-a-number")
    assert _timeout() == 30.0
    monkeypatch.setenv("LLM_TIMEOUT_S", "-2")
    assert _timeout() == 0.001


def test_openai_style_reads_chat_completions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/chat/completions")
        assert request.headers.get("authorization") == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Draft for human review: a person still has to review this."}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    provider = OpenAIProvider(
        base_url="https://unit.example",
        model="gpt-test",
        api_key="test-key",
        style="openai",
        transport=httpx.MockTransport(handler),
    )
    completion = provider.complete(system="system", user="user")
    assert completion.tokens_in == 3
    assert completion.tokens_out == 4
    assert completion.text.startswith("Draft for human review:")


def test_openai_provider_rejects_a_missing_key_or_model():
    try:
        OpenAIProvider(base_url="https://unit.example", model=HAIKU_MODEL, api_key="")
    except LLMError:
        pass
    else:
        raise AssertionError("an empty key was accepted")
    try:
        OpenAIProvider(base_url="https://unit.example", model="", api_key="k")
    except LLMError:
        pass
    else:
        raise AssertionError("an empty model id was accepted")


def test_provider_timeout_and_bad_bodies_are_llm_errors():
    def timeout(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.TimeoutException("slow")

    def connect(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("down")

    def not_json(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="not json")

    def not_object(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=["x"])

    def no_text(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"content": []})

    def string_content(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"content": "plain reply"})

    for handler in (timeout, connect, not_json, not_object, no_text):
        provider = OpenAIProvider(
            base_url="https://unit.example/anthropic",
            model=HAIKU_MODEL,
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        )
        try:
            provider.complete(system="s", user="u")
        except LLMError:
            continue
        raise AssertionError("a bad provider reply was accepted")

    ok = OpenAIProvider(
        base_url="https://unit.example/anthropic",
        model=HAIKU_MODEL,
        api_key="test-key",
        transport=httpx.MockTransport(string_content),
    )
    assert ok.complete(system="s", user="u").text == "plain reply"


def test_http_error_detail_uses_message_or_text():
    def with_message(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={"message": "nope"})

    def with_list(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json=["x"])

    def with_text(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, text="raw fail")

    for handler, needle in ((with_message, "nope"), (with_list, "raw"), (with_text, "raw fail")):
        provider = OpenAIProvider(
            base_url="https://unit.example/anthropic",
            model=HAIKU_MODEL,
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        )
        try:
            provider.complete(system="s", user="u")
        except LLMError as exc:
            assert needle in str(exc) or "400" in str(exc)
            continue
        raise AssertionError("an http error was treated as prose")


def test_openai_style_rejects_empty_choices():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"choices": []})

    provider = OpenAIProvider(
        base_url="https://unit.example/v1",
        model="gpt-test",
        api_key="test-key",
        style="openai",
        transport=httpx.MockTransport(handler),
    )
    try:
        provider.complete(system="s", user="u")
    except LLMError:
        return
    raise AssertionError("empty choices were accepted")


def test_drafter_retries_an_empty_completion_then_uses_templates():
    class EmptyThenText:
        calls = 0

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            self.calls += 1
            if self.calls == 1:
                return Completion("  ", 1, 1, "fake")
            return Completion("Draft for human review: please read this.", 1, 1, "fake")

    first = Drafter(EmptyThenText(), NullProvider())
    message = first.customer_message(_variables())
    assert message.downgraded is False
    assert message.text.startswith("Draft for human review:")

    class AlwaysEmpty:
        model = "fake"

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            return Completion("", 1, 1, self.model)

    second = Drafter(AlwaysEmpty(), NullProvider())
    fallback = second.customer_message(_variables())
    assert fallback.downgraded is True


def test_prompt_registry_rejects_broken_pins_and_files(tmp_path: Path):
    registry = PromptRegistry(root=tmp_path / "prompts", rules_path=tmp_path / "missing.yaml")
    try:
        registry.render("drafter", {"classification": "unauthorized"})
    except ValueError:
        pass
    else:
        raise AssertionError("a missing pin file was accepted")
    (tmp_path / "pins.yaml").write_text("drafter: true\n", encoding="utf-8")
    registry = PromptRegistry(root=tmp_path / "prompts", rules_path=tmp_path / "pins.yaml")
    try:
        registry.render("drafter", {"classification": "unauthorized"})
    except ValueError:
        pass
    else:
        raise AssertionError("a non-integer pin was accepted")
    assert "classification" in allowed_variable_names()
    try:
        PromptRegistry()._reject_raw({"policy_id": 1})
    except ValueError:
        return
    raise AssertionError("a non-string variable was accepted")


def test_empty_completion_retries_then_falls_back():
    class EmptyThenText:
        model = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, system: str, user: str) -> Completion:
            del system
            self.calls += 1
            if self.calls == 1:
                return Completion("  ", 1, 1, "fake")
            assert "Reply with the customer message only." in user
            return Completion("hello there customer", 1, 1, "fake")

    client = EmptyThenText()
    message = Drafter(client, NullProvider()).customer_message(_variables())
    assert message.downgraded is False
    assert "hello there customer" in message.text
    assert client.calls == 2

    class AlwaysEmpty:
        model = "fake"

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            return Completion(" ", 1, 1, "fake")

    fallback = Drafter(AlwaysEmpty(), NullProvider()).customer_message(_variables())
    assert fallback.downgraded is True


def test_failure_kind_labels_known_errors():
    from dispute_copilot.llm.base import failure_kind

    assert failure_kind(RuntimeError("x")) == "unusable"
    assert failure_kind(LLMError("timeout")) == "timeout"
    assert failure_kind(LLMError("connection failed")) == "connection failed"
    assert failure_kind(LLMError("503")) == "503"
    assert failure_kind(LLMError("provider status 429: slow")) == "429"
    assert failure_kind(LLMError("non-json body")) == "non-json"


def test_prompt_registry_rejects_a_missing_pin_file(tmp_path: Path):
    registry = PromptRegistry(root=tmp_path, rules_path=tmp_path / "missing.yaml")
    with pytest.raises(ValueError, match="pin file is missing"):
        registry.render("drafter", _variables())
    from dispute_copilot.drafting import drafter_variables

    assert drafter_variables(
        classification="unauthorized",
        routing="ready_for_review",
        transaction_id="TX-1",
        reported_date="2026-10-01",
        deadline=None,
        policy_id="CARD-POLICY-V2",
        missing_fields="",
        transaction_attempts=1,
        quotes="[]",
    )["deadline"] == "null"


def test_provider_name_and_null_drafter(monkeypatch):
    from dispute_copilot.llm.factory import build_drafter, provider_name

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert provider_name() == "null"
    monkeypatch.setenv("LLM_PROVIDER", "null")
    assert build_drafter() is None
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    drafter = build_drafter()
    assert drafter is not None
    assert type(drafter.primary).__name__ == "NullProvider"
