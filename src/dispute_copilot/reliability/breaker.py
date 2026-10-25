"""Cross-case circuit breaker for one read dependency.

The breaker never shortens a case. While closed or half-open, a call gets the
full retry budget (three attempts). It counts whole cases: it opens only after
N consecutive cases exhausted every attempt on the same dependency. While
open, later cases skip that call and take the existing degradation path
(transactions unavailable -> amount and currency empty, manual_review). After
the cooldown one case is let through as a probe with its full attempts. A
success closes the breaker; an exhausted probe opens it again.
"""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dispute_copilot.observability.tracing import current

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
_ROOT = Path(__file__).resolve().parents[3]
BREAKER_FILE = _ROOT / "config" / "rules" / "breaker.yaml"
_ALLOWED_KEYS = frozenset({"enabled", "failure_threshold", "cooldown_s"})


@dataclass(frozen=True)
class BreakerConfig:
    enabled: bool = True
    failure_threshold: int = 3
    cooldown_s: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if self.cooldown_s < 0:
            raise ValueError("cooldown_s must not be negative")


def load_breaker_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> BreakerConfig:
    env = os.environ if env is None else env
    target = path or BREAKER_FILE
    raw: dict = {}
    if target.is_file():
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{target.name} must be a mapping")
        unknown = set(raw) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(f"{target.name} has unknown keys: {sorted(unknown)}")
    enabled = bool(raw.get("enabled", True))
    threshold = int(raw.get("failure_threshold", 3))
    cooldown = float(raw.get("cooldown_s", 30.0))
    if env.get("BREAKER_ENABLED", "").strip():
        enabled = env["BREAKER_ENABLED"].strip().lower() not in {"0", "false", "off", "no"}
    if env.get("BREAKER_FAILURE_THRESHOLD", "").strip():
        threshold = int(env["BREAKER_FAILURE_THRESHOLD"])
    if env.get("BREAKER_COOLDOWN_S", "").strip():
        cooldown = float(env["BREAKER_COOLDOWN_S"])
    return BreakerConfig(enabled, threshold, cooldown)


@dataclass
class CircuitBreaker:
    dependency: str
    config: BreakerConfig = field(default_factory=BreakerConfig)
    clock: Callable[[], float] = time.monotonic
    state: str = CLOSED
    consecutive_exhausted: int = 0
    opened_at: float | None = None
    opens: int = 0

    def allow(self) -> bool:
        """True means make the call with the full retry budget."""
        if not self.config.enabled or self.state == CLOSED:
            return True
        if self.state == OPEN and self.opened_at is not None:
            if self.clock() - self.opened_at >= self.config.cooldown_s:
                self._move(HALF_OPEN, "cooldown elapsed; next case is a probe")
                return True
            return False
        return True

    def record_success(self) -> None:
        if not self.config.enabled:
            return
        self.consecutive_exhausted = 0
        if self.state != CLOSED:
            self.opened_at = None
            self._move(CLOSED, "probe succeeded")

    def record_exhausted(self) -> None:
        if not self.config.enabled:
            return
        self.consecutive_exhausted += 1
        if self.state == HALF_OPEN:
            self._open("probe exhausted its attempts")
            return
        if self.state == CLOSED and self.consecutive_exhausted >= self.config.failure_threshold:
            self._open(f"{self.consecutive_exhausted} consecutive cases exhausted retries")

    def snapshot(self) -> dict:
        return {
            "dependency": self.dependency,
            "state": self.state,
            "consecutive_exhausted": self.consecutive_exhausted,
            "failure_threshold": self.config.failure_threshold,
            "cooldown_s": self.config.cooldown_s,
        }

    def _open(self, reason: str) -> None:
        self.opened_at = self.clock()
        self.opens += 1
        self._move(OPEN, reason)

    def _move(self, state: str, reason: str) -> None:
        previous, self.state = self.state, state
        current().event(
            "breaker.transition",
            level="warning" if state == OPEN else "info",
            dependency=self.dependency,
            from_state=previous,
            to_state=state,
            reason=reason,
            consecutive_exhausted=self.consecutive_exhausted,
        )
