"""Build the drafter from the environment. Tests leave this unset and stay on templates."""

import os

from dispute_copilot.drafting import Drafter
from dispute_copilot.llm.null import NullProvider
from dispute_copilot.llm.openai_compat import HAIKU_MODEL, OpenAIProvider, default_base_url, resolve_api_key


def provider_name() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if resolve_api_key():
        return "bedrock"
    return "null"


def build_critic():
    """The step-1 judge. None means the call cannot be made and the review blocks."""
    name = provider_name()
    if name in {"", "null", "template", "templates", "off"}:
        return None
    key = resolve_api_key()
    if not key:
        return None
    return OpenAIProvider(
        base_url=os.environ.get("LLM_BASE_URL", "").strip() or default_base_url(),
        model=os.environ.get("LLM_MODEL", "").strip() or HAIKU_MODEL,
        api_key=key,
        timeout_s=_timeout(),
        style=os.environ.get("LLM_API_STYLE", "anthropic").strip().lower() or "anthropic",
    )


def build_drafter() -> Drafter | None:
    """None keeps the original template narrative. A Drafter may still fall back to it."""
    name = provider_name()
    if name in {"", "null", "template", "templates", "off"}:
        return None
    key = resolve_api_key()
    fallback = NullProvider()
    if not key:
        return Drafter(fallback, fallback)
    timeout = _timeout()
    primary = OpenAIProvider(
        base_url=os.environ.get("LLM_BASE_URL", "").strip() or default_base_url(),
        model=os.environ.get("LLM_MODEL", "").strip() or HAIKU_MODEL,
        api_key=key,
        timeout_s=timeout,
        style=os.environ.get("LLM_API_STYLE", "anthropic").strip().lower() or "anthropic",
    )
    return Drafter(primary, fallback)


def _timeout() -> float:
    raw = os.environ.get("LLM_TIMEOUT_S", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 0.001
    return value
