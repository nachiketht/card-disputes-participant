"""JSON model replies are checked with pydantic and retried once."""

import json

from pydantic import BaseModel, ConfigDict, Field

from dispute_copilot.classifiers.haiku import call_model
from dispute_copilot.llm.base import Completion, LLMError
from dispute_copilot.llm.schema import ClassifierReply, CriticReply, complete_json


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = Field(ge=0)


class _Turns:
    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0
        self.users: list[str] = []

    def complete(self, *, system: str, user: str) -> Completion:
        del system
        self.users.append(user)
        index = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        return Completion(self.texts[index], 2, 3, "fake")


def test_a_schema_miss_retries_once_and_keeps_the_second_object():
    model = _Turns("not json", '{"n": 4}')
    result = complete_json(model, Box, system="s", user="u")
    assert model.calls == 2
    assert result.value is not None
    assert result.value.n == 4
    assert result.attempts == 2
    assert result.tokens_in == 4
    assert result.tokens_out == 6
    assert "required keys" in model.users[1]


def test_two_schema_misses_do_not_call_a_third_time():
    model = _Turns("not json", '{"n": -1}')
    result = complete_json(model, Box, system="s", user="u")
    assert model.calls == 2
    assert result.value is None
    assert result.attempts == 2


def test_a_valid_reply_is_not_retried():
    model = _Turns('{"n": 1}', '{"n": 9}')
    result = complete_json(model, Box, system="s", user="u")
    assert model.calls == 1
    assert result.value is not None
    assert result.value.n == 1
    assert result.attempts == 1


def test_a_provider_error_is_not_a_schema_retry():
    class Boom:
        calls = 0

        def complete(self, *, system: str, user: str) -> Completion:
            del system, user
            self.calls += 1
            raise LLMError("provider timeout")

    model = Boom()
    try:
        complete_json(model, Box, system="s", user="u")
    except LLMError:
        assert model.calls == 1
        return
    raise AssertionError("provider error was swallowed")


def test_classifier_schema_requires_the_three_strings():
    parsed = ClassifierReply.model_validate(
        {"class": "unauthorized", "reason": "The customer does not recognize it.", "evidence": "I do not recognize it."}
    )
    assert parsed.label == "unauthorized"
    try:
        ClassifierReply.model_validate({"class": "unauthorized", "reason": "ok"})
    except Exception:
        return
    raise AssertionError("missing evidence was accepted")


def test_critic_schema_requires_pass_or_block_and_two_sentences():
    parsed = CriticReply.model_validate(
        {"verdict": "pass", "reason": "Citations are present. No outcome is promised."}
    )
    assert parsed.verdict == "pass"
    try:
        CriticReply.model_validate({"verdict": "maybe", "reason": "Citations are present. No outcome is promised."})
    except Exception:
        return
    raise AssertionError("a non pass-or-block verdict was accepted")


def test_call_model_retries_once_on_invalid_json():
    complaint = "I do not recognize this purchase."
    valid = json.dumps(
        {
            "class": "unauthorized",
            "reason": "The customer does not recognize the purchase.",
            "evidence": "I do not recognize this purchase.",
        }
    )
    model = _Turns("not json", valid)
    reply = call_model(model, complaint)
    assert model.calls == 2
    assert reply["accepted"] is True
    assert reply["class"] == "unauthorized"
    assert reply["tokens_in"] == 4
    assert reply["tokens_out"] == 6
