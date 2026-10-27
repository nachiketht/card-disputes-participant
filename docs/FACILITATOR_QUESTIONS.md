# Questions for the facilitator

Sent 2026-10-21. Please reply by 2026-10-23.

The mock stays on loopback. CI and the default classification approach do not call a model. Claude Haiku 4.5 (`claude-haiku-4-5`) is the checklist judge when `FEATURE_LLM_REVIEW=block`, and the classifier on approach 2 and approach 3. The host is not written in this file. Q1 is still open for which host a cohort run may use.

1. Which model endpoint and credentials are cohort-approved, is a local model allowed in the assessed demo, and is usage logging required? Default: no model.
2. Is local Docker acceptable as the deployment environment if cloud access is limited? May the mock run inside a cloud task, loopback only, with no ingress? Default: the mock stays local.
3. Confirm the 70/100 pass bar and that all six critical gates apply.
4. Will the assessor use a non-default state path, and do they reset state before the assessed run?
5. When a duplicate-charge report is missing the second transaction, is disclosure with ready-for-review acceptable, or should the case go to manual review? Standing rule: a duplicate stands only when the fetched transactions contain two charges with the same merchant id, amount, and currency. Without that pair the class does not stand, the deadline is cleared, and the route is `manual_review`. A transaction read that is unavailable keeps `duplicate_charge` and uses the unavailable route. The check runs on every approach.
6. If a conflicting customer statement is withdrawn, should the case be classified from the remaining statement, or stay ambiguous and go to manual review?
7. For a reported date outside every policy, what are the expected policy ids, deadline, and routing? Current assumption, not yet implemented: procedure only, null deadline, manual review.
8. Authorization edges. (a) Service-not-received wording with authorization denied: the class is unauthorized, because that check comes first, and the case is not sent to a model. (b) Duplicate wording with authorization not supplied: the rules class is `duplicate_charge` and that class does not add an authorization-confirmation field. The duplicate guard still requires two exact charges; without that pair the class does not stand and the route is `manual_review`. (c) Service wording with authorization not supplied: the class is `service_not_received`, and the missing fields list authorization confirmation first.
9. May a record be created for a manual-review or request-information case once a person approves that exact draft? Current assumption: yes. Create follows approval, not routing.
