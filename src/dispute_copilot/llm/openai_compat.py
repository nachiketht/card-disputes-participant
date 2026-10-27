"""One provider client for Azure AI.

The default wire format is the Anthropic Messages API (`POST {base}/v1/messages`).
The key is `LLM_API_KEY`. `style="openai"` speaks chat completions.
"""

import os

import httpx

from dispute_copilot.llm.base import Completion, LLMError

HAIKU_MODEL = "claude-haiku-4-5"
AZURE_BASE_URL = "https://pod3-resource.services.ai.azure.com/anthropic"
ANTHROPIC_VERSION = "2023-06-01"


def default_base_url() -> str:
    """Anthropic Messages route on the Azure AI resource."""
    return AZURE_BASE_URL


def resolve_api_key() -> str:
    return os.environ.get("LLM_API_KEY", "").strip()


class OpenAIProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float = 30.0,
        style: str = "anthropic",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("no model key is configured")
        if not model:
            raise LLMError("no model id is configured")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.style = style
        self._transport = transport

    def complete(self, *, system: str, user: str) -> Completion:
        try:
            if self.style == "openai":
                return self._openai(system, user)
            return self._anthropic(system, user)
        except LLMError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMError("provider timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMError("provider connection failed") from exc

    def _anthropic(self, system: str, user: str) -> Completion:
        payload = {
            "model": self.model,
            "max_tokens": 512,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "disabled"},
        }
        body = self._post(
            f"{self.base_url}/v1/messages",
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        text = _anthropic_text(body)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return Completion(
            text,
            _count(usage.get("input_tokens")),
            _count(usage.get("output_tokens")),
            self.model,
        )

    def _openai(self, system: str, user: str) -> Completion:
        root = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body = self._post(
            f"{root}/chat/completions",
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
        )
        text = _openai_text(body)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return Completion(
            text,
            _count(usage.get("prompt_tokens")),
            _count(usage.get("completion_tokens")),
            self.model,
        )

    def _post(self, url: str, payload: dict, headers: dict[str, str]) -> dict:
        request_headers = {"content-type": "application/json", **headers}
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            response = client.post(url, json=payload, headers=request_headers)
        if response.status_code >= 400:
            raise LLMError(f"provider status {response.status_code}: {_error_detail(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError("provider returned non-json") from exc
        if not isinstance(body, dict):
            raise LLMError("provider returned a non-object")
        return body


def _error_detail(response: httpx.Response) -> str:
    try:
        parsed = response.json()
    except ValueError:
        return response.text[:240]
    if not isinstance(parsed, dict):
        return response.text[:240]
    error = parsed.get("error")
    if isinstance(error, dict):
        kind = error.get("type", "")
        message = error.get("message", "")
        return f"{kind}: {message}"[:240]
    message = parsed.get("message")
    if isinstance(message, str):
        return message[:240]
    return response.text[:240]


def _anthropic_text(body: dict) -> str:
    content = body.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        raise LLMError("provider response has no content")
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    text = "\n".join(part.strip() for part in parts if part.strip())
    if not text:
        raise LLMError("provider response has no text")
    return text


def _openai_text(body: dict) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("provider response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise LLMError("provider response has no text")


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
