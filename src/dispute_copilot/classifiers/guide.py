"""Shared class text for the classifier prompt and the ranker payload."""

from dispute_copilot.fields import Classification

UNCLEAR = "multiple_or_unclear"

_UNAUTHORIZED = Classification.UNAUTHORIZED.value
_DUPLICATE = Classification.DUPLICATE_CHARGE.value
_SERVICE = Classification.SERVICE_NOT_RECEIVED.value
_AMBIGUOUS = Classification.AMBIGUOUS.value
_UNSUPPORTED = Classification.UNSUPPORTED.value
AMBIGUOUS_OPTION = "ambiguous_or_unclear"

_CRITERIA = {
    _UNAUTHORIZED: (
        "Did not make or authorize this purchase, including fraud, not my charge, "
        "not me, not mine, or unknown merchant. Short or noisy denials still count. Not ATM cash."
    ),
    _DUPLICATE: (
        "Same purchase billed more than once: twice, two charges, extra charge, "
        "billed two times. Not a missing delivery."
    ),
    _SERVICE: (
        "Paid work or goods that never arrived or was never performed: never got it, "
        "still nothing, nobody came, cancelled. A short note still counts. Not ATM or wrong-item quality."
    ),
    _UNSUPPORTED: (
        "ATM or cash machine that did not dispense: withdrew, machine gave no cash, "
        "machine not give money. Not a card-purchase denial and not missing service."
    ),
    _AMBIGUOUS: (
        "Two concrete classes both apply, or a quality or wrong-item issue such as "
        "wrong colour. Not a short denial, non-delivery, or ATM."
    ),
    AMBIGUOUS_OPTION: (
        "Use only when no concrete class fits and there is no dispute claim. "
        "Do not use for short or noisy denials, non-delivery, or ATM cash."
    ),
}

_LABELS = {
    _UNAUTHORIZED: "did not authorize; fraud; not my charge; not mine",
    _DUPLICATE: "billed more than once for one purchase",
    _SERVICE: "paid, never arrived or performed; never got it; cancelled",
    _AMBIGUOUS: "two types, or quality/wrong item",
    _UNSUPPORTED: "ATM or cash machine did not dispense",
}

_INSTRUCTIONS = (
    "Pick one concrete class. Short denials, non-delivery notes, and ATM cash "
    "still count. Use ambiguous or unclear only when two classes fit or there is no claim."
)

_EXAMPLES = f"""Examples:
- "not my charge" -> {_UNAUTHORIZED}
- "not mine" -> {_UNAUTHORIZED}
- "fraud" -> {_UNAUTHORIZED}
- "unknown merchant" -> {_UNAUTHORIZED}
- "never got it" -> {_SERVICE}
- "Still nothing after paying." -> {_SERVICE}
- "they cancelled on the day" -> {_SERVICE}
- "They charged me twice for one order." -> {_DUPLICATE}
- "The machine gave me no cash but took the money." -> {_UNSUPPORTED}
- "Withdrew, machine not give money." -> {_UNSUPPORTED}
- "It arrived in the wrong colour." -> {_AMBIGUOUS}
"""


def ranker_criteria() -> dict[str, str]:
    return dict(_CRITERIA)


def ranker_instructions() -> str:
    return _INSTRUCTIONS


def ranker_state(complaint: str) -> str:
    """Complaint plus compact examples. Class criteria stay on the question."""
    return "Assign one class.\n\n" + _EXAMPLES + "\nComplaint:\n" + complaint


def category_lines() -> list[str]:
    lines = [f"{name}: {_LABELS[name]}" for name in (
        _UNAUTHORIZED,
        _DUPLICATE,
        _SERVICE,
        _AMBIGUOUS,
        _UNSUPPORTED,
    )]
    lines.append(f"{UNCLEAR}: more than one class, or not clear")
    return lines
