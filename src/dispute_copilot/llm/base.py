"""The one model call the pipeline is allowed to make.

A provider returns prose. It does not return a classification, a route, a
deadline, or a fact. Callers that need a scored field read the rules modules.
"""

from dataclasses import dataclass
from typing import Protocol

from dispute_copilot.errors import CopilotError


class LLMError(CopilotError):
    """The provider timed out, returned an error, or returned nothing usable."""


@dataclass(frozen=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str) -> Completion:
        """Return one completion. Raise LLMError when the call cannot be used."""
