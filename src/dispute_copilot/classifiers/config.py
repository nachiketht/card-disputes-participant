"""Settings for approaches 2 and 3, and for assignment."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILE = _ROOT / "config" / "rules" / "classifier.yaml"
_APPROACHES = frozenset({"1", "2", "3", "ab"})


@dataclass(frozen=True)
class ClassifierSettings:
    jev_threshold: float
    jev_model: str
    jev_timeout_s: float
    haiku_timeout_s: float
    ab_seed: int
    ab_weights: dict[str, float]
    min_sample: int
    clef_model: str = "clef-flash"
    clef_timeout_s: float = 30.0
    false_negative_ceiling: float = 0.02
    false_positive_low: float = 0.10
    false_positive_high: float = 0.40


def load_settings(path: Path | None = None) -> ClassifierSettings:
    file = path or CONFIG_FILE
    loaded = yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else {}
    if not isinstance(loaded, dict):
        raise ValueError("classifier.yaml must be a mapping")
    jev = loaded.get("jev") if isinstance(loaded.get("jev"), dict) else {}
    haiku = loaded.get("haiku") if isinstance(loaded.get("haiku"), dict) else {}
    clef = loaded.get("clef") if isinstance(loaded.get("clef"), dict) else {}
    ab = loaded.get("ab") if isinstance(loaded.get("ab"), dict) else {}
    comparison = loaded.get("comparison") if isinstance(loaded.get("comparison"), dict) else {}
    weights = ab.get("weights") if isinstance(ab.get("weights"), dict) else {"1": 1, "2": 1, "3": 1}
    clean = {str(key): float(value) for key, value in weights.items() if str(key) in {"1", "2", "3"}}
    for key in ("1", "2", "3"):
        clean.setdefault(key, 0.0)
    return ClassifierSettings(
        jev_threshold=_float(jev.get("threshold"), 0.70),
        jev_model=str(jev.get("model") or "jev-latest"),
        jev_timeout_s=_float(jev.get("timeout_s"), 30.0),
        haiku_timeout_s=_float(haiku.get("timeout_s"), 30.0),
        ab_seed=int(ab.get("seed") or 0),
        ab_weights=clean,
        min_sample=int(comparison.get("min_sample") or 30),
        clef_model=str(clef.get("model") or "clef-flash"),
        clef_timeout_s=_float(clef.get("timeout_s"), 30.0),
        false_negative_ceiling=_float(comparison.get("false_negative_ceiling"), 0.02),
        false_positive_low=_float(comparison.get("false_positive_low"), 0.10),
        false_positive_high=_float(comparison.get("false_positive_high"), 0.40),
    )


def selected_approach(explicit: str | None = None) -> str:
    """Flag, then CLASSIFIER_APPROACH, then approach 1."""
    raw = explicit if explicit is not None else os.environ.get("CLASSIFIER_APPROACH")
    if raw is None or str(raw).strip() == "":
        return "1"
    name = str(raw).strip().lower()
    if name not in _APPROACHES:
        raise ValueError("approach must be 1, 2, 3, or ab")
    return name


def _float(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)
