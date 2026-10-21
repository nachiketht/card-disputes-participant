"""Decisions that govern the code. Status says whether this checkpoint implements them."""

# ADR-1. Deterministic code owns scored fields

Context: the brief scores policy, deadline, classification, missing fields, and routing exactly.
Decision: those values are computed in code from the policy files and tool payloads. A model is not called.
Alternatives: let a model classify. Rejected, because a wrong type fails the case and cannot be explained from the policy text.
Brief check: this checkpoint's drafts for a denied purchase use the policy day count and the tool amount.
Rollback: none. A model may later draft prose only, and the scored fields stay on this path.
Status: implemented for the denied-authorization path. The other dispute types are not classified yet.

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
Decision: a 503 is retried up to three attempts. The cap is rejected above three. If the named transaction never arrives, this checkpoint writes nothing and does not fill in an amount.
Alternatives: copy an amount from the complaint sentence, or stop after one failure.
Brief check: a persistent failure records three attempts and leaves no draft.
Rollback: none.
Status: implemented as a hard stop. The later null-amount manual-review draft is not in this checkpoint.

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

# Accepted, not built in this checkpoint

These are recorded so the next checkpoint does not reopen them. They are not in the code yet.

- ADR-8 through ADR-15 and ADR-18 and ADR-19 in the plan: ordered decision table, claim-line search, memory-only source registry, redaction at every later egress, ask-once, second approval, stdio MCP wrapper, fixed-clock display, one container image, and the change-control freeze after the 12/12 line.
- Open assumptions, asked in `docs/FACILITATOR_QUESTIONS.md`: Q3 pass bar, Q4 assessor state path, Q5 missing duplicate transaction, Q6 withdrawn statement, Q7 out-of-range date, Q8 authorization edges, Q9 create after a non-ready routing.

# Process note

Branch protection on `initial-build` and `main` (required CI, one other reviewer) is a GitHub setting. It was not changed from this workspace. Turning it on is a team decision because it changes who can push.
