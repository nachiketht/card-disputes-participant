"""Choice request for a swappable ranker. No host is built in."""

import os
import time

import httpx

from dispute_copilot.classifiers.config import ClassifierSettings, load_settings
from dispute_copilot.classifiers.haiku import category_text, known_class
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import LLMError
from dispute_copilot.observability.cost import price_call
from dispute_copilot.observability.tracing import current

AMBIGUOUS_OPTION = "ambiguous_or_unclear"
_QUESTION = "class"


class JevClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_s: float, transport: httpx.BaseTransport | None = None) -> None:
        if not base_url.strip():
            raise LLMError("no jev url is configured")
        if not api_key.strip():
            raise LLMError("no jev key is configured")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self._transport = transport

    def ask(self, state: str) -> dict:
        payload = {
            "model": self.model,
            "state": state,
            "questions": {
                _QUESTION: {
                    "type": "choice",
                    "criteria": _criteria(),
                }
            },
        }
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
                response = client.post(
                    self.base_url,
                    json=payload,
                    headers={"content-type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise LLMError("provider timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMError("provider connection failed") from exc
        if response.status_code >= 400:
            raise LLMError("provider status was not usable")
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError("provider returned non-json") from exc
        if not isinstance(body, dict):
            raise LLMError("provider returned a non-object")
        return body


def build_jev(settings: ClassifierSettings | None = None, transport: httpx.BaseTransport | None = None) -> JevClient | None:
    settings = settings or load_settings()
    url = os.environ.get("JEV_BASE_URL", "").strip()
    key = os.environ.get("JEV_API_KEY", "").strip()
    model = os.environ.get("JEV_MODEL", "").strip() or settings.jev_model
    if not url or not key:
        return None
    return JevClient(base_url=url, api_key=key, model=model, timeout_s=settings.jev_timeout_s, transport=transport)


def read_answer(body: dict, threshold: float) -> dict:
    """Top option by probability. A tie, or a short probability, is ambiguous."""
    answers = body.get("answers")
    block = answers.get(_QUESTION) if isinstance(answers, dict) else None
    if not isinstance(block, dict):
        raise LLMError("provider response has no choice")
    probabilities = block.get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities:
        raise LLMError("provider response has no probabilities")
    confidence = block.get("confidence")
    ranked: list[tuple[str, float]] = []
    for key, value in probabilities.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ranked.append((key, float(value)))
    if not ranked:
        raise LLMError("provider response has no probabilities")
    ranked.sort(key=lambda item: item[1], reverse=True)
    top_name, top_probability = ranked[0]
    tied = len(ranked) > 1 and ranked[1][1] == top_probability
    option = _option(top_name)
    ambiguous = tied or option is None or top_probability < threshold
    return {
        "class": None if option is None else option.value,
        Classification.AMBIGUOUS.value: ambiguous or option == Classification.AMBIGUOUS,
        "probabilities": {name: round(value, 6) for name, value in ranked},
        "confidence": confidence if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
        "top_probability": round(top_probability, 6),
        "choice": block.get("choice") if isinstance(block.get("choice"), str) else top_name,
    }


def call_jev(client, complaint: str, threshold: float) -> dict:
    started = time.perf_counter()
    tokens_in = None
    tokens_out = None
    priced: dict = {"status": "unpriced", "usd": None}
    model_name = str(getattr(client, "model", "") or "")
    try:
        with current().span("model.call") as span:
            body = client.ask(complaint)
            model_name = str(getattr(client, "model", "") or "")
            tokens_in, tokens_out = _usage(body)
            if tokens_in is not None:
                priced = price_call(model_name, tokens_in, tokens_out or 0)
            span.set(model=model_name, cost_status=priced["status"])
            if priced.get("usd") is not None:
                span.set(cost_usd=priced["usd"])
        parsed = read_answer(body, threshold)
    except (LLMError, TypeError, ValueError, AttributeError):
        return {
            "class": None,
            Classification.AMBIGUOUS.value: True,
            "probabilities": {},
            "confidence": None,
            "top_probability": None,
            "choice": None,
            "failed": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "tokens_in": None,
            "tokens_out": None,
            "cost": {"status": "unpriced", "usd": None},
        }
    parsed["failed"] = False
    parsed["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    parsed["tokens_in"] = tokens_in
    parsed["tokens_out"] = tokens_out
    parsed["cost"] = priced
    return parsed


def _usage(body: object) -> tuple[int | None, int | None]:
    if not isinstance(body, dict):
        return None, None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    incoming = usage.get("prompt_tokens", usage.get("input_tokens"))
    outgoing = usage.get("completion_tokens", usage.get("output_tokens"))
    return _token(incoming), _token(outgoing)


def _token(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _option(name: str) -> Classification | None:
    if name in {AMBIGUOUS_OPTION, "ambiguous or unclear"}:
        return Classification.AMBIGUOUS
    return known_class(name)


def _criteria() -> dict[str, str]:
    rows = {item.value: line for line, item in _pairs()}
    rows[AMBIGUOUS_OPTION] = "ambiguous or unclear"
    return rows


def _pairs():
    for line in category_text().splitlines():
        name = line.split(":", 1)[0]
        found = known_class(name)
        if found is not None:
            yield line, found
