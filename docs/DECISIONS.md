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

# ADR-27. A duplicate stands only when two fetched charges match

Context: a duplicate report is supposed to have two transaction records. A successful read can return a single row. An earlier note kept the duplicate class and only changed the route.
Decision: one shared check, used by every approach, including the plain rules. A final class of duplicate_charge stands only when the already-fetched transactions contain at least two charges with the same merchant id, amount, and currency. If they do not, the class does not stand: classification becomes ambiguous, the deadline is cleared, and routing is manual review. Amount and currency still come from the named transaction when it is present. If the transaction read is unavailable, the class stays duplicate_charge and the existing unavailable path applies: manual review, with the deadline still calculated. Persistent failure and a missing named transaction (ADR-7) still take that unavailable path.
Alternatives: keep the class and only change the route, which leaves an uncorroborated duplicate marked as that type.
Status: implemented. Code: `classifiers/guard.py`, called from `classifiers/engine.py` and `workflow.py`.

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

# ADR-33. Container layout, Azure running

Context: one image and two containers share localhost. The mock stays on `127.0.0.1`. The app never approves.
Decision: Azure Container Apps is the deployment that is running. `docs/CLOUD_BREAKDOWN.md` and `docs/AZURE_DEPLOY.md` record the names that were supplied: region West US 3, resource group `card-disputes-rg`, registry `pod3disputes`, image `card-disputes:promptfix` (code only), storage account `pod3disputesdata`, shares `mock-data` (`dataset.json` only, mock mount `/data`) and `app-data` (`documents/` and `policies/`, app mount `/data`), environment `card-disputes-env`, app `card-disputes`, one replica, no public ingress. Container `mock` listens on 8765 and keeps its ledger in temporary storage at `/state`. Container `app` writes drafts to temporary storage at `/submissions` and calls `http://127.0.0.1:8765`. The app cannot see `dataset.json`. A restart clears the ledger and the drafts. Files on the shares stay. The judge model id is `LLM_MODEL` in `.env` (currently `claude-haiku-5-5`). The host is `AZURE_BASE_URL` in `.env` (secret `azure-base-url`). The key is secret `llm-api-key` (not in git). When the call cannot be made, the checklist is still saved and submit stays available, with a notice that names the missing setting. The note does not approve. Approaches 2 and 3 may call the same model on an eligible case. Human approval is `az containerapp exec` into the `mock` container, `tools/runtime.py approve`, state `/state/run.sqlite`. The subscription id is not written in the docs.
The repo still has `Dockerfile`, `docker-compose.yml`, and `deploy/azure/containerapp.yaml`. The image is `python:3.12-slim` and runs `pip install .`. `uv.lock` is copied and is not passed to that install. Compose has no host flag. The container-app file has no ingress field.
Cost cells in `docs/CLOUD_BREAKDOWN.md` are unmeasured.
Status: names above are the running deployment as supplied. This session did not sign in to `<subscription>` and did not query the app. `docker` was not installed here, so the image was not built and compose was not started.

# ADR-34. Read-only status page on loopback

Context: traces, JSON logs, metrics, and audit events are already files. An operator needs one place to see latency, cost, injections, and review state. Sending those files to a hosted metrics product would copy case data off the machine and add a dependency this checkpoint does not need.
Decision: `dispute-copilot dashboard` builds one HTML page from `REPORTS_DIR`. `--export PATH` writes that file and exits. Without it, the process serves the page on `127.0.0.1` only. There is no host flag. The default port is 8754. The handler answers GET `/` and GET `/health`. POST, PUT, PATCH, and DELETE return 405. The page does not approve, reject, reset, or post. Token prices come from `config/rules/cost.yaml` as USD per million tokens. A listed model is `estimate from list price`. A model with no entry is `no price configured for <model>`. Compute cost is `estimate, unmeasured` unless that file contains a measured `usd` figure. `--label` or `RUN_LABEL` tags a run. A case and a model call store `where` from that label, or from the host class of the model endpoint when no label is set. The page shows one row per place and model the saved runs contain. The same redactor used for logs runs on the page before it is written. `LOG_STDOUT=1` copies JSON log lines to stdout. The stdio server does not do that, because stdout is its protocol. On the container, `REPORTS_DIR` is `/submissions/reports`, so traces sit on the same temporary disk as the drafts. Copy them out with `az containerapp exec`. Do not add ingress for this page.
Alternatives: a hosted metrics product (copies case data); binding every interface (the mock rule is loopback, and this page is the same kind of local view).
Status: implemented. Code: `observability/board.py`, `observability/cost.py`, `dispute-copilot dashboard`.

# ADR-35. Local checks only, no deploy job

