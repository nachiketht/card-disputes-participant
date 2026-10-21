# Questions for the facilitator

Sent 2026-10-21. Please reply by 2026-10-23.

If Q1 and Q2 have no reply by that date, the default stands: no model, and the mock stays local. The path in this checkpoint already follows that default.

1. Which model endpoint and credentials are cohort-approved, is a local model allowed in the assessed demo, and is usage logging required? Default: no model.
2. Is local Docker acceptable as the deployment environment if cloud access is limited? May the mock run inside a cloud task, loopback only, with no ingress? Default: the mock stays local.
3. Confirm the 70/100 pass bar and that all six critical gates apply.
4. Will the assessor use a non-default state path, and do they reset state before the assessed run?
5. When a duplicate-charge report is missing the second transaction, is disclosure with ready-for-review acceptable, or should the case go to manual review?
6. If a conflicting customer statement is withdrawn, should the case be classified from the remaining statement, or stay ambiguous and go to manual review?
7. For a reported date outside every policy, what are the expected policy ids, deadline, and routing? Current assumption, not yet implemented: procedure only, null deadline, manual review.
8. Authorization edges. (a) Service-not-received wording with authorization denied: is unauthorized expected because that check comes first? (b) Duplicate wording with authorization not supplied: we expect duplicate_charge and no missing authorization field, because the procedure does not require authorization for duplicates. (c) Service wording with authorization not supplied: we expect service_not_received plus a request for authorization, because the procedure says an authorized purchase. Are (b) and (c) the expected readings?
9. May a record be created for a manual-review or request-information case once a person approves that exact draft? Current assumption: yes. Create follows approval, not routing.
