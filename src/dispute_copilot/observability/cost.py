"""Estimated token cost from config/rules/cost.yaml.

A model with no price is "not configured". This module does not invent a rate.
Compute time is "estimate, unmeasured" unless the file contains a measured usd
figure. Nothing here changes a scored field.
"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
COST_FILE = _ROOT / "config" / "rules" / "cost.yaml"


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
    """Return status "estimate" and a usd figure, or "not configured" and usd None."""
    table = table if table is not None else load_cost()
    models = table.get("models") if isinstance(table.get("models"), dict) else {}
    rates = models.get(model)
    if not isinstance(rates, dict):
        return {"status": "not configured", "usd": None}
    incoming = _rate(rates.get("input_per_1k"))
    outgoing = _rate(rates.get("output_per_1k"))
    if incoming is None or outgoing is None:
        return {"status": "not configured", "usd": None}
    if tokens_in < 0 or tokens_out < 0:
        return {"status": "not configured", "usd": None}
    usd = (tokens_in / 1000.0) * incoming + (tokens_out / 1000.0) * outgoing
    return {"status": "estimate", "usd": round(usd, 6)}


def compute_cost(table: dict | None = None) -> dict:
    table = table if table is not None else load_cost()
    compute = table.get("compute") if isinstance(table.get("compute"), dict) else {}
    usd = compute.get("usd")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return {"status": "estimate, unmeasured", "usd": None}
    return {"status": "provided", "usd": round(float(usd), 6)}


def note_model_span(span, completion) -> None:
    """Attach token counts and a priced estimate to a model span. No prompt text."""
    model = str(getattr(completion, "model", "") or "")
    tokens_in = _tokens(getattr(completion, "tokens_in", None))
    tokens_out = _tokens(getattr(completion, "tokens_out", None))
    span.set(model=model, tokens_in=tokens_in, tokens_out=tokens_out)
    if tokens_in is None or tokens_out is None:
        span.set(cost_status="unmeasured")
        return
    priced = price_call(model, tokens_in, tokens_out)
    span.set(cost_status=priced["status"])
    if priced["usd"] is not None:
        span.set(cost_usd=priced["usd"])


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
