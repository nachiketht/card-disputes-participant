"""Block outcome promises in the customer-message section.

The check is a phrase list, not a model. A hit drops the model text and the
drafter uses the template message instead.
"""

_BLOCKED = (
    "refund",
    "reimburs",
    "eligib",
    "guarantee",
    "we promise",
    "you will receive",
    "money back",
    "we will approve",
    "your dispute is approved",
    "case is approved",
)


def rejects(text: str) -> bool:
    folded = text.casefold()
    return any(phrase in folded for phrase in _BLOCKED)