Context: the workflow ran lint and the test suite. Later work added a status page, a stdio server, adversarial cases, and a saved development report. A cloud deploy from the workflow would need a registry login and a chosen deploy path.
Decision: `.github/workflows/ci.yml` has six local jobs: `lint`, `test`, `evaluate`, `security`, `secrets`, and `image`. Those names are the ones to require in branch protection. `evaluate` starts the mock on `127.0.0.1` with a temporary state file, runs `run-all`, and fails unless `tools/evaluate.py` passes every development case and `reports/dev.json` matches that report. `image` builds the local image and drafts one case through the mock inside the image. It does not push. `secrets` scans tracked files. The workflow sets `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off`. The six local jobs use no repository secrets and do not call a model endpoint. A push does not update the running revision. ADR-38 adds a live exec check on that revision, and a separate manual image update that a push does not start.
Status: the workflow file is in the tree. This workspace had no `docker` binary, so the `image` job was not executed here. The `evaluate` job also runs the scorecard from ADR-36.

# ADR-36. One scorecard over development and synthetic cases

Context: `tools/evaluate.py` scores the development gold file. Synthetic cases live in the test tree. Citation checks inside that evaluator do not say that a matching quote is not semantic support.
Decision: `evals/sets/` holds versioned sets. `dispute-copilot evals` and `make evals` print one scorecard and write `reports/evals.json`. Minimums are `config/rules/evals.yaml`. A missed minimum exits 1. The judge stays off. Citation support checks that the source id exists and the quote is in that text. Markdown is read from the files. Payload texts come from `tools/source_index.py`. The report states that this check does not judge semantic support. The command does not approve.
Status: implemented. Code: `evals/scorecard.py`, `evals/citations.py`, `tools/run_evals.py`.

# ADR-37. Guardrail events on protections that already decide

Context: untrusted-text marking, the phrase list, redaction, contract validation, the loopback mock rule, and the two-stage approval gate already exist in separate modules. A status page could not show them as one list.
Decision: `guardrails.record` writes `guardrail.input`, `guardrail.output`, `guardrail.boundary`, or `guardrail.approval` when one of those protections fires, and sets attribute `guardrail` on the open span. Metrics count the four kinds. The status page security panel lists them. The module does not approve and does not change a scored field.
Status: implemented. Code: `guardrails.py`.

# ADR-38. Live container check on push, image update by hand

Context: the running app is Azure Container Apps, one replica, no public ingress. A push should exercise that replica. Replacing the revision on every push would clear the temporary ledger and the temporary drafts. Writing `run-all` against `/state/run.sqlite` would change call counts and can change later fault behavior. The app container cannot see `dataset.json`. That split stays.
Decision: job `azure` in `.github/workflows/ci.yml` runs on `push` only, after the six local jobs. It signs in with GitHub OIDC (`azure/login`, client id, tenant id, subscription id from repository secrets). A missing sign-in, and a fork, skip with a notice and exit 0. The job uses `az containerapp exec` into `card-disputes` / `card-disputes-rg`. It health-checks the live mock on `127.0.0.1:8765` and checks that the app container cannot read `/data/dataset.json`. Drafts run against a second mock on `127.0.0.1:8766` whose state file is under `/tmp`, never `/state/run.sqlite`. App drafts and reports stay under `/tmp`. The child sets `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off` and drops `LLM_API_KEY`. `agent-review` is the checklist. It does not post. `tools/evaluate.py` runs in the mock container, which is the container that mounts the fixture. The job does not approve, create a record, or reset. Cleanup stops only a process whose command line is that side mock, then deletes the `/tmp` files. If cleanup does not finish, the log says the demo ledger was not opened. A missing result, an unreachable container, a failed development case, a failed checklist row, or a failed evaluation fails the job. There is no fallback to port 8765. Traces, metrics, and the evaluation are the artifact `azure`. Concurrency group `azure-live-container` does not cancel, so two pushes do not share the replica. Push does not call `az containerapp update`. `.github/workflows/azure-deploy.yml` is `workflow_dispatch` only. It is unverified until a person runs it. The exec role is scoped to the resource group and does not include secret reads. Setup commands are in `docs/CI.md`.
Alternatives: calling the live mock on 8765 for `run-all` (writes the demo ledger); adding ingress or a new endpoint (the app stays private); redeploying on every push (clears the ledger and the drafts); one identity for exec and for image update (the every-push role would be able to change the revision and read secrets).
Status: the workflow files and `tools/azure_live.py` are in the tree. A local rehearsal kept a seeded pending review and a calls count of 7, and scored 12 of 12. This workspace did not sign in and did not exec into the replica.

# ADR-39. Request path and replay from saved steps

