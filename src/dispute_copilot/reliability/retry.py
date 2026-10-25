"""Retry policy for read tools: at most three attempts, exponential backoff, capped jitter.

The procedure fixes three total attempts per case, so the loader rejects a
larger number. Delays are small by default so a full run stays fast. Values
come from config/rules/retry.yaml, then environment overrides.
"""

import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROCEDURE_MAX_ATTEMPTS = 3
_ROOT = Path(__file__).resolve().parents[3]
RETRY_FILE = _ROOT / "config" / "rules" / "retry.yaml"
_ALLOWED_KEYS = frozenset(
    {"max_attempts", "retryable_status_codes", "retry_on_timeout", "base_delay_s", "max_delay_s", "http_timeout_s"}
)


@dataclass
class RetryPolicy:
    max_attempts: int = PROCEDURE_MAX_ATTEMPTS
    retryable_status_codes: frozenset[int] = frozenset({503})
    retry_on_timeout: bool = True
    base_delay_s: float = 0.05
    max_delay_s: float = 0.2
    http_timeout_s: float = 10.0
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > PROCEDURE_MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be from 1 to {PROCEDURE_MAX_ATTEMPTS}")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("backoff delays must not be negative")
        if self.http_timeout_s <= 0:
            raise ValueError("http_timeout_s must be positive")

    def delay(self, attempt: int) -> float:
        """Wait after failed attempt N: half the capped exponential step plus up to half again."""
        cap = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return cap / 2 + self.rng.uniform(0, cap / 2)

    def retryable(self, status: int, timed_out: bool) -> bool:
        if timed_out:
            return self.retry_on_timeout
        return status in self.retryable_status_codes


def load_policy(path: Path | None = None, env: Mapping[str, str] | None = None, **overrides) -> RetryPolicy:
    env = os.environ if env is None else env
    raw = _read(path or RETRY_FILE)
    values: dict = {
        "max_attempts": int(raw.get("max_attempts", PROCEDURE_MAX_ATTEMPTS)),
        "retryable_status_codes": frozenset(int(x) for x in raw.get("retryable_status_codes", [503])),
        "retry_on_timeout": bool(raw.get("retry_on_timeout", True)),
        "base_delay_s": float(raw.get("base_delay_s", 0.05)),
        "max_delay_s": float(raw.get("max_delay_s", 0.2)),
        "http_timeout_s": float(raw.get("http_timeout_s", 10.0)),
    }
    for key, name in (
        ("base_delay_s", "RETRY_BASE_DELAY_S"),
        ("max_delay_s", "RETRY_MAX_DELAY_S"),
        ("http_timeout_s", "HTTP_TIMEOUT_S"),
    ):
        if env.get(name, "").strip():
            values[key] = float(env[name])
    values.update(overrides)
    return RetryPolicy(**values)


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must be a mapping")
    unknown = set(loaded) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{path.name} has unknown keys: {sorted(unknown)}")
    return loaded
