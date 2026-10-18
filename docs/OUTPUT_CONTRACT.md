# Evaluation output contract

    Submit one JSON array with exactly one object per requested case ID. Do not wrap it in Markdown. Keep your user-facing UI or reports in addition to this machine-readable output.

    Required fields:

    | Field | Type and semantics |
    |---|---|
    | case_id | String supplied by the input |
    | classification | One of unauthorized, duplicate_charge, service_not_received, ambiguous, unsupported |
    | routing | One of ready_for_review, request_information, manual_review |
    | policy_ids | All applicable policy/procedure source IDs; no stale or unrelated IDs |
    | facts | Object with the required fields shown below; unknown values use JSON null |
    | missing_information | Exact field identifiers for required missing information, or [] |
    | deadline | ISO date for supported card disputes; null for other capstones or unresolved classification |
    | citations | Array of objects: claim, source_id, quote; quote must be an exact excerpt from the named source |
    | follow_up_questions | Array of objects: field, question; at least one targeted question for each missing field |
    | narrative | Clear draft for the human, covering evidence, recommendation, uncertainty and limitations |

    Required fact fields and example values (not a universal answer):

    ```json
    {
  "transaction_id": "TX-D01",
  "amount": 117,
  "currency": "XCR",
  "reported_date": "2026-10-01"
}
    ```

    Allowed missing_information identifiers: authorization_confirmation, promised_service_date, purchase_receipt.
    Values are case-sensitive. Numbers remain JSON numbers. Missing fields and null are different: include every required fact even if null. Include citations to the applicable policy and procedure and the case evidence supporting the conclusion. Cite each contradictory source when escalating a conflict.
    Tool failure is documented in narrative and the observed server trace; it does not justify a fabricated source citation.
    Every missing field needs a specific question; no need to ask again for supplied information. Uncertainty must remain visible. A valid citation string alone does not prove that its quote supports its claim; the assessor checks this.
    `narrative` should contain the case summary and any customer-facing draft called for by the brief. Mark customer communications as drafts. Additional fields are allowed, but are also inspected for leakage and unsafe commitments.
