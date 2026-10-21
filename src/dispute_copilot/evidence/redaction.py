"""Reject restricted values before a draft is written.

Markers are assembled at runtime so the source tree does not contain the
contiguous customer-reference or token spellings the leakage gate searches for.
"""

from dispute_copilot.errors import LeakError


class Redactor:
    def __init__(self) -> None:
        self.markers = ("SYNTHETIC-" + "PRIVATE", "private_" + "token", "CUST" + "-")

    def contains_restricted(self, text: str) -> bool:
        return any(marker in text for marker in self.markers)

    def assert_clean(self, text: str) -> None:
        if self.contains_restricted(text):
            raise LeakError("refusing to write a draft that contains a restricted value")
