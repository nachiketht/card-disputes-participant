"""Customer-message drafter. One hop: the configured model, then templates.

The constructor accepts only the template provider as fallback, so a second
model cannot be configured. Scored fields are not arguments this module is
allowed to change; it returns prose for the customer-message section.
"""

from dataclasses import dataclass

from dispute_copilot.evidence.redaction import Redactor
from dispute_copilot.fields import (
    CLASSIFICATION,
    DEADLINE,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    ROUTING,
)
from dispute_copilot.llm.base import LLMError, LLMProvider
from dispute_copilot.llm.guard import rejects
from dispute_copilot.llm.null import TEMPLATE_CUSTOMER_MESSAGE, NullProvider
from dispute_copilot.llm.prompts import PromptRegistry, RenderedPrompt
from dispute_copilot.observability.cost import note_model_span
from dispute_copilot.observability.tracing import current

_DRAFT_MARK = "Draft for human review:"


def drafter_variables(
    *,
    classification: str,
    routing: str,
    transaction_id: str,
    reported_date: str,
    deadline: str | None,
    policy_id: str,
    missing_fields: str,
    transaction_attempts: int,
    quotes: str,
) -> dict[str, str]:
    """Closed set for the pinned drafter prompt. Every value is already structured."""
    return {
        CLASSIFICATION: classification,
        ROUTING: routing,
        FACT_TRANSACTION_ID: transaction_id,
        FACT_REPORTED_DATE: reported_date,
        DEADLINE: deadline if deadline else "null",
        "policy_id": policy_id,
        "missing_fields": missing_fields or "none",
        "transaction_attempts": str(transaction_attempts),
        "quotes": quotes,
    }


@dataclass(frozen=True)
class DraftedMessage:
    text: str
    downgraded: bool
    prompt_id: str | None = None
    prompt_version: int | None = None
    prompt_hash: str | None = None


class Drafter:
    def __init__(self, primary: LLMProvider, fallback: NullProvider, registry: PromptRegistry | None = None) -> None:
        if type(fallback) is not NullProvider:
            raise TypeError("the only fallback is the template provider")
        self.primary = primary
        self.fallback = fallback
        self.registry = registry or PromptRegistry()
        self._redactor = Redactor()

    def customer_message(self, variables: dict[str, str]) -> DraftedMessage:
        try:
            rendered = self.registry.render("drafter", variables)
        except (OSError, ValueError):
            return self._templates(None)
        try:
            text = self._complete(rendered)
        except Exception:
            return self._templates(rendered)
        if rejects(text) or self._redactor.contains_restricted(text):
            return self._templates(rendered)
        return DraftedMessage(
            _normalize(text),
            False,
            rendered.prompt_id,
            rendered.prompt_version,
            rendered.prompt_hash,
        )

    def _complete(self, rendered: RenderedPrompt) -> str:
        first = self._complete_once(rendered.system, rendered.user)
        if first.strip():
            return first.strip()
        second = self._complete_once(
            rendered.system,
            rendered.user + "\nReply with the customer message only.",
        )
        if not second.strip():
            raise LLMError("empty completion")
        return second.strip()

    def _complete_once(self, system: str, user: str) -> str:
        if getattr(self.primary, "model", "") == "template":
            return self.primary.complete(system=system, user=user).text
        with current().span("model.call") as span:
            completion = self.primary.complete(system=system, user=user)
            note_model_span(span, completion)
            return completion.text

    def _templates(self, rendered: RenderedPrompt | None) -> DraftedMessage:
        return DraftedMessage(
            TEMPLATE_CUSTOMER_MESSAGE,
            True,
            None if rendered is None else rendered.prompt_id,
            None if rendered is None else rendered.prompt_version,
            None if rendered is None else rendered.prompt_hash,
        )


def _normalize(text: str) -> str:
    cleaned = " ".join(text.split())
    if cleaned.startswith(_DRAFT_MARK):
        return cleaned
    return f"{_DRAFT_MARK} {cleaned}"
