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

# ADR-28. The procedure is data, not a constant

Context: CARD-PROCEDURE-01 is a module constant in the policy resolver, and the sentences quoted for classification, routing and missing fields are Python dicts beside hardcoded phrase lists. A renamed or reworded procedure is a code change in three modules.
Decision: the procedure Source ID comes from settings (flag, env, default CARD-PROCEDURE-01) and every consumer takes it from the resolver output. Each rule is a row in config/rules/decision_table.yaml carrying its signal, optional evidence condition, classification, missing fields, routing and the exact sentence prefixes it quotes; missing.yaml and routing.yaml carry their quote prefixes. The loader fails at startup on a classification, identifier or routing outside fields.py, a row without a quote, a duplicate id, or a quote not found in the loaded procedure text. One generated test per row. Adding a step is adding a row and its test.
Alternatives: keep the constants and edit code per change; let a model read the procedure and classify. Both rejected: the first is a redesign inside the freeze, the second lets the model set a scored field.
Brief check: the five classifications and three identifiers stay fixed by the contract. The twelve dev outputs are unchanged. A synthetic CARD-PROCEDURE-02 with reworded sentences passes with no code change.
Rollback: pin the previous procedure id and rows; the loader guards stay.
Status: accepted 2026-10-23. Not yet built.

# ADR-29. Prompts are versioned files behind an adapter

Context: the drafter prompt is a string inside a function. A prompt edit has no version, the trace cannot say which prompt wrote a narrative, and the bake-off cannot compare variants.
Decision: prompts live in prompts/<task>/v<N>.md with front matter (id, version, task, schema_version, variables, changelog). config/rules/prompts.yaml pins the active version per task. A PromptRegistry renders a prompt over a closed variable set (facts, verified quotes, flags, disclosures); an unknown variable or a raw document string is a render error. A PromptAdapter produces the provider wire shape. prompt_id, prompt_version and prompt_hash are recorded on every model span, in the agent approval, in metrics and in the bake-off report. Version files are immutable behind a manifest checked in CI. A change is a new version, a pin change and a CHANGES.md row. The template fallback is not a prompt.
Alternatives: a prompt string in code (untraceable); a prompt-management service (a new dependency for two prompts).
Brief check: scored fields are identical across every committed prompt version. No raw prose reaches the model. Logs never carry prompt text.
Rollback: move the pin back to the previous version.
Status: accepted 2026-10-23. Not yet built.

# ADR-30. Cross-case circuit breaker, supersedes ADR-24

Context: ADR-24 (plan v3.9, CR-09, client decision 4 of 2026-10-22) chose no circuit breaker, because the procedure fixes three total attempts per case and the audit is expected to show them (CARD-D07: 3 transaction attempts, CARD-D10: 2). The Week 10 hardening review asks for a breaker on every dependency-backed failure case. During a sustained outage, the no-breaker design spends three attempts on every case against a service known to be down.
Decision: one breaker per read dependency (transactions, merchant), counted in whole cases, not attempts. Closed: every case gets the full retry budget, unchanged. It opens only after `failure_threshold` consecutive cases exhausted all three attempts on the same dependency (default 3). Open: later cases skip that call and take the existing degradation path, the same one a failed read takes (transactions unavailable: amount and currency null, `manual_review`, outage sentence cited; merchant unavailable: merchant context unavailable). The narrative says the call was skipped by the breaker. After `cooldown_s` (default 30) one case is let through as a probe with its full three attempts; success closes the breaker, exhaustion reopens it. Settings in `config/rules/breaker.yaml`, overridable with `BREAKER_ENABLED`, `BREAKER_FAILURE_THRESHOLD`, `BREAKER_COOLDOWN_S`. Every transition and skip is a trace event. Code: `reliability/breaker.py`, used by `adapters/tools.py:read_with_retries`.
Why it respects the procedure: no case ever gets fewer than three attempts while the breaker lets a call through, and a skipped case takes the same documented degradation as an exhausted one, so no amount is invented and no outage is read as an empty history. With default settings the dev run never opens the breaker: CARD-D07 is the only exhausted case, so D07 still shows 3 attempts and D10 still shows 2, and evaluation stays 12/12 (`tests/integration/test_live_checkpoint.py`, `evidence/week10/04_scenarios.txt`).
Alternatives: keep ADR-24 (fails the hardening rubric, wastes attempts in a long outage); a per-attempt breaker that can cut attempts inside a case (breaks the procedure and the audit counts).
Consequence: a skipped case shows 0 attempts for that dependency in the audit. That only happens after an outage on earlier consecutive cases, and the trace records why.
Rollback: `BREAKER_ENABLED=0` or `enabled: false` restores ADR-24 behavior with no code change.
Status: accepted 2026-10-25. Built.

# ADR-31. Stdio context server, optional library

