"""Authorization disagreement, then cash, then one claim type.

Two of unauthorized, duplicate, and service in the same complaint is ambiguous.
A cash or ATM complaint stays unsupported. Free text is used only for those
signals. Labeled authorization comes from the intake and follow-ups. An
attachment cannot enter the conflict row. Promised date and receipt lines are
read only after the service row matches alone, so the boilerplate on every
intake cannot change another type.
"""

import re
from dataclasses import dataclass

from dispute_copilot.fields import (
    AUTH_CONFIRMED,
    AUTH_DENIED,
    AUTH_NOT_SUPPLIED,
    Classification,
    MissingId,
)

_ATM = re.compile(r"\batm\b")
_TX_ID = re.compile(r"\bTX-[A-Za-z0-9-]+\b")
_SERVICE_WORD = re.compile(r"\b(?:service|delivery|order|goods|merchandise|item|shipment)\b")
_UNSUPPORTED = (
    "cash withdrawal",
    "did not dispense",
    "didn't dispense",
    "cash not dispensed",
    "did not dispense cash",
    "didn't dispense cash",
    "no cash came out",
    "cash did not come out",
    "cash didn't come out",
    "cash was not released",
    "no money was dispensed",
    "withdrawal was not paid",
    "failed to dispense cash",
    "cash machine did not dispense",
    "cashpoint did not dispense",
)
_RECOGNITION = (
    "do not recognize",
    "don't recognize",
    "did not authorize",
    "didn't authorize",
    "do not recognise",
    "don't recognise",
    "not my purchase",
    "not my transaction",
    "did not make this purchase",
    "didn't make this purchase",
    "never authorized",
    "never authorised",
    "unauthorized charge",
    "unauthorised charge",
    "do not recall this charge",
    "don't recall this charge",
)
_DUPLICATE = (
    "twice",
    "two charges",
    "extra charge",
    "charged twice",
    "billed twice",
    "posted twice",
    "double charge",
    "double charged",
    "duplicate charge",
    "second charge",
    "charged me twice",
    "same purchase twice",
    "two times",
    "duplicate transaction",
    "billed me twice",
)
_SERVICE_GAP = (
    "not been delivered",
    "not delivered",
    "was not supplied",
    "not been supplied",
    "was not delivered",
    "wasn't delivered",
    "never delivered",
    "never supplied",
    "wasn't supplied",
    "have not received",
    "haven't received",
    "did not receive",
    "didn't receive",
    "never received",
    "did not arrive",
)
ATTACHMENT_MISSING = "missing"
_CUSTOMER_AUTH = frozenset({AUTH_CONFIRMED, AUTH_NOT_SUPPLIED})


@dataclass(frozen=True)
class Decision:
    classification: Classification
    missing: tuple[MissingId, ...]
    conflict: bool


def classify(
    complaint: str,
    authorization: str,
    other_authorizations: list[str],
    promised_service_date: str | None,
    purchase_receipt: str | None,
    attachment_status: str | None,
) -> Decision:
    text = complaint.casefold()
    statements = [authorization, *other_authorizations]
    if len(statements) > 1 and len(set(statements)) > 1:
        return Decision(Classification.AMBIGUOUS, (), True)
    if _unsupported(text):
        return Decision(Classification.UNSUPPORTED, (), False)
    heard = authorization in _CUSTOMER_AUTH or authorization == AUTH_DENIED
    unauthorized = authorization == AUTH_DENIED or (
        authorization == AUTH_NOT_SUPPLIED and _denies_recognition(text)
    )
    duplicate = heard and _duplicate(text, complaint)
    service = heard and _service(text)
    if sum((unauthorized, duplicate, service)) > 1:
        return Decision(Classification.AMBIGUOUS, (), False)
    if unauthorized and authorization == AUTH_DENIED:
        return Decision(
            Classification.UNAUTHORIZED,
            missing_for(Classification.UNAUTHORIZED, authorization, promised_service_date, purchase_receipt, attachment_status),
            False,
        )
    if unauthorized:
        return Decision(
            Classification.UNAUTHORIZED,
            missing_for(Classification.UNAUTHORIZED, authorization, promised_service_date, purchase_receipt, attachment_status),
            False,
        )
    if duplicate:
        return Decision(
            Classification.DUPLICATE_CHARGE,
            missing_for(Classification.DUPLICATE_CHARGE, authorization, promised_service_date, purchase_receipt, attachment_status),
            False,
        )
    if service:
        return Decision(
            Classification.SERVICE_NOT_RECEIVED,
            missing_for(
                Classification.SERVICE_NOT_RECEIVED,
                authorization,
                promised_service_date,
                purchase_receipt,
                attachment_status,
            ),
            False,
        )
    return Decision(Classification.AMBIGUOUS, (), False)


def missing_for(
    classification: Classification,
    authorization: str,
    promised_service_date: str | None,
    purchase_receipt: str | None,
    attachment_status: str | None,
) -> tuple[MissingId, ...]:
    """Missing fields for the final class. Every approach calls this.

    Unauthorized with authorization not supplied asks for that confirmation.
    A labeled denial does not. A service class with authorization not supplied
    lists that confirmation first, then the service fields.
    """
    if classification == Classification.SERVICE_NOT_RECEIVED:
        missing = _service_fields(promised_service_date, purchase_receipt, attachment_status)
        if authorization == AUTH_NOT_SUPPLIED:
            return (MissingId.AUTHORIZATION_CONFIRMATION, *missing)
        return missing
    if classification == Classification.UNAUTHORIZED and authorization == AUTH_NOT_SUPPLIED:
        return (MissingId.AUTHORIZATION_CONFIRMATION,)
    return ()


def _unsupported(text: str) -> bool:
    return _ATM.search(text) is not None or any(phrase in text for phrase in _UNSUPPORTED)


def _denies_recognition(text: str) -> bool:
    return any(phrase in text for phrase in _RECOGNITION)


def _duplicate(text: str, complaint: str) -> bool:
    if any(phrase in text for phrase in _DUPLICATE):
        return True
    return len(set(_TX_ID.findall(complaint))) >= 2


def _service(text: str) -> bool:
    return _SERVICE_WORD.search(text) is not None and any(phrase in text for phrase in _SERVICE_GAP)


def _blank(value: str | None) -> bool:
    return value is None or value.strip() == "" or value == AUTH_NOT_SUPPLIED


def _service_fields(
    promised_service_date: str | None,
    purchase_receipt: str | None,
    attachment_status: str | None,
) -> tuple[MissingId, ...]:
    missing: list[MissingId] = []
    if _blank(promised_service_date):
        missing.append(MissingId.PROMISED_SERVICE_DATE)
    if _blank(purchase_receipt) or attachment_status == ATTACHMENT_MISSING:
        missing.append(MissingId.PURCHASE_RECEIPT)
    return tuple(missing)
