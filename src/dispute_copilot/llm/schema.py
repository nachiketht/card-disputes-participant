"""Parse a JSON model reply. One retry when the object misses the schema."""

import json
import re
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dispute_copilot.llm.base import Completion
from dispute_copilot.observability.cost import note_model_span
from dispute_copilot.observability.tracing import current

T = TypeVar("T", bound=BaseModel)
REPAIR_USER = "Return one JSON object that matches the required keys."


class ClassifierReply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    label: str = Field(alias="class", min_length=1)
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @field_validator("label", "reason", "evidence", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class CriticReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "block"]
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_is_short(cls, value: str) -> str:
        cleaned = value.strip()
        lines = cleaned.splitlines()
        if not lines or len(lines) > 4 or any(not line.strip() for line in lines):
            raise ValueError("reason must be one to four lines")
        parts = re.split(r"[.!?]+", cleaned)
        sentences = [part for part in parts if part.strip()]
        if len(sentences) < 2 or len(sentences) > 4:
            raise ValueError("reason must be two to four sentences")
        return cleaned


@dataclass(frozen=True)
class JsonCall:
    value: BaseModel | None
    last: Completion
    tokens_in: int
    tokens_out: int
    attempts: int


def parse_model(schema: type[T], text: str) -> T | None:
    body = _json_object(text)
    if body is None:
        return None
    try:
        return schema.model_validate(body)
    except ValidationError:
        return None


def complete_json(model, schema: type[T], *, system: str, user: str) -> JsonCall:
    """Call once, then once more only when the first reply misses the schema.

    A configured fallback is asked once after that, or when the first call raises.
    """
    total_in = 0
    total_out = 0
    last: Completion | None = None
    prompts = (user, f"{user}\n{REPAIR_USER}")
    for attempt, prompt in enumerate(prompts, start=1):
        if attempt == 2 and getattr(model, "fallback_used", False):
            break
        with current().span("model.call") as span:
            completion = model.complete(system=system, user=prompt)
            note_model_span(span, completion)
        last = completion
        total_in += completion.tokens_in
        total_out += completion.tokens_out
        parsed = parse_model(schema, completion.text)
        if parsed is not None:
            return JsonCall(parsed, completion, total_in, total_out, attempt)
    assert last is not None
    use_fallback = getattr(model, "use_fallback", None)
    if use_fallback is None or getattr(model, "fallback_used", False):
        return JsonCall(None, last, total_in, total_out, 2)
    with current().span("model.call") as span:
        completion = use_fallback(system=system, user=user)
        note_model_span(span, completion)
    parsed = parse_model(schema, completion.text)
    return JsonCall(
        parsed,
        completion,
        total_in + completion.tokens_in,
        total_out + completion.tokens_out,
        3,
    )


def _json_object(text: str) -> dict | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw[:4].casefold() == "json":
            raw = raw[4:].strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    return value
