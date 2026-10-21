"""Validate a draft before it is written.

The evaluator accepts extra keys, but this model refuses them. A stray key is
how a token or an unreviewed field would leave the process. The version
constant stays out of the payload on purpose.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dispute_copilot.errors import ContractError
from dispute_copilot.fields import (
    FACT_AMOUNT,
    NARRATIVE,
    POLICY_IDS,
    Classification,
    MissingId,
    Routing,
)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class FollowUpQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: MissingId
    question: str = Field(min_length=10)


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str | None
    amount: int | float | None
    currency: str | None
    reported_date: str | None

    @field_validator(FACT_AMOUNT, mode="before")
    @classmethod
    def amount_is_numeric(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("amount must be a number or null")
        return value


class DisputeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1)
    classification: Classification
    routing: Routing
    policy_ids: list[str] = Field(min_length=1)
    facts: Facts
    missing_information: list[MissingId]
    deadline: str | None
    citations: list[Citation] = Field(min_length=1)
    follow_up_questions: list[FollowUpQuestion]
    narrative: str

    @field_validator(NARRATIVE)
    @classmethod
    def narrative_is_a_draft(cls, value: str) -> str:
        if len(value.strip()) < 20:
            raise ValueError("narrative is too short to review")
        return value

    @field_validator(POLICY_IDS)
    @classmethod
    def policy_ids_are_present(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("policy id is blank")
        return value


def validate_draft(payload: dict) -> dict:
    try:
        model = DisputeDraft.model_validate(payload)
    except ValidationError as exc:
        raise ContractError(str(exc)) from exc
    return model.model_dump(mode="json")
