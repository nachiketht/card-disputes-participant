"""Draft one question per missing field. This process does not send it."""

from dispute_copilot.fields import QUESTION_FIELD, QUESTION_TEXT, MissingId

_PROMPTS = {
    MissingId.AUTHORIZATION_CONFIRMATION: "Please confirm whether you authorized this card purchase.",
    MissingId.PROMISED_SERVICE_DATE: "What date was this service promised to you?",
    MissingId.PURCHASE_RECEIPT: "Please provide the purchase receipt for this service.",
}


def follow_up_questions(missing: tuple[MissingId, ...] | list[MissingId]) -> list[dict]:
    return [{QUESTION_FIELD: item, QUESTION_TEXT: _PROMPTS[item]} for item in missing]
