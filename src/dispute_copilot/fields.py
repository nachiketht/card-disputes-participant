"""The only module that spells contract names.

Every other module imports these constants. A lint gate rejects a source
string whose value is one of them, so a new output name cannot appear ad hoc.
CONTRACT_VERSION is not written into the evaluator payload.
"""

from enum import Enum

CONTRACT_VERSION = "1.0"

CASE_ID = "case_id"
CLASSIFICATION = "classification"
ROUTING = "routing"
POLICY_IDS = "policy_ids"
FACTS = "facts"
MISSING_INFORMATION = "missing_information"
DEADLINE = "deadline"
CITATIONS = "citations"
FOLLOW_UP_QUESTIONS = "follow_up_questions"
NARRATIVE = "narrative"

FACT_TRANSACTION_ID = "transaction_id"
FACT_AMOUNT = "amount"
FACT_CURRENCY = "currency"
FACT_REPORTED_DATE = "reported_date"

CITATION_CLAIM = "claim"
CITATION_SOURCE_ID = "source_id"
CITATION_QUOTE = "quote"

QUESTION_FIELD = "field"
QUESTION_TEXT = "question"

AUTH_DENIED = "denied"
AUTH_CONFIRMED = "confirmed"
AUTH_NOT_SUPPLIED = "not supplied"

OUTPUT_FIELDS = (
    CASE_ID,
    CLASSIFICATION,
    ROUTING,
    POLICY_IDS,
    FACTS,
    MISSING_INFORMATION,
    DEADLINE,
    CITATIONS,
    FOLLOW_UP_QUESTIONS,
    NARRATIVE,
)

FACT_FIELDS = (
    FACT_TRANSACTION_ID,
    FACT_AMOUNT,
    FACT_CURRENCY,
    FACT_REPORTED_DATE,
)


class Classification(str, Enum):
    UNAUTHORIZED = "unauthorized"
    DUPLICATE_CHARGE = "duplicate_charge"
    SERVICE_NOT_RECEIVED = "service_not_received"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class Routing(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    REQUEST_INFORMATION = "request_information"
    MANUAL_REVIEW = "manual_review"


class MissingId(str, Enum):
    AUTHORIZATION_CONFIRMATION = "authorization_confirmation"
    PROMISED_SERVICE_DATE = "promised_service_date"
    PURCHASE_RECEIPT = "purchase_receipt"


def contract_literals() -> frozenset[str]:
    """Strings that may appear as literals only in this module."""
    values = set(OUTPUT_FIELDS)
    values.update(FACT_FIELDS)
    values.update(
        {
            CITATION_CLAIM,
            CITATION_SOURCE_ID,
            CITATION_QUOTE,
            QUESTION_FIELD,
            QUESTION_TEXT,
        }
    )
    values.update(item.value for item in Classification)
    values.update(item.value for item in Routing)
    values.update(item.value for item in MissingId)
    return frozenset(values)
