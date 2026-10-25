# Commands

Run these from the repository root. Python is 3.12 (`requires-python` is `>=3.12,<3.13`). Install [uv](https://docs.astral.sh/uv/) if it is not on `PATH`.

Hard rules, visible on every run:

- The app never approves. Approval is a separate human command, `tools/runtime.py approve`, against the same state file the mock was started with. There is no HTTP approve route. Source under `src/` does not call that operator command.
- The mock is loopback-only. It binds `127.0.0.1` and has no host flag. Do not publish the port.
- Code under `src/` never reads `dataset.json`. The corpus loads `policies/*.md` and `documents/*.md` only. The mock process is what opens the fixture. `tools/lint_gates.py` fails if any file under `src/` mentions that fixture name.

A passing checklist does not approve a case. The reviewer note says so.

## Fresh setup

```sh
uv sync --locked
uv run --locked python tools/validate_package.py
```

`make sync` is the same install. A healthy fixture print is:

```text
Fixture integrity passed: 12 cases; 52 sources.
```

The mock itself is standard library. This also works with no virtualenv:

```sh
python3.12 tools/runtime.py serve --help
```

`dispute-copilot` is installed by the sync above. `uv run --locked python -m dispute_copilot` is the same entry point.

Optional extra declared in `pyproject.toml` (the draft path does not import it):

```sh
uv sync --locked --extra openai
```

Leave that extra out for the walkthrough below.

A second optional extra, `mcp`, installs the Model Context Protocol library used by the stdio server below. `uv sync --locked` without `--extra mcp` still installs the copilot. The walkthrough does not need it.

### Environment

The process reads `.env` from the working directory and from the repository root. A variable already set in the process wins. Blank lines and `#` comments are skipped.

For this walkthrough, do not copy `.env.example` as-is. That example sets `FEATURE_LLM_REVIEW=block`. With no key, the checklist then blocks and `submit-review` refuses the case. Use:

```sh
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
```

| Variable | Used by | Effect |
|---|---|---|
| `MOCK_BASE_URL` | `dispute-copilot` | Default `http://127.0.0.1:8765`. Same as `--mock-url`. |
| `DATA_DIR` | `dispute-copilot` | Default `data/dev`. Same as `--data-dir`. |
| `SUBMISSIONS_DIR` | `dispute-copilot` | Default `submissions`. Same as `--submissions`. |
| `REPORTS_DIR` | `dispute-copilot` | Default `reports`. Traces, logs, and `metrics.json`. |
| `LOG_STDERR` | `dispute-copilot` | `1` also prints JSON log lines on stderr. |
| `RUN_ID` | `dispute-copilot` | Trace run id. Default `run-` plus 10 hex characters. |
| `LLM_PROVIDER` | drafter and checklist | `null` (also `template`, `templates`, `off`) keeps template text. |
| `FEATURE_LLM_REVIEW` | checklist | `off` (default) skips the critic. `block` may turn a pass into a fail. It cannot turn a fail into a pass. |
| `AWS_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `LLM_API_KEY` | model client | First non-empty value is the key. Empty means no model call. |
| `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_STYLE`, `LLM_TIMEOUT_S`, `AWS_REGION` | model client | See `.env.example`. Unused when the provider is `null`. |
| `HTTP_TIMEOUT_S`, `RETRY_BASE_DELAY_S`, `RETRY_MAX_DELAY_S` | read tools | Override `config/rules/retry.yaml`. |
| `BREAKER_ENABLED`, `BREAKER_FAILURE_THRESHOLD`, `BREAKER_COOLDOWN_S` | read tools | Override `config/rules/breaker.yaml`. `0`, `false`, `off`, or `no` disables the breaker. |
| `MOCK_PORT`, `MOCK_STATE` | week-10 scripts and `scripts/idempotency_demo.py` | Default port `8766`, default state `.state/week10.sqlite`. |

This checkout already tracks files under `submissions/` and some files under `reports/`. Point `SUBMISSIONS_DIR`, `REPORTS_DIR`, and `--report` at a directory you do not commit if the tree should stay clean. `.state/` is ignored.

## Container files

`Dockerfile`, `docker-compose.yml`, and `deploy/azure/containerapp.yaml` are on this branch. `docs/AZURE_DEPLOY.md` is the longer note. The mock command in those files has no host flag; `tools/runtime.py` listens on `127.0.0.1`.

A check of these files did not have Docker installed. `docker build`, `docker compose up`, and the exec flow (`run`, `agent-review`, `submit-review`, approve, `create-record`, `run-all`, evaluate) were not run in a container. The commands in the rest of this guide were run on the host against a loopback mock. Do not treat the compose file as proven until it has been started.

The compose file lists `env_file: .env`. That file is not in the repository.

## Stdio context server

`make mcp` starts `python -m dispute_copilot.mcp_server`. The process reads stdin and writes protocol messages on stdout. It does not take a port, and it does not bind one. Logs and traces go under `REPORTS_DIR` (`reports/logs/mcp.jsonl` and `reports/traces/`).

```sh
uv sync --locked --extra mcp
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
make mcp
```

Start the mock first if the call needs it (`make mock`, or `tools/runtime.py serve` on `127.0.0.1`). `MOCK_BASE_URL` defaults to `http://127.0.0.1:8765`.

A client that starts a stdio process can use:

```json
{
  "mcpServers": {
    "dispute-copilot": {
      "command": "uv",
      "args": ["run", "--locked", "--extra", "mcp", "python", "-m", "dispute_copilot.mcp_server"],
      "env": {
        "MOCK_BASE_URL": "http://127.0.0.1:8765",
        "LLM_PROVIDER": "null",
        "FEATURE_LLM_REVIEW": "off"
      }
    }
  }
}
```

Calls, each a wrapper around the existing command:

| Call | What it runs |
|---|---|
| `run_case` | `run` for one case |
| `run_all` | `run-all` |
| `get_draft` | the saved draft |
| `agent_review` | the checklist. A pass does not approve the case |
| `submit_review` | `submit-review` |
| `review_status` | the mock review status |
| `create_record` | `create-record`, still stopped by the existing gate until a person has approved |
| `explain_trace` | span names and `duration_ms` for one case |
| `metrics` | the metrics report |

Read-only resources: `guide://policy` and `contract://output`. The saved draft is returned by `get_draft`. Complaint text and evidence quotes in that result are marked `untrusted` data. They are not instructions. There is no approve, reject, or reset call. The server does not read the fixture file.

Without the extra, `python -m dispute_copilot.mcp_server` prints a one-line message on stderr and exits 1. An extra argument, including a port, exits 2.

## Mock

Start it in one terminal and leave it running. `make mock` uses port `8765` and state `.state/run.sqlite`:

```sh
make mock
```

That is:

```sh
uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite
```

A bare `serve` is different. With no flags the state file is `.state/mock.sqlite`, the data directory is `data/dev`, and the port is `8765`:

```sh
uv run --locked python tools/runtime.py serve --data data/dev --state .state/run.sqlite --port 8765
```

Startup line:

```text
Fictional training mock at http://127.0.0.1:8765; Ctrl-C stops it.
```

The listen address is `127.0.0.1` only. Stop it with Ctrl-C.

From a second terminal:

```sh
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/v1/cases
```

Health body:

```json
{"status": "ok", "dataset": "card-disputes-dev", "fictional_training_only": true}
```

`GET /v1/cases` lists `CARD-D01` through `CARD-D12` with document ids. It does not include expected answers.

Pass the same `--state` to every operator command. `approve` against a different file does not fail; `pending` on an empty file prints `[]` and the review you can see in the mock stays pending.

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
mkdir -p reports
uv run --locked python tools/runtime.py audit --state .state/run.sqlite > reports/audit.json
uv run --locked python tools/runtime.py reset --confirm --state .state/run.sqlite
```

`approve` prints `approved` and exits 0. `reject` prints `rejected` and exits 0. A second decision on the same id prints `Review must exist and be pending.` and exits 1. Omitting `review_id` or `--reviewer` is an argparse error, exit 2.

`reset` without `--confirm` exits 2. With `--confirm` it prints:

```text
Reset local mock state. Stop all clients before resetting.
```

That deletes reviews, records, idempotency keys, per-tool attempt counters, and the audit log. Stop writers first. A reset issued while the server was still up did clear pending reviews and the created record (`GET /v1/records/CARD-D01` then returned 404). Attempt counts live in the state file, so they survive a restart until reset.

`serve` flags: `--data`, `--state`, `--port`. `pending`, `approve`, `reject`, `audit`, and `reset` take `--state`. `approve` and `reject` take positional `review_id` and required `--reviewer`. `reset` requires `--confirm`.

## Workflow, in order

Use one mock, one state file, and one submissions directory. Defaults below match `make mock`.

```sh
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
export MOCK_BASE_URL=http://127.0.0.1:8765
STATE=.state/run.sqlite
```

1. Start the mock (terminal 1) with that `STATE` and port `8765`.
2. Draft. `run` writes one case. `run-all` writes every case the mock lists.
3. Checklist: `dispute-copilot agent-review CASE`. This step is required. It does not post.
4. `submit-review CASE`. This posts `POST /v1/reviews` only after a passing checklist for the same draft hash.
5. Human approval: `tools/runtime.py approve REVIEW_ID --reviewer NAME --state "$STATE"`. The app does not run this.
6. `create-record CASE`.

`submit-review` immediately after `run`, with no checklist, fails. Nothing is posted. Stderr is `record was not created (status 400)` and the exit code is 2. That refusal is `agent_approval_required`. The name is not printed. The audit has no `review_requested` row for that attempt.

### Draft one case

```sh
uv run --locked dispute-copilot run CARD-D01
```

Stdout is one JSON object. Exit 0. On the development fixture, `CARD-D01` looks like this (`draft` path follows `--submissions`):

```json
{
  "draft": "submissions/drafts/CARD-D01/v1.json",
  "case_id": "CARD-D01",
  "classification": "unauthorized",
  "routing": "ready_for_review",
  "policy_ids": ["CARD-POLICY-V2", "CARD-PROCEDURE-01"],
  "deadline": "2026-10-31",
  "facts": {
    "transaction_id": "TX-D01",
    "amount": 117,
    "currency": "XCR",
    "reported_date": "2026-10-01"
  }
}
```

Files written:

- `submissions/drafts/CARD-D01/vN.json` (the next version; a rerun does not overwrite `v1`)
- `submissions/results.json` (one object per case; a rerun replaces that case)
- `reports/traces/CARD-D01.jsonl` (replaced on the next `run` of the same case)
- `reports/logs/run.jsonl` (appended)

`run` does not submit a review and does not create a record.

### Checklist

```sh
uv run --locked dispute-copilot agent-review CARD-D01
```

Stderr is a reviewer note. Stdout is the checklist object. Exit 0 when `passed` is true, and 2 when it is not. A pass still does not approve the case.

```text
Reviewer note for CARD-D01
No model reason. The checklist result still stands.
Type: unauthorized
Purchase: TX-D01
Amount: 117 XCR
Route: ready_for_review
Deadline: 2026-10-31
A pass does not approve the case.
```

```json
{
  "case_id": "CARD-D01",
  "draft_version": 1,
  "draft_hash": "<sha256 of the draft>",
  "passed": true,
  "checks": {
    "contract_valid": true,
    "redaction_clean": true,
    "commitment_guard": true,
    "citations_present": true,
    "narrative_marked_draft": true
  },
  "run_id": "<uuid hex>",
  "llm_review": null,
  "contract_version": "1.0"
}
```

This writes `submissions/reviews/CARD-D01.json` with an `agent_approval` object and does not call the mock. With `FEATURE_LLM_REVIEW=block` and no usable model reply, `passed` is false, `checks.llm_critic` is false, the note says `Verdict: block` and `Reason: The model reply was not usable.`, and the exit code is 2. `submit-review` then returns status 400.

### Submit the review

```sh
uv run --locked dispute-copilot submit-review CARD-D01
```

Exit 0. Stderr repeats the reviewer note. Stdout is the handle: `agent_approval` plus `review_id`, `draft_hash`, and `draft_version`. The same JSON is stored in `submissions/reviews/CARD-D01.json`. The mock audit gains `review_requested`. `review_id` looks like `rev_` plus 12 hex characters and changes every post.

Running `submit-review` again posts a new review and replaces `review_id`. Approve the latest id. The previous id stays pending in the mock until you reject it or reset.

### Create is refused until a person approves

```sh
uv run --locked dispute-copilot create-record CARD-D01
```

While the review is pending, stderr is `record was not created (status 403)` and the exit code is 2. The gate stops the post inside the program. The audit does not gain `record_created` or `unapproved_write_attempt`. `pending` still shows `"status": "pending"` and `"reviewer": null`.

A raw `POST /v1/records` with no approved review is different: the mock answers HTTP 403 `{"error": "approval_missing_or_draft_changed"}` and audits `unapproved_write_attempt`. A post with no `Idempotency-Key` is HTTP 400 `{"error": "draft_review_and_idempotency_key_required"}`.

Reject instead of approve:

```sh
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
```

`create-record` then prints the same status 403 line and exits 2. The audit event is `human_rejected`.

### Human approval, then create

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
uv run --locked dispute-copilot create-record CARD-D01
```

`approve` prints `approved`. `create-record` exits 0 and prints:

```json
{
  "http_status": 201,
  "record_id": "dispute-d01",
  "existing_record_id": null,
  "posted": true
}
```

`GET /v1/records/CARD-D01` returns 200 with `"id": "dispute-d01"`. The audit event is `record_created`, including the reviewer name. `CARD-D01` and `CARD-D03` record ids on this fixture are `dispute-d01` and `dispute-d03`. `CARD-D02` and `CARD-D09` share `dispute-double-02`.

### Same create again

```sh
uv run --locked dispute-copilot create-record CARD-D01
```

Exit 0. Nothing is posted. The pre-check finds the row:

```json
{
  "http_status": 200,
  "record_id": "dispute-d01",
  "existing_record_id": "dispute-d01",
  "posted": false
}
```

The audit still has one `record_created` for that case. A later `run` writes `v2` and refreshes `results.json`, but `create-record` still returns the existing row and does not post the new draft. A new draft that should be submitted needs a new checklist and a new `submit-review` before any later create, and only if no record exists yet.

### Draft every development case

```sh
uv run --locked dispute-copilot run-all
```

Exit 0 when every listed case is written. Exit 2 when any case is skipped (`not_written` holds the reason). On `data/dev` every case is written, `not_written` is `[]`, and `alerts_fired` on that run is `[]`. The command also writes `reports/metrics.json`.

| Case | Classification | Routing | Amount | Deadline | Policy |
|---|---|---|---|---|---|
| CARD-D01 | unauthorized | ready_for_review | 117 | 2026-10-31 | CARD-POLICY-V2 |
| CARD-D02 | duplicate_charge | ready_for_review | 134 | 2026-11-16 | CARD-POLICY-V2 |
| CARD-D03 | service_not_received | ready_for_review | 151 | 2026-12-02 | CARD-POLICY-V2 |
| CARD-D04 | unauthorized | request_information | 168 | 2026-10-31 | CARD-POLICY-V2 |
| CARD-D05 | ambiguous | manual_review | 185 | null | CARD-POLICY-V2 |
| CARD-D06 | unsupported | manual_review | 202 | null | CARD-POLICY-V2 |
| CARD-D07 | unauthorized | manual_review | null | 2026-10-31 | CARD-POLICY-V2 |
| CARD-D08 | duplicate_charge | ready_for_review | 236 | 2026-11-16 | CARD-POLICY-V2 |
| CARD-D09 | duplicate_charge | ready_for_review | 134 | 2026-11-16 | CARD-POLICY-V2 |
| CARD-D10 | service_not_received | ready_for_review | 270 | 2026-12-02 | CARD-POLICY-V2 |
| CARD-D11 | unauthorized | ready_for_review | 287 | 2026-09-20 | CARD-POLICY-V1 |
| CARD-D12 | service_not_received | request_information | 304 | 2026-12-03 | CARD-POLICY-V2 |

Every row also cites `CARD-PROCEDURE-01`. Currency on these rows is `XCR`. `CARD-D04` is missing `authorization_confirmation`. `CARD-D12` is missing `promised_service_date` and `purchase_receipt`. `CARD-D07` stays a written draft: transactions returned 503 on all 3 attempts, so amount and currency are null and routing is `manual_review`. `CARD-D10` fails the first transactions read and succeeds on the second, so the amount is kept.

Flags may be placed before or after the subcommand. Explicit form:

```sh
uv run --locked dispute-copilot \
  --mock-url http://127.0.0.1:8765 \
  --data-dir data/dev \
  --submissions submissions \
  run CARD-D01
```

## `dispute-copilot` reference

```text
usage: dispute-copilot [-h] [--mock-url MOCK_URL] [--data-dir DATA_DIR]
                       [--submissions SUBMISSIONS]
                       {run,run-all,agent-review,submit-review,create-record,metrics,dashboard}
                       ...
```

Global flags (also accepted on each subcommand): `--mock-url`, `--data-dir`, `--submissions`, `--label`, `-h`.

| Command | Arguments | Mock required | Exit 0 | Other exits |
|---|---|---|---|---|
| `run CASE` | one case id | yes | draft written | 1 health or bad id; 2 case not written |
| `run-all` | none | yes | every case written | 1 health; 2 any case skipped |
| `agent-review CASE` | one case id | yes (health is checked) | `passed` true | 2 checklist failed, or no draft |
| `submit-review CASE` | one case id | yes | review posted | 2 refused or no draft |
| `create-record CASE` | one case id | yes | created, or existing row reported | 2 gate or missing handle |
| `metrics` | none | no | report printed | |
| `dashboard` | `--export PATH`, `--port` (default 8754) | no | file written, or page served until interrupted | 1 if the port will not bind |

Missing command or missing `CASE` is argparse exit 2. An unknown command (`bakeoff`, for example) is exit 2: `invalid choice`.

Case ids must be a single path segment. Empty, `.`, `..`, or an id containing `/` or `\` prints `case id must be a single path segment` and exits 1, before the health check.

Health failure prints `mock health check failed` and exits 1. No submission file is written.

Other stderr lines seen from this tree:

| Situation | Stderr | Exit |
|---|---|---|
| `submit-review` with no draft file | `no draft has been written for CASE` | 2 |
| `submit-review` without a passing checklist for this hash | `record was not created (status 400)` | 2 |
| `create-record` with no review file | `record was not created (status 400)` | 2 |
| `create-record` while pending or rejected | `record was not created (status 403)` | 2 |
| Case id not listed by the mock | `CASE is not in the mock case list` | 2 |
| Data dir has no matching markdown | `CASE document SOURCE is not in the corpus` | 2 |

The status 400 and 403 lines do not include the inner reason. For `submit-review` before a passing checklist, that reason is `agent_approval_required`.

`metrics` reads `reports/traces/*.jsonl` (or `$REPORTS_DIR/traces`), rewrites `metrics.json`, and prints `metrics` plus `alerts_fired`. It does not contact the mock. Every trace file in that directory is included, so a directory left over from a partial run can fire alerts even when the last full `run-all` was clean. `run` replaces `CASE.jsonl`. Checklist, submit, and create traces are separate files: `CASE.agent-review.jsonl`, `CASE.submit-review.jsonl`, `CASE.create-record.jsonl`.

## Evaluate

Score `submissions/results.json` against development gold. Full gold has 12 cases. A missing case fails that case. The report does not by itself prove approval or tool use.

```sh
mkdir -p reports
uv run --locked python tools/evaluate.py \
  --data data/dev \
  --gold feedback/dev_gold.json \
  --submission submissions/results.json \
  --report reports/dev.json
```

Flags: `--data`, `--gold`, and `--submission` are required. `--report` is optional; the parent directory is created. The JSON is always printed. Exit 0 when `passed` is true. Exit 1 when any case fails, the file is not a JSON array, or the path cannot be read (`Invalid evaluation input: ...`).

A `run-all` against the development mock scores `passed_cases` 12, `total_cases` 12, `passed` true, `format_errors` []. One submitted case scores `passed_cases` 1 and exits 1. The report ends with:

```text
Automated checks do not establish semantic grounding, safe prose, human approval, tool correctness or readiness. Review narrative and server audit separately.
```

Per-case checks: `classification`, `routing`, `policy_selection`, `facts`, `missing_information`, `deadline`, `citations_valid`, `required_evidence`, `narrative_present`, `no_restricted_values`, `targeted_questions`, plus `submitted` when the case is absent.

## Observability

Defaults: traces in `reports/traces/`, logs in `reports/logs/run.jsonl`, metrics in `reports/metrics.json`. `REPORTS_DIR` moves all three. `LOG_STDERR=1` copies log lines to stderr. `LOG_STDOUT=1` copies the same JSON lines to stdout, for a platform log collector. Times in traces are UTC. `LOG_STDOUT` is not applied to the stdio context server, because that process already uses stdout for its protocol.

`--label NAME` on a command (or `RUN_LABEL`) stores `label` on each trace record. The status page compares `local`, `azure`, and `aws`. A label with no traces is unmeasured.

```sh
uv run --locked dispute-copilot run-all --label local
LOG_STDOUT=1 uv run --locked dispute-copilot run-all --label azure
```

### Status page

`dispute-copilot dashboard` reads traces, `logs/run.jsonl`, `logs/mcp.jsonl`, and `audit.json` when that file is present. It does not contact the mock. With `--export out.html` it writes one HTML file and exits. Without `--export` it serves that page on `127.0.0.1` only. The default port is `8754`. There is no host flag. GET `/` and GET `/health` are the only successful routes. POST, PUT, PATCH, and DELETE return 405. The page does not approve, reject, reset, or post.

```sh
uv run --locked dispute-copilot dashboard --export reports/status.html
uv run --locked dispute-copilot dashboard
# http://127.0.0.1:8754
```

`make dashboard` is the serve form. Open the HTML file in a browser when you used `--export`.

The page has eight sections: run overview (pass or fail per case against the latency target, counts, SLO, alerts), a per-case span timeline, latency (p50, p95, max per step and per case), cost (per model call, per case, per run), a log table with filters, security (injections, masked values, gate refusals), approval state, and a deployment comparison. A pass on the overview means the case was written inside the latency target. It is not a gold score and it is not an approval.

Token prices live in `config/rules/cost.yaml` as per-1,000-token estimates. The shipped file has no model prices. A call whose model is missing from that file is shown as `not configured`, not as a number. Compute cost is `estimate, unmeasured` unless `compute.usd` is filled with a measured figure. The page runs the same redactor as the logs before it is written.

Human review state stays `unmeasured` unless `REPORTS_DIR/audit.json` is a JSON list of audit rows (`event`, `case_id`). Copy that list from `tools/runtime.py audit` if you want the column filled. The page only reads it.

Pulling the same files from the Azure app, without opening ingress, is in `docs/AZURE_DEPLOY.md` and `docs/RUNBOOK.md`.

```sh
uv run --locked dispute-copilot metrics
uv run --locked python scripts/trace_view.py reports/traces/CARD-D01.jsonl
```

`scripts/trace_view.py` takes trace paths and has no flags. A missing path exits 1 with `FileNotFoundError`. `--help` is treated as a filename.

A healthy `CARD-D01` trace is one line per span: `intake.parse`, `documents.followups`, `rules.classify`, `tool.attempt`, `tool.call`, `policy.select`, `rules.routing`, `citations.build`, `ledger.lookup`, `contract.validate`, `redaction.check`, `draft.write`, and `case`. `CARD-D07` adds `retry.backoff` and `tool.exhausted`, then `degradation.applied`. `CARD-D08` adds `security.untrusted_instruction` with `action` `treated_as_data`. The trace and the draft do not contain the merchant note. `CARD-D10` shows attempt 1 at HTTP 503 and attempt 2 at HTTP 200.

On a clean development `run-all`, `metrics` reported `cases_total` 12, `cases_drafted` 12, `valid_contract_rate` 1.0, `good_case_rate` 1.0, `tool_exhausted` 1, `tool_exhausted_rate` 0.0417, `tool_retries` 3, `breaker_opens` 0, `breaker_skips` 0, `untrusted_instruction_cases` 1, `manual_review_rate` 0.25, routes `ready_for_review` 7, `request_information` 2, `manual_review` 3, and `alerts_fired` `[]`. Durations move between machines. The latency target in `config/rules/slo.yaml` is 2000 ms. A case counts toward `good_case_rate` when the trace shows a drafted case inside that target.

Alert names in `config/rules/slo.yaml`: `tool_exhausted_rate_high` (page, `tool_exhausted_rate` > 0.25), `good_case_rate_below_slo` (page, `good_case_rate` < 0.95), `breaker_opened` (ticket, `breaker_opens` > 0), `untrusted_instruction_spike` (ticket, `untrusted_instruction_cases` > 2), `unapproved_create_attempt` (page, `gate_blocked` > 0). A create that the gate refuses records `review.gate` with `passed` false and can fire `unapproved_create_attempt` the next time `metrics` reads that trace.

`scripts/week10_summary.py` reads a command name. `run` summarizes `run-all` JSON on stdin. `eval PATH` prints per-case pass or fail. Any other word treats stdin as `audit` JSON and prints tool reads that were retried or returned 503. No argument exits 1 with `IndexError`.

SLO, alerts, and operator response: `docs/SLO.md`, `config/rules/slo.yaml`, `docs/RUNBOOK.md`. Failure-case map: `docs/HARDENING_CHECKLIST.md`. Audit notes: `docs/SECURITY_AUDIT.md`. HTTP shapes: `docs/API.md`.

## Reliability and duplicate creates

Read tools retry HTTP 503 or a timeout, up to 3 attempts, with exponential backoff and capped jitter (`config/rules/retry.yaml`: base 0.05 s, cap 0.2 s, HTTP timeout 10 s). The breaker (`config/rules/breaker.yaml`, enabled, threshold 3, cooldown 30 s) opens only after that many consecutive cases exhaust every attempt on the same dependency. One outage case (`CARD-D07`) does not open it. Later cases still call transactions.

The supplied mock returns 503 at once. It does not hold a socket open. `scripts/fault_demo.py` starts its own double on `127.0.0.1` and an ephemeral port:

```sh
uv run --locked python scripts/fault_demo.py timeout
uv run --locked python scripts/fault_demo.py breaker
```

`timeout` prints recovery on attempt 3 (hang, then 503, then 200) and then a case that exhausts 3 timeouts: `tool failed with status 0 after 3 attempt(s); no amount was invented`. `breaker` uses threshold 2 and cooldown 0.5 s: two exhausted cases open it, the next case skips with 0 requests, a failed probe opens it again, a healed probe closes it. Exit 0. Any other word is argparse exit 2.

To open the breaker on the supplied mock, lower the threshold and keep the cooldown long enough that later cases still see it open. This changes drafts (null amounts, `manual_review`) and will not match gold:

```sh
export BREAKER_FAILURE_THRESHOLD=1
export BREAKER_COOLDOWN_S=60
uv run --locked dispute-copilot run-all
```

On that setting, `CARD-D07` exhausts 3 attempts and opens the transactions breaker. `CARD-D08` through `CARD-D12` skip the call (`breaker_skips` 5, `breaker_opens` 1). `alerts_fired` contains `breaker_opened`. `good_case_rate` stayed 1.0 because those cases were still drafted inside the latency target. Unset the two variables afterwards. Default files are `config/rules/breaker.yaml`.

Duplicate-request demo. It never approves. It expects a mock already listening. Defaults are port `8766`, state `.state/week10.sqlite`, submissions `.state/week10/submissions`. Override with `MOCK_PORT`, `MOCK_STATE`, `SUBMISSIONS_DIR`, and `REPORTS_DIR`.

```sh
uv run --locked python tools/runtime.py serve \
  --data data/dev --state .state/week10.sqlite --port 8766
```

Second terminal:

```sh
export MOCK_PORT=8766
export MOCK_STATE=.state/week10.sqlite
export SUBMISSIONS_DIR=.state/week10/submissions
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
uv run --locked python scripts/idempotency_demo.py prepare
uv run --locked python tools/runtime.py approve REVIEW_ID \
  --reviewer reviewer-name --state .state/week10.sqlite
uv run --locked python scripts/idempotency_demo.py duplicate
```

`prepare` runs `run`, `agent-review`, and `submit-review` for `--case` (default `CARD-D02`) and `--twin` (default `CARD-D09`), then `create-record` on the case. That create exits 2 with status 403, and the script prints the approve command. `duplicate` before approval exits 3 and prints the same command.

After approval, `duplicate` exits 0 and prints `records created: 1 (expected 1). PASS`:

1. First `create-record` is HTTP 201, `record_id` `dispute-double-02`, `posted` true.
2. The same command again is HTTP 200, `posted` false.
3. The same idempotency key replayed is HTTP 200, audit `write_replayed`.
4. The same key with a changed draft is HTTP 409 `{"error": "idempotency_conflict"}`.
5. A new key for the same case is HTTP 409 `{"error": "duplicate_case", "existing_record_id": "dispute-double-02"}`, audit `duplicate_prevented`.
6. `create-record CARD-D09` finds that same record (`posted` false). `GET /v1/records/CARD-D09` is 200 with the same id.

The twin still needs its own review file. `create-record` on a case with no `submissions/reviews/CASE.json` exits 2 with status 400 even when the shared record already exists.

## Security checks

```sh
uv run --locked pytest -q tests/unit/test_security.py
```

That file checks:

- `CARD-D08` merchant text is treated as data. The draft does not contain the injected instruction or the private marker, and nothing is posted.
- Ordinary merchant text is not flagged.
- The only mock writes in the adapters are `POST` to the review and record paths.
- `src/` does not mention `runtime.py` and does not call `approve` or `reject`.
- Tracked files do not contain a credential-shaped value, and `.env` is not tracked.

Related files in the same suite: `tests/unit/test_gates.py`, `tests/unit/test_approval.py`. The full suite is `make test` (98 passed on this tree).

`CARD-D08` on a live `run-all` writes a draft, flags `security.untrusted_instruction`, and does not create a record. Do not paste the merchant payload. `docs/RUNBOOK.md` is the response for that case.

## Lint, make, tests

```sh
make lint
make test
make check
make daily
```

`make lint` runs `uv run --locked python tools/lint_gates.py` and prints `lint gates passed (46 source files)`. Exit 1 lists problems on stderr. The gate scans `src/**/*.py` for a mention of the fixture file, a restricted marker, an `approve` definition or call, and a contract name spelled outside `fields.py`.

`tools/lint_gates.py` has no help parser. The only recognized argument is `--staged`, which also rejects an index path under `.state/`. Any other argument, including `--help`, is ignored and the gates still run.

```sh
uv run --locked python tools/lint_gates.py --staged
git config core.hooksPath .githooks
```

`.githooks/pre-commit` runs the staged gate. `git config` writes this clone only.

`make test` is `uv run --locked pytest` (`testpaths` is `tests`, `pythonpath` is `src`). `make check` is lint, then pytest. `make daily` is `make check`, then this line:

```text
The rules engine drafts every development case. Score the written results with tools/evaluate.py.
```

Direct pytest:

```sh
uv run --locked pytest
uv run --locked pytest tests/unit
uv run --locked pytest tests/integration
```

`make regen-gold` prints a refusal and exits 2. It does not rewrite `feedback/dev_gold.json`.

```text
refusing: hidden-style gold is not in this checkpoint, and feedback/dev_gold.json is never rewritten
```

Make targets that exist: `sync`, `lint`, `test`, `check`, `daily`, `mock`, `regen-gold`, `metrics`, `week10`, `week10-duplicate`.

`make metrics` is `uv run --locked dispute-copilot metrics`.

`make week10` runs `scripts/week10_evidence.sh`. It starts its own mock on `127.0.0.1:${MOCK_PORT:-8766}` with state `${MOCK_STATE:-.state/week10.sqlite}`, resets that state between sections, and writes `evidence/week10/`. It does not approve. It stops after `scripts/idempotency_demo.py prepare` and tells you to approve, then run `make week10-duplicate` (`scripts/week10_duplicate.sh`), which restarts the mock on the same state and runs `duplicate`. Those scripts export `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off`.

These are not targets and not subcommands: `make bakeoff`, `make shadow-eval`, `make evaluate`, `make judge`. `make bakeoff` exits 2 with `No rule to make target 'bakeoff'`. `dispute-copilot` has no `bakeoff` command.

## One-file CARD-D01 script

`scripts/normal_case.py` is a separate path for `CARD-D01` only. It talks to whatever mock is on `--base-url` (default `http://127.0.0.1:8765`). It does not take a state file. It does not run `agent-review`. It writes fixed paths and will replace tracked files:

- `submissions/drafts/CARD-D01/v1.json` (always `v1`, not the next version)
- `submissions/results.json` (replaced with a one-element array)
- `submissions/reviews/CARD-D01.json`

```sh
uv run --locked python scripts/normal_case.py --help
uv run --locked python scripts/normal_case.py run
uv run --locked python scripts/normal_case.py submit-review
uv run --locked python scripts/normal_case.py create
```

Flags: `--base-url`, `--data-dir` (default `data/dev`). Subcommands: `run`, `submit-review`, `create`.

`run` prints the draft path and `next`. `submit-review` posts immediately and prints `review_id`, `draft_hash`, `status` `pending`. `create` before approval exits 1:

```text
Review REVIEW_ID is pending. Nothing was written. Ask a person to approve that exact draft.
```

After `tools/runtime.py approve` on that id, `create` prints HTTP 201 and `record_id` `dispute-d01`. A second `create` prints HTTP 200 and `existing_record_id` `dispute-d01` (this script's JSON shape, not the `posted` field from `dispute-copilot`). The script never approves.

Do not mix the two writers in one submissions directory. The installed command's `create-record` still requires `agent_approval` for the server hash. The one-file script does not write that object.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `record was not created (status 400)` on `submit-review` | Checklist missing, failed, or for a different draft hash (`agent_approval_required`). No review was posted. | Run `agent-review` on the latest draft. If you set `FEATURE_LLM_REVIEW=block`, set it to `off` or provide a key that returns a usable pass. |
| `no draft has been written for CASE` | `submit-review` or `agent-review` before `run`, or a different `--submissions`. | `run` that case into the same submissions directory. |
| `mock health check failed`, exit 1 | Nothing is listening on `--mock-url`, or it is not this mock. | Start `serve` on `127.0.0.1` and pass that port as `--mock-url`. Fix this before looking at draft errors: health is checked first. |
| `record was not created (status 403)` | Review pending or rejected, or the gate failed. The program did not post. | `pending` with the same `--state`. `approve` that `review_id`. Then `create-record`. |
| `pending` prints `[]` but you just submitted | Operator command is on a different state file. `make mock` uses `.state/run.sqlite`. Bare `serve` uses `.state/mock.sqlite`. | Pass the same `--state` you gave `serve`. |
| `Review must exist and be pending.` | Id is unknown on that state file, or it was already approved or rejected. | `pending` and `audit` on that file. A new `submit-review` needs a new approve. |
| Second `create-record` shows `posted: false` | The record already exists. Exit 0. This is the idempotent path. | Read `record_id`. Do not expect a second row. `CARD-D09` resolves to the `CARD-D02` record. |
| `run` after create still will not post a new body | Pre-check returns the existing row before the gate. | Expected. Reset the state file if you need a fresh ledger. Stop clients first. |
| `submit-review` twice | Two pending reviews. The file keeps only the latest `review_id`. | Approve the id now in `submissions/reviews/CASE.json`. |
| `run-all` exit 2 | One or more cases are in `not_written` with a reason. | Read that list. A date outside every policy, or a document missing from `--data-dir`, skips that case only. |
| `evaluate.py` exit 1, `passed_cases` under 12 | Submission is missing cases or fields differ from `feedback/dev_gold.json`. | Use the `results.json` from `run-all` on an unfaulted mock. A breaker demo will not score 12. |
| `good_case_rate_below_slo` or `unapproved_create_attempt` after a good run | `metrics` is reading old trace files, including a refused create. | Point `REPORTS_DIR` at an empty directory and rerun, or delete the stale `traces/` files. |
| `make bakeoff` or `dispute-copilot bakeoff` | Not implemented. | Use the commands in this file. |
| Port already in use | Another `serve` is still bound. | Ctrl-C that process, or pick `--port` and the matching `--mock-url`. |

Order that works on a fresh machine: sync, validate, export `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off`, start the mock on `127.0.0.1` with a state file you will reuse, `run` or `run-all`, `agent-review`, `submit-review`, human `approve`, `create-record`. The app never approves. The mock stays on loopback. Code under `src/` never reads `dataset.json`.
