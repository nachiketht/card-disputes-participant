"""Flag instruction-like text in untrusted content. Flagging never changes a decision.

Documents and tool payloads are data. The rules engine reads only labeled
lines and enum values, so an embedded command cannot reach a scored field or
a write. This scan exists for evidence and alerting: it records which source
carried instruction-like text and which pattern matched. The text itself is
never logged.
"""

import re

PATTERNS: dict[str, re.Pattern[str]] = {
    "system_override": re.compile(r"\bsystem\s+(override|prompt|instruction)", re.IGNORECASE),
    "ignore_instructions": re.compile(r"\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|bank|above|policy|instructions)", re.IGNORECASE),
    "skip_approval": re.compile(r"\b(skip|bypass|without)\s+(the\s+)?(human\s+)?(approval|review)", re.IGNORECASE),
    "promise_refund": re.compile(r"\b(promise|issue|grant)\w*\s+(an?\s+)?(immediate\s+)?refund", re.IGNORECASE),
    "exfiltrate_tokens": re.compile(r"\b(copy|send|reveal|include)\s+(all\s+)?(the\s+)?private\s+tokens?", re.IGNORECASE),
    "claimed_priority": re.compile(r"\b(administrator|admin|root)\s+priority\b", re.IGNORECASE),
}


def instruction_flags(text: object) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    return [name for name, pattern in PATTERNS.items() if pattern.search(text)]


def payload_flags(payload: object) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            found.extend(payload_flags(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(payload_flags(value))
    else:
        found.extend(instruction_flags(payload))
    return sorted(set(found))
