"""Routing precedence from the procedure.

Manual review wins over a request for information. Missing fields stay listed
either way. The first draft does not escalate a request into manual review.
"""

from dispute_copilot.fields import Classification, Routing


def choose_routing(
    classification: Classification,
    missing: tuple[object, ...],
    transactions_unavailable: bool,
) -> Routing:
    if classification in {Classification.UNSUPPORTED, Classification.AMBIGUOUS} or transactions_unavailable:
        return Routing.MANUAL_REVIEW
    if missing:
        return Routing.REQUEST_INFORMATION
    return Routing.READY_FOR_REVIEW