Context: a reader needs the order one case actually took, including a failed read, a later attempt, the routing branch, and an injection flag, without running the case again. The first drawing was a vertical list, and the page led with every table.
Decision: the request path is a left-to-right diagram in fixed columns: intake, evidence side by side, ledger, rules and routing, draft, checklist, then a separate human stage. A saved name outside that list still appears. Retries are chips on the step. A failed read has a dashed line to the route taken. Routing shows three outcomes, with the chosen one marked. An injection flag is a badge and a side branch. Steps with no record stay visible and read not run. The parallel reads and the join are labelled. Two runs stack on the same columns. Replay lights the saved order on the diagram, including at 0.5x. The open page is a short strip, that diagram, and four cards. Tables, logs, and identifiers stay under Technical details, closed until opened. The script is in the page, including the exported file. It does not contact a service and it does not write. Colours are variables. Light is the default. Dark follows the system setting, and a header control can pin either. No font file is requested. Text from logs is escaped.
Status: implemented. Code: `observability/path.py`, `observability/board.py`.

# ADR-40. A model class must agree with the labeled authorization

Context: approaches 2 and 3 can assign a class the rules did not choose. The labeled authorization is a field on the intake, separate from the complaint wording. A denied label is already ineligible for a model.
Decision: after a model proposes a final class, `accept_proposed_class` checks that label before the class is kept. Approach 2 and approach 3 use that one function.

Unauthorized from a model is kept only when the label is not supplied or denied. Not supplied is the case where the rules would also infer unauthorized from wording. A confirmed label contradicts that proposal: the class does not stand, the final class is ambiguous, and routing is manual review (`authorization_conflict`). Duplicate and service from a model are kept only when the label is confirmed or not supplied, the same limit the rules use. A denied label never reaches a model. If a class other than unauthorized is screened with a denied label, the check raises AssertionError.

Missing fields for the final class always come from `missing_for`. That covers the rules, a class kept after the ranker agrees, and a class a model assigns. Unauthorized with authorization not supplied lists authorization confirmation. A labeled denial does not add that field. A service class with authorization not supplied lists authorization confirmation first, then the service fields.
Status: implemented. Code: `classifiers/eligibility.py`, `classifiers/engine.py`, `rules/classify.py`.

# ADR-41. Five comparison metrics

Context: the comparison page listed model share, latency p50, Haiku cost averaged over priced calls, and a wrong-label rate. Those are not the rates used to choose an approach. A priced-call average hides cases that never called Haiku. A wrong class that was sent on as ready for review is a different failure from a class held for manual review.
Decision: each approach is judged on five figures. Wrong-label rate is saved classes that differ from the reviewer category, over cases that have a category. Auto-routed-but-wrong rate uses only the subset whose route is `ready_for_review`. A request for information and a manual review are not auto-routed. Manual-review rate is that route over cases run. Cost per case is the sum of list-price USD for the configured model and for Jev, divided by cases run. A case with no priced call adds zero dollars and still counts. Jev is priced from `config/rules/cost.yaml` at 0.042 USD per million input tokens and 0.00 USD per million output tokens when usage tokens are present. Latency is the p95 of classification audit `latency_ms`. A feedback sample under `comparison.min_sample` (30) is too small to conclude, for the wrong-label rate and again for the auto-routed sample. Agreement and disagreeing case ids stay on the page. They are not a sixth rate. p50 and the share that called a model are not headline figures. These rates do not approve a case.
Status: implemented. Code: `observability/compare_page.py`.

# Accepted, not built in this checkpoint

ADR-8, the ordered decision table, is implemented in `rules/classify.py`. The first match wins. These remain for a later checkpoint:

- ADR-9 through ADR-15 in the plan: claim-line search beyond labeled lines, ask-once, second approval, fixed-clock display, and the change-control freeze after the 12/12 line. The stdio wrapper is ADR-31. The running Azure layout is ADR-33. The status page is ADR-34. The local check workflow is ADR-35. The scorecard is ADR-36. Guardrail events are ADR-37. The live exec check and the manual image update are ADR-38. The request path and replay are ADR-39. A model class is checked against the labeled authorization in ADR-40. The five comparison metrics are ADR-41. An image build was not run from this workspace. The live container was not contacted from this workspace.
- Open assumptions, asked in `docs/FACILITATOR_QUESTIONS.md`: Q3 pass bar, Q4 assessor state path, Q6 withdrawn statement, Q7 out-of-range date, Q8 authorization edges, Q9 create after a non-ready routing.

# Process note

Branch protection on `initial-build` and `main` (required CI, one other reviewer) is a GitHub setting. It was not changed from this workspace. Turning it on is a team decision because it changes who can push.
