"""One extra model call when the configured model cannot answer."""

from dispute_copilot.llm.base import Completion, LLMError, failure_kind


class FallbackProvider:
    """Try the primary client, then one fallback client."""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback
        self.model = str(getattr(primary, "model", "") or "")
        self.fallback_used = False
        self.primary_failure = ""

    def complete(self, *, system: str, user: str) -> Completion:
        try:
            result = self.primary.complete(system=system, user=user)
        except LLMError as exc:
            self.primary_failure = failure_kind(exc)
            self.fallback_used = True
            self.model = ""
            result = self.fallback.complete(system=system, user=user)
            self.model = str(getattr(result, "model", "") or getattr(self.fallback, "model", "") or "")
            return _mark(result, fallback_used=True, primary_failure=self.primary_failure)
        self.fallback_used = bool(getattr(result, "fallback_used", False))
        self.primary_failure = str(getattr(result, "primary_failure", "") or "")
        self.model = str(getattr(result, "model", "") or self.model)
        return _mark(result, fallback_used=self.fallback_used, primary_failure=self.primary_failure)

    def use_fallback(self, *, system: str, user: str) -> Completion:
        """One fallback call after the primary reply could not be used."""
        if not self.primary_failure:
            self.primary_failure = "unusable"
        self.fallback_used = True
        self.model = ""
        result = self.fallback.complete(system=system, user=user)
        self.model = str(getattr(result, "model", "") or getattr(self.fallback, "model", "") or "")
        return _mark(result, fallback_used=True, primary_failure=self.primary_failure)


def _mark(result: Completion, *, fallback_used: bool, primary_failure: str) -> Completion:
    return Completion(
        result.text,
        result.tokens_in,
        result.tokens_out,
        result.model,
        fallback_used,
        primary_failure if fallback_used else "",
    )
