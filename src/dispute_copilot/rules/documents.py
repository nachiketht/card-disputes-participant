"""Read labeled lines and ignore everything else.

Prose in an intake, a follow-up, or an attachment is evidence text, not an
instruction. An attachment is only allowed to contribute its status line, so a
labeled authorization line planted there cannot change the customer's account.
A missing authorization line is not supplied. This module does not guess.
"""

from dispute_copilot.fields import (
    AUTH_NOT_SUPPLIED,
    FACT_REPORTED_DATE,
    FACT_TRANSACTION_ID,
    MissingId,
)

INTAKE_LABELS = frozenset(
    {
        "Source ID",
        "Reported date",
        "Transaction ID",
        "Customer reference",
        "Authorization",
        "Promised service date",
        "Purchase receipt",
        "Delivery note",
        "Submission reference",
        "Attachment status",
    }
)


def labeled_lines(text: str, allowed: frozenset[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        if key in allowed and key not in found:
            found[key] = value.strip()
    return found


def complaint_text(text: str) -> str:
    """Opening complaint paragraph, excluding the heading and the form lines."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if kept:
                break
            continue
        key = stripped.split(": ", 1)[0] if ": " in stripped else None
        if key in INTAKE_LABELS:
            if kept:
                break
            continue
        kept.append(stripped)
    quote = "\n".join(kept)
    if not quote or quote not in text:
        raise ValueError("complaint text is not an excerpt of the document")
    return quote


def parse_intake(text: str) -> dict:
    raw = labeled_lines(text, INTAKE_LABELS)
    authorization = raw.get("Authorization") or AUTH_NOT_SUPPLIED
    return {
        FACT_REPORTED_DATE: raw.get("Reported date"),
        FACT_TRANSACTION_ID: raw.get("Transaction ID"),
        "authorization": authorization,
        MissingId.PROMISED_SERVICE_DATE.value: raw.get("Promised service date"),
        MissingId.PURCHASE_RECEIPT.value: raw.get("Purchase receipt"),
        "delivery_note": raw.get("Delivery note"),
        "complaint": complaint_text(text),
    }


def parse_followup(text: str) -> dict:
    raw = labeled_lines(text, frozenset({"Authorization"}))
    return {"authorization": raw.get("Authorization") or AUTH_NOT_SUPPLIED}


def parse_attachment(text: str) -> dict:
    raw = labeled_lines(text, frozenset({"Attachment status"}))
    return {"attachment_status": raw.get("Attachment status")}


def line_starting(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return None
