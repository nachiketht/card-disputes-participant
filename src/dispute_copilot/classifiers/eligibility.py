"""When a model may be asked, and which class that answer may keep.

The hard cases stay on the rules result. A proposed class is checked against
the labeled authorization before it becomes the final class.
"""

from dispute_copilot.fields import AUTH_CONFIRMED, AUTH_DENIED, AUTH_NOT_SUPPLIED, Classification
from dispute_copilot.rules.classify import Decision

_OPEN_LABEL = frozenset({AUTH_CONFIRMED, AUTH_NOT_SUPPLIED})

_BLOCKED = frozenset(
    {
        Classification.UNSUPPORTED,
        Classification.DUPLICATE_CHARGE,
        Classification.SERVICE_NOT_RECEIVED,
    }
)


def model_allowed(decision: Decision, authorization: str) -> bool:
    """Ambiguous with no conflict, or unauthorized from complaint wording only."""
    if authorization == AUTH_DENIED or decision.conflict:
        return False
    if decision.classification in _BLOCKED:
        return False
    if decision.classification == Classification.AMBIGUOUS:
        return True
    return decision.classification == Classification.UNAUTHORIZED and authorization == AUTH_NOT_SUPPLIED


def accept_proposed_class(proposed: Classification, authorization: str) -> Classification | None:
    """Return the class when the labeled authorization allows it.

    None means the proposal does not stand. The caller sends that case to
    manual review. A denied label is not a model case. The only class this
    function will accept with that label is unauthorized, and the approaches
    do not call a model there. Any other class with a denied label is a hard
    failure.
    """
    if authorization == AUTH_DENIED:
        assert proposed == Classification.UNAUTHORIZED, "a denied label cannot take this class"
        return proposed
    if proposed == Classification.UNAUTHORIZED:
        if authorization == AUTH_NOT_SUPPLIED:
            return proposed
        return None
    if proposed in {Classification.DUPLICATE_CHARGE, Classification.SERVICE_NOT_RECEIVED}:
        if authorization in _OPEN_LABEL:
            return proposed
        return None
    return proposed
