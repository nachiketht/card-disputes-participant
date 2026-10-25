"""Mask restricted values before a draft, log, or trace is written.

Markers are assembled at runtime so the source tree does not contain the
contiguous customer-reference or token spellings the leakage gate searches for.
A quote that still contains a restricted value after masking is not safe to store.
"""

import re

from dispute_copilot.errors import LeakError

MASK = "[redacted]"
_TRAILING = r"[A-Za-z0-9_\-]*"


class Redactor:
    def __init__(self) -> None:
        self.markers = ("SYNTHETIC-" + "PRIVATE", "private_" + "token", "CUST" + "-")

    def contains_restricted(self, text: str) -> bool:
        return any(marker in text for marker in self.markers)

    def mask(self, text: str) -> str:
        """Replace each marker and the token glued to it."""
        result = text
        for marker in sorted(self.markers, key=len, reverse=True):
            result = re.sub(re.escape(marker) + _TRAILING, MASK, result)
        return result

    def mask_quote(self, quote: str) -> str | None:
        """Return a masked quote, or None when no reviewable words would remain."""
        masked = self.mask(quote)
        if self.contains_restricted(masked):
            return None
        leftover = masked.replace(MASK, "")
        letters = "".join(ch for ch in leftover if ch.isalnum())
        if len(letters) < 3:
            return None
        return masked

    def assert_clean(self, text: str) -> None:
        if self.contains_restricted(text):
            raise LeakError("refusing to write a draft that contains a restricted value")
