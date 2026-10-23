"""Decisions that govern the code. Status says whether this checkpoint implements them."""

# ADR-1. Deterministic code owns scored fields

Context: the brief scores policy, deadline, classification, missing fields, and routing exactly.
Decision: those values are computed in code from the policy files and tool payloads. A model is not called.
Alternatives: let a model classify. Rejected, because a wrong type fails the case and cannot be explained from the policy text.
Brief check: every development draft takes its type, day count, and amount from the procedure order, the policy file, and the tool payload.
Rollback: none. A model may later draft prose only, and the scored fields stay on this path.
Status: implemented. `rules/classify.py` applies the procedure order to every development case.

# ADR-2. One vocabulary module

Context: output names duplicated across files drift, and the evaluator is case-sensitive.
Decision: `src/dispute_copilot/fields.py` is the only module that spells contract names. `CONTRACT_VERSION` is `1.0` and is not written into the evaluator payload.
Alternatives: string literals at each call site.
Brief check: the lint gate fails if another source file spells one of those names.
Rollback: add a name in `fields.py` and bump the version. Do not scatter it.
Status: implemented.

# ADR-3. Two serializers

Context: the mock hashes compact JSON with non-ASCII preserved. The evaluator's tool text uses default spaces and default ASCII escaping.
Decision: `hash_canonical` and `citation_source_text` are separate functions. Create uses the first. Citation quotes are checked against the second.
Alternatives: one serializer. It fails either the hash or the quote check.
Brief check: golden byte tests lock both forms.
Rollback: none.
Status: implemented.

# ADR-4. Policy by reported date

Context: both policies say to select by Reported date, not transaction date or the system clock.
Decision: the resolver parses Effective from, Effective through, and the day counts. The deadline is reported date plus that count. Report day is day zero. A transaction date in another period is disclosed and does not change the selection.
Alternatives: select by transaction date, or by today's date. Both contradict the policy text.
Brief check: 2026-08-31 selects V1 and 2026-09-01 selects V2. A dropped-in later policy file is selected by its own dates.
Rollback: none without a written policy change.
Status: implemented. A date outside every file stops this checkpoint instead of applying the out-of-range branch, which is still an open question (Q7).

# ADR-5. Corpus by Source ID

Context: reading the mock fixture file would skip the tool exercise.
Decision: the corpus reads markdown under `policies/` and `documents/` from a configurable data root and indexes the Source ID header.
Alternatives: read the fixture file.
Brief check: a fixture file sitting beside the markdown is not loaded.
Rollback: none.
Status: implemented.

# ADR-6. Human approval is outside this process

Context: the procedure says case creation requires a person to approve the exact draft.
Decision: `run` writes a draft and stops. `submit-review` posts that saved draft. `create-record` re-fetches the review, checks the server hash, and posts the server draft. There is no approve command in this package.
Alternatives: approve inside the same command. That removes the human boundary.
Brief check: a pending review does not create a record, and the audit shows no blocked write because this process does not post.
Rollback: none.
Status: implemented for one review. The second-approval rule is accepted and not built (ADR-14).

# ADR-7. Retry the transaction read, do not invent an amount

Context: the procedure allows three total attempts and says not to treat a service failure as an empty history.
Decision: a 503 is retried up to three attempts. The cap is rejected above three. If the named transaction never arrives, the draft keeps the intake id and reported date, leaves amount and currency empty, and routes to manual review. No other transaction is substituted, and the transaction payload is not cited.
Alternatives: copy an amount from the complaint sentence, or stop after one failure.
Brief check: a persistent failure records three attempts, writes that empty-amount draft, and does not cite the transaction payload.
Rollback: none.
Status: implemented.

# ADR-16. No reply on the model question means no model

Context: a facilitator may not name an approved endpoint.
Decision: the question is written down with a reply date of 2026-10-23. No reply means the provider stays absent. This checkpoint already drafts from a template.
Alternatives: block the build, or call a model without a written answer.
Brief check: scored fields do not depend on a model. No model client is constructed.
Rollback: point a later provider at an approved endpoint after a written reply, and log it.
Status: default is in force. The provider code is not built.

# ADR-17. No reply on the mock question means the mock stays local

Context: the mock binds to loopback and the README says to keep it local.
Decision: the same reply date. No reply means the assessed mock stays on 127.0.0.1. The HTTP client does not trust proxy environment variables, so loopback traffic is not sent elsewhere.
Alternatives: publish the mock, or skip the question.
Brief check: the run commands take a base URL and the tests use a loopback port.
Rollback: a written reply can allow a loopback mock inside a later cloud task.
Status: default is in force. Cloud deployment is not built.

# ADR-27. A duplicate report with one returned transaction goes to manual review

Context: the procedure says to retrieve both transaction records for a duplicate charge. The transaction service can answer successfully and return only the named transaction. The current routing marks that case ready for review with no second-charge citation.
Decision: decision-table row 4b. When the classification is duplicate_charge, the tool call succeeded, the named transaction is present, and no second distinct row is returned, routing is manual review. Classification stays duplicate_charge, the deadline is still computed, amount and currency come from the named transaction, missing_information stays empty, the duplicate_transaction citation is omitted, the routing citation also quotes the retrieve-both sentence, and the narrative discloses the single returned id. Persistent failure and a missing named transaction (ADR-7) take precedence. This is a fixed rule, not a configurable degradation kind.
Alternatives: ready for review with a disclosure, which marks an uncorroborated claim ready; ambiguous, which the procedure reserves for authorization conflicts and which would null the deadline.
Brief check: no fabricated citation, deadline kept for a supported type, D02, D08 and D09 unchanged.
Rollback: edit row 4b and its generated test, logged as an R change.
Status: accepted 2026-10-23 (closes Q5). Not yet built.

# Accepted, not built in this checkpoint

ADR-8, the ordered decision table, is implemented in `rules/classify.py`. The first match wins. These remain for a later checkpoint:

- ADR-9 through ADR-15 and ADR-18 and ADR-19 in the plan: claim-line search beyond labeled lines, ask-once, second approval, stdio MCP wrapper, fixed-clock display, one container image, and the change-control freeze after the 12/12 line.
- Open assumptions, asked in `docs/FACILITATOR_QUESTIONS.md`: Q3 pass bar, Q4 assessor state path, Q6 withdrawn statement, Q7 out-of-range date, Q8 authorization edges, Q9 create after a non-ready routing.

# Process note

Branch protection on `initial-build` and `main` (required CI, one other reviewer) is a GitHub setting. It was not changed from this workspace. Turning it on is a team decision because it changes who can push.
