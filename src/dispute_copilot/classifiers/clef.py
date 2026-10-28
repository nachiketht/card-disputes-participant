"""Cloudflare Clef-flash. Same choice payload as Jev, different host."""

import os
import re
import time

import httpx

from dispute_copilot.classifiers.config import ClassifierSettings, load_settings
from dispute_copilot.classifiers.jev import _QUESTION, _criteria, read_answer
from dispute_copilot.fields import Classification
from dispute_copilot.llm.base import LLMError
from dispute_copilot.observability.cost import price_call
from dispute_copilot.observability.tracing import current

_ACCOUNT = re.compile(r"^[0-9a-fA-F]{32}$")
_MODELS = frozenset({"clef-flash", "clef"})
_HOST = "https://api.cloudflare.com/client/v4/accounts"


class ClefClient:
    def __init__(
        self,
        *,
        account_id: str,
        api_key: str,
        model: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _ACCOUNT.match(account_id.strip()):
            raise LLMError("no clef account is configured")
        if not api_key.strip():
            raise LLMError("no clef key is configured")
        name = model.strip() or "clef-flash"
        if name not in _MODELS:
            raise LLMError("clef model is not usable")
        self.account_id = account_id.strip()
        self.api_key = api_key
        self.model = name
        self.timeout_s = timeout_s
        self._transport = transport
        self.base_url = f"{_HOST}/{self.account_id}/ai/run/@cf/cloudflare/{self.model}"

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
        return _unwrap(body)


def build_clef(settings: ClassifierSettings | None = None, transport: httpx.BaseTransport | None = None) -> ClefClient | None:
    settings = settings or load_settings()
    key = os.environ.get("CLEF_API_KEY", "").strip()
    account = os.environ.get("CLEF_ACCOUNT_ID", "").strip()
    model = os.environ.get("CLEF_MODEL", "").strip() or settings.clef_model
    if not key or not account:
        return None
    return ClefClient(
        account_id=account,
        api_key=key,
        model=model,
        timeout_s=settings.clef_timeout_s,
        transport=transport,
    )


def call_clef(client, complaint: str, threshold: float) -> dict:
    started = time.perf_counter()
    tokens_in = None
    tokens_out = None
    priced: dict = {"status": "unpriced", "usd": None}
    model_name = str(getattr(client, "model", "") or "")
    try:
        with current().span("model.call") as span:
            body = client.ask(complaint)
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
            "model": model_name,
        }
    parsed["failed"] = False
    parsed["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    parsed["tokens_in"] = tokens_in
    parsed["tokens_out"] = tokens_out
    parsed["cost"] = priced
    parsed["model"] = model_name
    return parsed


def _unwrap(body: object) -> dict:
    if not isinstance(body, dict):
        raise LLMError("provider returned a non-object")
    if body.get("success") is False:
        raise LLMError("provider status was not usable")
    if isinstance(body.get("answers"), dict):
        return body
    result = body.get("result")
    if isinstance(result, dict) and isinstance(result.get("answers"), dict):
        return result
    raise LLMError("provider response has no choice")


def _usage(body: dict) -> tuple[int | None, int | None]:
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
