"""Record when an existing protection fires.

The rules still decide the draft. This module only writes a trace event and,
when a span is open, a trace attribute named guardrail. It does not approve.
"""

from urllib.parse import urlparse

from dispute_copilot.observability.tracing import LOGGER, NullTracer, current, safe

KINDS = ("input", "output", "boundary", "approval")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def record(kind: str, **attrs) -> None:
    if kind not in KINDS:
        raise ValueError("unknown guardrail")
    tracer = current()
    tracer.mark(guardrail=kind)
    tracer.event(f"guardrail.{kind}", level="warning", guardrail=kind, **attrs)
    if isinstance(tracer, NullTracer):
        line = {"name": f"guardrail.{kind}", "guardrail": kind}
        for key, value in attrs.items():
            line[key] = safe(value)
        LOGGER.info(line["name"], extra={"record": line})


def loopback_host(url: str) -> bool:
    host = urlparse(url).hostname
    return host in LOOPBACK_HOSTS
