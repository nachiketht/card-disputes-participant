"""A date handed in by the caller.

Rules take this object when they need a date. They do not call the wall clock.
Nothing in the current draft path reads it: the deadline uses the reported date.
"""

from datetime import date


class FixedClock:
    def __init__(self, day: date) -> None:
        self.day = day

    @classmethod
    def from_iso(cls, value: str) -> "FixedClock":
        return cls(date.fromisoformat(value))