Context: a client that speaks the Model Context Protocol may call the same commands the CLI already runs. A network listener would be another place a draft could be reached. The base install should keep working when that library is absent.
Decision: `src/dispute_copilot/mcp_server.py` speaks stdio only. Startup calls `run_stdio_async`. There is no host, no port, and no HTTP route. An extra argument exits 2. The package `mcp` (this checkpoint resolved 2.3.0, class `MCPServer`) is an optional extra named `mcp`. `uv sync --locked` without that extra still installs the copilot. If the extra is missing, the module exits 1 and does not open a session. Each call wraps the existing functions (`prepare` and `write_draft` through the same case trace the CLI uses, `run_all_report`, `latest_draft`, `record_agent_review`, `submit_review`, `create_record`, trace reads, and `write_report`). The same case-id check, redactor, and approval gate apply. The call list has no approve, reject, or reset operation. This module does not read the fixture file. Complaint text and evidence quotes are returned as data marked `untrusted`. A failure the code expects is a protocol error whose text is a small JSON object (`code`, `message`, and `status` when the workflow returned one). The message has no traceback. A case id that fails the path check, or that the redactor flags, is not copied into that message. Every call writes a span named `mcp.tool` and a log event `mcp.call` that includes `latency_ms`.
Why this library: it already implements the stdio session and the error result. A hand-written JSON-RPC loop was the alternative and was not needed. An HTTP transport was rejected because it would bind a port.
The saved draft is a `get_draft` result, not a resource URI. The library attaches the requested URI to a resource error, which would repeat a rejected case id. The read-only resources are the policy guide and the output contract.
Status: implemented. CI installs the extra so the stdio session runs there. The container image was not rebuilt for this extra.

# ADR-32. Checklist, then human approval, joined by a fail-closed gate

Context: the procedure says a person approves the exact draft before a record is created. A checklist that passes is easy to mistake for that approval.
Decision: two steps, joined by `approval_gate` in `review_flow/gate.py`. Step one is `record_agent_review` (`dispute-copilot agent-review`). It stores `agent_approval` on the review handle. `passed` is true only when every checklist item passed. The reviewer note says a pass does not approve the case. `submit_review` posts the saved draft only when that object passed, names the same case, and hashes to the same draft. Otherwise it raises `CreateRejected` with status 400 and error `agent_approval_required`, and it does not post. Step two is the human command `tools/runtime.py approve`, outside this package. `create_record` re-fetches the review and calls `approval_gate`. The gate returns true only when the checklist passed, the case matches, the server hash matches, the review status is `approved`, and the reviewer is a non-empty string. Any other shape, including an exception inside the gate, returns false. A false gate raises `CreateRejected` with status 403 and error `approval_gate` and does not post. The local file is not the create body. ADR-6 still holds: this package has no approve command. A second human approval (ADR-14) is a separate rule and is not this gate.
Status: implemented. Code: `review_flow/agent_review.py`, `review_flow/gate.py`, `review_flow/__init__.py`, and `tools/runtime.py` for the human command.

# ADR-33. Container layout files, cloud choice still open

Context: rehearsal files describe one image and two containers that share localhost. Which cloud is used first has not been chosen. This note does not choose one.
Decision: the tree contains `Dockerfile`, `docker-compose.yml`, and `deploy/azure/containerapp.yaml`. They are a layout, not a choice of cloud.
- The image is `python:3.12-slim`. It copies the project and runs `pip install .`. `uv.lock` is copied and is not passed to that install. `config` and `prompts` are symlinked into the interpreter library directory. The default command is `python tools/runtime.py serve` with `--port 8765` and no host flag. The mock code binds `127.0.0.1`.
- Compose builds one image. The mock service mounts the fixture file and a state volume. The app service sets `network_mode: service:mock`, `MOCK_BASE_URL=http://127.0.0.1:8765`, and mounts `documents/` and `policies/` only. Its command is `sleep infinity`. It lists `env_file: .env`. That file is not in the repository. The compose file has no host flag.
- `deploy/azure/containerapp.yaml` names two containers, `mock` and `app`, in one app. It has no ingress field. Secrets are placeholders. The data mounts use AzureFile. The ledger and the submissions volumes use EmptyDir. The app's `MOCK_BASE_URL` is `http://127.0.0.1:8765`. The mock command has no host flag.
Which cloud is used first remains an open choice. These files do not settle it.
Status: the files are in the tree. `docker` was not installed in this session, so the image was not built and compose was not started. No cloud sign-in was run.

# Accepted, not built in this checkpoint

ADR-8, the ordered decision table, is implemented in `rules/classify.py`. The first match wins. These remain for a later checkpoint:

- ADR-9 through ADR-15 in the plan: claim-line search beyond labeled lines, ask-once, second approval, fixed-clock display, and the change-control freeze after the 12/12 line. The stdio wrapper is ADR-31. The container files are ADR-33; which cloud is used first is still open, and an image build was not run.
- Open assumptions, asked in `docs/FACILITATOR_QUESTIONS.md`: Q3 pass bar, Q4 assessor state path, Q6 withdrawn statement, Q7 out-of-range date, Q8 authorization edges, Q9 create after a non-ready routing.

# Process note

Branch protection on `initial-build` and `main` (required CI, one other reviewer) is a GitHub setting. It was not changed from this workspace. Turning it on is a team decision because it changes who can push.
