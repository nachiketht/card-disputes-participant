"""Per-case traces as JSON lines.

One trace per case, one span per step. Tool calls, each attempt, backoff
waits, breaker decisions, rule decisions and the review gate are spans or
events. Every attribute passes through the redactor before it is written, and
free text is never recorded: only ids, counts, statuses and enum values.
"""

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import CASE_ID

LOGGER = logging.getLogger("dispute_copilot")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
_MAX_TEXT = 160
_current: ContextVar["Tracer | None"] = ContextVar("dispute_copilot_tracer", default=None)


def new_run_id() -> str:
    return os.environ.get("RUN_ID") or "run-" + uuid.uuid4().hex[:10]


def safe(value: object, redactor: Redactor | None = None) -> object:
    redactor = redactor or Redactor()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if redactor.contains_restricted(value):
            masked = redactor.mask(value)
            value = masked if not redactor.contains_restricted(masked) else "[redacted]"
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "..."
    if isinstance(value, (list, tuple)):
        return [safe(item, redactor) for item in value]
    if hasattr(value, "value"):
        return safe(getattr(value, "value"), redactor)
    return safe(str(value), redactor)


class Span:
    def __init__(self, tracer: "Tracer", name: str, parent_id: str | None, attrs: dict) -> None:
        self.tracer = tracer
        self.name = name
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_id = parent_id
        self.attrs: dict = {}
        self.status = "ok"
        self.error: str | None = None
        self.started = time.perf_counter()
        self.started_at = _now()
        self.set(**attrs)

    def set(self, **attrs) -> None:
        for key, value in attrs.items():
            self.attrs[key] = safe(value, self.tracer.redactor)


class Tracer:
    def __init__(self, case_id: str, run_id: str, trace_dir: Path | None, stage: str = "run") -> None:
        self.case_id = case_id
        self.stage = stage
        self.run_id = run_id
        self.trace_id = uuid.uuid4().hex
        self.trace_dir = trace_dir
        self.redactor = Redactor()
        self.records: list[dict] = []
        self._stack: list[Span] = []

    @contextmanager
    def span(self, name: str, **attrs):
        parent = self._stack[-1].span_id if self._stack else None
        span = Span(self, name, parent, attrs)
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error = type(exc).__name__
            raise
        finally:
            self._stack.pop()
            self._finish(span)

    def event(self, name: str, level: str = "info", **attrs) -> None:
        parent = self._stack[-1].span_id if self._stack else None
        record = self._base("event", name, parent)
        record["level"] = level
        record["attrs"] = {key: safe(value, self.redactor) for key, value in attrs.items()}
        self._emit(record)

    def _finish(self, span: Span) -> None:
        record = self._base("span", span.name, span.parent_id)
        record["span_id"] = span.span_id
        record["start"] = span.started_at
        record["duration_ms"] = round((time.perf_counter() - span.started) * 1000, 3)
        record["status"] = span.status
        if span.error:
            record["error"] = span.error
        record["attrs"] = dict(span.attrs)
        self._emit(record)

    def _base(self, kind: str, name: str, parent: str | None) -> dict:
        return {
            "ts": _now(),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            CASE_ID: self.case_id,
            "stage": self.stage,
            "kind": kind,
            "name": name,
            "parent_id": parent,
        }

    def _emit(self, record: dict) -> None:
        text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if self.redactor.contains_restricted(text):
            record = {**record, "attrs": {"redacted": True}}
            text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.records.append(record)
        level = logging.WARNING if record.get("level") == "warning" or record.get("status") == "error" else logging.INFO
        LOGGER.log(level, record["name"], extra={"record": record})

    def write(self) -> Path | None:
        if self.trace_dir is None:
            return None
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        name = self.case_id if self.stage == "run" else f"{self.case_id}.{self.stage}"
        path = self.trace_dir / f"{name}.jsonl"
        lines = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in self.records]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path


class NullTracer(Tracer):
    def __init__(self) -> None:
        super().__init__("-", "-", None)

    def _emit(self, record: dict) -> None:
        return None


_NULL = NullTracer()


def current() -> Tracer:
    return _current.get() or _NULL


@contextmanager
def case_trace(case_id: str, run_id: str, trace_dir: Path | None, stage: str = "run"):
    tracer = Tracer(case_id, run_id, trace_dir, stage)
    token = _current.set(tracer)
    try:
        yield tracer
    finally:
        _current.reset(token)
        tracer.write()


def _now() -> str:
    """Operational timestamp for trace records only. Rules never read a clock."""
    return datetime.fromtimestamp(time.time(), timezone.utc).isoformat(timespec="milliseconds")
