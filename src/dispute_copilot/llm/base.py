"""The one model call the pipeline is allowed to make.

A provider returns prose. It does not return a classification, a route, a
deadline, or a fact. Callers that need a scored field read the rules modules.
"""

from dataclasses import dataclass
from typing import Protocol

from dispute_copilot.errors import CopilotError


class LLMError(CopilotError):
    """The provider timed out, returned an error, or returned nothing usable."""


def failure_kind(exc: BaseException) -> str:
    """A short audit label. The response body, key, and account id are not included."""
    if not isinstance(exc, LLMError):
        return "unusable"
    text = str(exc).strip()
    folded = text.casefold()
    if folded in {"timeout", "provider timeout"}:
        return "timeout"
    if folded in {"connection failed", "provider connection failed"}:
        return "connection failed"
    if len(text) == 3 and text.isdigit() and text[0] in "12345":
        return text
    head = text.split(":", 1)[0].strip()
    if head.casefold().startswith("provider status "):
        code = head.rsplit(" ", 1)[-1]
        if len(code) == 3 and code.isdigit() and code[0] in "12345":
            return code
    if "non-json" in folded:
        return "non-json"
    return "unusable"


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str) -> Completion:
        """Return one completion. Raise LLMError when the call cannot be used."""
