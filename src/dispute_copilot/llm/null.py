"""Template prose. This is the only fallback, and the default when no model is configured."""

from dispute_copilot.llm.base import Completion

TEMPLATE_CUSTOMER_MESSAGE = (
    "We have prepared your complaint for review. "
    "We cannot confirm an outcome. "
    "No case has been created yet; human approval remains required."
)


class NullProvider:
    """Returns the fixed customer message. It never opens a network connection."""

    model = "template"

    def complete(self, *, system: str, user: str) -> Completion:
        del system, user
        return Completion(TEMPLATE_CUSTOMER_MESSAGE, 0, 0, self.model)

    def customer_message(self) -> str:
        return TEMPLATE_CUSTOMER_MESSAGE
