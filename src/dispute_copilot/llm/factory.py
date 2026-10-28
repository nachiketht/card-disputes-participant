"""Build the drafter from the environment. Tests leave this unset and stay on templates."""

import os

from dispute_copilot.drafting import Drafter
from dispute_copilot.llm.fallback import FallbackProvider
from dispute_copilot.llm.null import NullProvider
from dispute_copilot.llm.openai_compat import HAIKU_MODEL, OpenAIProvider, require_base_url, resolve_api_key

_FALLBACK_MODEL = "claude-haiku-5-5"


def provider_name() -> str:
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if resolve_api_key():
        return "azure"
    return "null"


def build_critic(transport=None, fallback_transport=None):
    """The step-1 judge. None means the call cannot be made and the review blocks."""
    return _live_client(transport, fallback_transport)


def build_drafter(transport=None, fallback_transport=None) -> Drafter | None:
    """None keeps the original template narrative. A Drafter may still fall back to it."""
    name = provider_name()
    if name in {"", "null", "template", "templates", "off"}:
        return None
    key = resolve_api_key()
    templates = NullProvider()
    if not key:
        return Drafter(templates, templates)
    primary = _live_client(transport, fallback_transport)
    if primary is None:
        return Drafter(templates, templates)
    return Drafter(primary, templates)


def _live_client(transport=None, fallback_transport=None):
    name = provider_name()
    if name in {"", "null", "template", "templates", "off"}:
        return None
    key = resolve_api_key()
    if not key:
        return None
    primary = OpenAIProvider(
        base_url=require_base_url(),
        model=_model_id("LLM_MODEL", HAIKU_MODEL),
        api_key=key,
        timeout_s=_timeout(),
        style=_style("LLM_API_STYLE"),
        transport=transport,
    )
    fallback = _fallback_client(fallback_transport)
    if fallback is None:
        return primary
    return FallbackProvider(primary, fallback)


def _fallback_client(transport):
    key = os.environ.get("LLM_FALLBACK_API_KEY", "").strip()
    url = os.environ.get("LLM_FALLBACK_BASE_URL", "").strip()
    if not key or not url:
        return None
    return OpenAIProvider(
        base_url=url,
        model=_model_id("LLM_FALLBACK_MODEL", _FALLBACK_MODEL),
        api_key=key,
        timeout_s=_timeout(),
        style=_style("LLM_FALLBACK_API_STYLE"),
        transport=transport,
    )


def _model_id(name: str, default: str) -> str:
    """Keep the configured id exactly, aside from surrounding space."""
    chosen = os.environ.get(name, "").strip()
    return chosen or default


def _style(name: str) -> str:
    chosen = os.environ.get(name, "anthropic").strip().lower()
    if chosen == "openai":
        return "openai"
    return "anthropic"


def _timeout() -> float:
    raw = os.environ.get("LLM_TIMEOUT_S", "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 0.001
    return value
