"""Estimated token cost from config/rules/cost.yaml.

Prices are USD per million tokens. A listed model is "estimate from list price".
A model with no entry is "no price configured for <model>". This module does
not invent a rate. Compute time is "estimate, unmeasured" unless the file
contains a measured usd figure. Nothing here changes a scored field.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

_ROOT = Path(__file__).resolve().parents[3]
COST_FILE = _ROOT / "config" / "rules" / "cost.yaml"
LIST_PRICE = "estimate from list price"
_DATE_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")


def load_cost(path: Path | None = None) -> dict:
    file = path or COST_FILE
    if not file.is_file():
        return {"models": {}, "compute": {"status": "estimate, unmeasured"}}
    loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("cost.yaml must be a mapping")
    models = loaded.get("models") or {}
    compute = loaded.get("compute") or {}
    if not isinstance(models, dict) or not isinstance(compute, dict):
        raise ValueError("cost.yaml models and compute must be mappings")
    return {"models": models, "compute": compute}


def price_call(model: str, tokens_in: int, tokens_out: int, table: dict | None = None) -> dict:
    """Price one call from the table. A missing model names that model."""
    table = table if table is not None else load_cost()
    models = table.get("models") if isinstance(table.get("models"), dict) else {}
    rates = _rates_for(model, models)
    if rates is None:
        return _missing(model)
    incoming = _rate(rates.get("input_per_million"))
    outgoing = _rate(rates.get("output_per_million"))
    if incoming is None or outgoing is None or tokens_in < 0 or tokens_out < 0:
        return _missing(model)
    usd = (tokens_in / 1_000_000.0) * incoming + (tokens_out / 1_000_000.0) * outgoing
    return {"status": LIST_PRICE, "usd": round(usd, 6)}


def compute_cost(table: dict | None = None) -> dict:
    table = table if table is not None else load_cost()
    compute = table.get("compute") if isinstance(table.get("compute"), dict) else {}
    usd = compute.get("usd")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return {"status": "estimate, unmeasured", "usd": None}
    return {"status": "provided", "usd": round(float(usd), 6)}


def note_model_span(span, completion) -> None:
    """Attach the model id, token counts, a place word, and a priced estimate.

    The endpoint, the key, and headers are not written.
    """
    model = str(getattr(completion, "model", "") or "")
    tokens_in = _tokens(getattr(completion, "tokens_in", None))
    tokens_out = _tokens(getattr(completion, "tokens_out", None))
    span.set(model=model, tokens_in=tokens_in, tokens_out=tokens_out, where=run_where())
    if tokens_in is None or tokens_out is None:
        span.set(cost_status="unmeasured")
        return
    priced = price_call(model, tokens_in, tokens_out)
    span.set(cost_status=priced["status"])
    if priced["usd"] is not None:
        span.set(cost_usd=priced["usd"])


def place_attrs(name: str) -> dict:
    """Place word for a case or a model call, plus the configured model id on a case."""
    attrs: dict = {"where": run_where()}
    if name == "case":
        model = configured_model()
        if model:
            attrs["model"] = model
    return attrs


def run_where() -> str:
    """Label when one is set. Otherwise the host class of the model endpoint."""
    label = os.environ.get("RUN_LABEL", "").strip()
    if label:
        return label[:64]
    base = os.environ.get("LLM_BASE_URL", "").strip()
    if not base:
        from dispute_copilot.llm.openai_compat import default_base_url

        base = default_base_url()
    return host_class(base)


def host_class(url: str) -> str:
    """Classify a model endpoint. The return value is a single word."""
    host = (urlparse(url.strip()).hostname or "").lower()
    if host == "services.ai.azure.com" or host.endswith(".services.ai.azure.com"):
        return "azure"
    if host in {"localhost", "::1"} or host.startswith("127."):
        return "local"
    return "other"


def configured_model() -> str:
    """Model id when a provider is on. Empty when this run stays on templates."""
    from dispute_copilot.llm.openai_compat import HAIKU_MODEL, resolve_api_key

    name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not name:
        name = "azure" if resolve_api_key() else "null"
    if name in {"null", "template", "templates", "off"}:
        return ""
    chosen = os.environ.get("LLM_MODEL", "").strip()
    return chosen or HAIKU_MODEL


def _rates_for(model: str, models: dict) -> dict | None:
    folded: dict[str, dict] = {}
    for key, rates in models.items():
        if isinstance(key, str) and isinstance(rates, dict) and key.strip():
            folded[key.strip().lower()] = rates
    needle = model.strip().lower()
    if not needle:
        return None
    direct = folded.get(needle)
    if direct is not None:
        return direct
    best: tuple[int, int] | None = None
    chosen: dict | None = None
    stripped = _DATE_SUFFIX.sub("", needle)
    for key, rates in folded.items():
        if stripped == key:
            rank = (1, len(key))
        elif stripped.endswith("." + key):
            rank = (0, len(key))
        else:
            continue
        if best is None or rank > best:
            best = rank
            chosen = rates
    return chosen


def _missing(model: str) -> dict:
    name = model.strip() or "(unnamed)"
    return {"status": f"no price configured for {name}", "usd": None}


def _rate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def _tokens(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value
