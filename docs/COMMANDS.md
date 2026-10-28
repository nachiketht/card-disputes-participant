# Commands

Run these from the repository root. Python is 3.12 (`requires-python` is `>=3.12,<3.13`). Install [uv](https://docs.astral.sh/uv/) if it is not on `PATH`.

Hard rules, visible on every run:

- The app never approves. Approval is a separate human command, `tools/runtime.py approve`, against the same state file the mock was started with. There is no HTTP approve route. Source under `src/` does not call that operator command.
- The mock is loopback-only. It binds `127.0.0.1` and has no host flag. Do not publish the port.
- Code under `src/` never reads `dataset.json`. The corpus loads `policies/*.md` and `documents/*.md` only. The mock process is what opens the fixture. `tools/lint_gates.py` fails if any file under `src/` mentions that fixture name.

A passing checklist does not approve a case. The reviewer note says so.

## Run everything

Copy these from the repository root, in this order. Python is 3.12. A non-empty value already set in the process wins over `.env`. An empty process value is filled from the file. Use placeholders. Do not paste a real endpoint or a key into a file you commit.

### 1. Setup

```sh
uv sync --locked --extra mcp
uv run --locked python tools/validate_package.py
```

`uv sync --locked` without the extra still installs `dispute-copilot`. `--extra mcp` adds the stdio server library. `--extra openai` is declared and is not required for the commands below. A healthy fixture print is `Fixture integrity passed: 12 cases; 52 sources.`

`make sync` is `uv sync --locked` and does not pass `--extra mcp`. Use the `uv` line above when you want the stdio server.

### 2. Mock

Leave this running in its own terminal. It listens on `127.0.0.1` only.

```sh
make mock
```

That is `uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite`. The startup line is `Fictional training mock at http://127.0.0.1:8765; Ctrl-C stops it.` Health from a second terminal:

```sh
curl -s http://127.0.0.1:8765/health
```

A live mock answers `{"status": "ok", ...}`. Later `approve`, `reject`, `pending`, `audit`, and `reset` must use this same `--state`.

### 3. Environment

No model. The checklist does not call one. Put this in the shell, or in a `.env` you do not commit:

```sh
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
```

Checklist judge, Claude Haiku 4.5. The id is `claude-haiku-4-5`. Prices for that id are in `config/rules/cost.yaml`. The call can only fail a checklist that already passed. It does not approve. Leave `LLM_PROVIDER` unset so `LLM_API_KEY` selects that client. Put the real key and the host in `.env`. Do not write the host in a committed file.

```sh
export FEATURE_LLM_REVIEW=block
export LLM_MODEL=claude-haiku-4-5
export LLM_API_STYLE=anthropic
export LLM_API_KEY=<key>
unset LLM_PROVIDER
```

`AZURE_BASE_URL` belongs in `.env` with the key. It is not listed here. A model call fails when it is empty. Approach 1 does not need it.

Do not commit `LLM_API_KEY`.

### 4. One case, then every development case

```sh
uv run --locked dispute-copilot run CARD-D01 --label local
uv run --locked dispute-copilot run-all --label local
```

`run` prints JSON with `draft`, `case_id`, `classification`, `routing`, `policy_ids`, `deadline`, and `facts`. Exit 0 means that case was written. `run-all` exit 0 means every case was written. Exit 2 means `not_written` lists the cases that were skipped.

### 5. Draft view

Each write adds `submissions/drafts/CARD-D01/vN.json`. The highest `N` is the draft later commands read. `submissions/results.json` is the array `tools/evaluate.py` scores.

```sh
python3 -m json.tool submissions/drafts/CARD-D01/v1.json
```

### 6. Checklist, with and without the critic

Without a model (`FEATURE_LLM_REVIEW=off`). Stderr is a reviewer note. It includes `No model reason. The checklist result still stands.` and ends `A pass does not approve the case.` Stdout JSON has `"passed": true` and `"llm_review": null` on a clean draft. Exit 0 is a pass. Exit 2 is a failed checklist.

```sh
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
uv run --locked dispute-copilot agent-review CARD-D01
```

With the critic (`FEATURE_LLM_REVIEW=block`), and the Azure or Bedrock block from step 3. Stderr then has `Verdict: pass` or `Verdict: block`, then `Reason:`. A `block` verdict fails the checklist. A missing key, a refused call, or a reply that is not usable is `Verdict: block` and `Reason: The model reply was not usable.`

```sh
export FEATURE_LLM_REVIEW=block
uv run --locked dispute-copilot agent-review CARD-D01
```

### 7. Submit

Posts the saved draft only after that checklist passed for the same draft. Skipping the checklist exits 2 and posts nothing.

```sh
uv run --locked dispute-copilot submit-review CARD-D01
```

### 8. Human approval, then create

The next two commands are the person, not the app. `approve` prints `approved`.

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer NAME --state .state/run.sqlite
uv run --locked dispute-copilot create-record CARD-D01
```

Replace `REVIEW_ID` with the id from `pending` or from `submit-review`. Replace `NAME` with the reviewer string you want stored.

### 9. Reject and reset

`reject` prints `rejected`. `create-record` then exits 2 and does not post.

```sh
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer NAME --state .state/run.sqlite
```

`reset` deletes the mock ledger in that state file. Stop clients first.

```sh
uv run --locked python tools/runtime.py reset --confirm --state .state/run.sqlite
```

`audit` prints the ledger events. Redirect it only after `reports/` exists.

```sh
uv run --locked python tools/runtime.py audit --state .state/run.sqlite
```

### 10. Trace and metrics

`scripts/trace_view.py` prints one trace file. It takes paths, not flags.

```sh
uv run --locked python scripts/trace_view.py reports/traces/CARD-D01.jsonl
```

`explain_trace` is the stdio-server call for the same span list. It is not a `dispute-copilot` subcommand. See the server block below.

```sh
uv run --locked dispute-copilot metrics
```

Stdout is JSON with `metrics` and `alerts_fired`. The same report is written to `reports/metrics.json`.

### 11. SLO and budget

`metrics.good_case_rate` is the share of `case` spans that were drafted in 2000 ms or less. The target and the alert lines are `config/rules/slo.yaml`. `metrics.error_budget` has `allowed_bad_cases`, `bad_cases`, and `remaining_fraction`. `remaining_fraction` is the share of that budget still left (`1.0` means none of it was used). The share used is one minus `remaining_fraction`. `good_case_rate_below_slo` is present in `alerts_fired` when `good_case_rate` is under 0.95.

```sh
uv run --locked python -c 'import json; d=json.load(open("reports/metrics.json")); print(d["metrics"]["good_case_rate"]); print(d["metrics"]["error_budget"]); print(d["alerts_fired"])'
```

### 12. Evals and the scorecard

`make evals` starts a loopback mock, drafts the development cases, and prints the scorecard. The judge stays off.

```sh
make evals
```

To score a submission you already wrote:

```sh
uv run --locked dispute-copilot evals --submission submissions/results.json --report reports/evals.json
```

Exit 1 means a category missed the minimum in `config/rules/evals.yaml`. The case checker, separate from the scorecard:

```sh
uv run --locked python tools/evaluate.py --data data/dev --gold feedback/dev_gold.json --submission submissions/results.json --report reports/dev.json
```

### 13. Guardrail checks and adversarial cases

```sh
uv run --locked pytest tests/adversarial tests/unit/test_security.py tests/unit/test_leak_patterns.py tests/unit/test_approval.py
uv run --locked --extra mcp python tools/check_mcp_names.py
```

`check_mcp_names.py` fails if the stdio server registers an approve, reject, or reset call.

### 14. Status page

Export writes the status HTML and a sibling `compare.html`, then exits. The serve form binds `127.0.0.1:8754` and answers GET `/`, `/index.html`, `/compare`, `/compare.html`, and `/health`.

```sh
uv run --locked dispute-copilot dashboard --export reports/status.html
uv run --locked dispute-copilot dashboard
```

Tag a run before you compare places. A place appears only when a saved run has it.

```sh
uv run --locked dispute-copilot run-all --label local
uv run --locked dispute-copilot run-all --label azure
```

On the page, replay speeds are `0.5x`, `1x`, `2x`, and `5x`, with pause, restart, and a position control. Replay reads the saved trace. It does not call the mock or a model.

### 15. Stdio server

```sh
uv sync --locked --extra mcp
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
make mcp
```

`make mcp` is `uv run --locked --extra mcp python -m dispute_copilot.mcp_server`. An extra argument exits 2 and prints `this server speaks stdio only`. Without the extra, the same module exits 1 and prints `the mcp extra is not installed`.

A client config, with placeholders only:

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

Calls: `run_case`, `run_all`, `get_draft`, `agent_review`, `submit_review`, `review_status`, `create_record`, `explain_trace`, `metrics`. Resources: `guide://policy`, `contract://output`. There is no approve, reject, or reset call.

### 16. Make targets

| Target | Command |
|---|---|
| `sync` | `uv sync --locked` |
| `lint` | `uv run --locked python tools/lint_gates.py` |
| `test` | `uv run --locked pytest` |
| `check` | `lint` then `test` |
| `mock` | serve on port 8765, state `.state/run.sqlite` |
| `mcp` | stdio server, with `--extra mcp` |
| `metrics` | `dispute-copilot metrics` |
| `dashboard` | `dispute-copilot dashboard` |
| `secrets` | `python3 tools/scan_secrets.py` |
| `evals` | `uv run --locked python tools/run_evals.py` |
| `week10` | `scripts/week10_evidence.sh` |

Also in the Makefile: `daily` runs `check` and prints where to score results. `week10-duplicate` runs `scripts/week10_duplicate.sh`. `regen-gold` refuses and exits 2: `refusing: hidden-style gold is not in this checkpoint, and feedback/dev_gold.json is never rewritten`.

### 17. CI jobs, run locally

Workflow: `.github/workflows/ci.yml`. Jobs: `lint`, `test`, `evaluate`, `security`, `secrets`, `image`, then `azure` on push only. Local equivalents:

```sh
uv sync --locked --extra mcp
uv run --locked --extra mcp python tools/validate_package.py
uv run --locked --extra mcp python tools/lint_gates.py
uv run --locked --extra mcp pytest
uv run --locked --extra mcp python tools/check_mcp_names.py
uv run --locked --extra mcp pytest tests/adversarial tests/unit/test_security.py tests/unit/test_leak_patterns.py tests/unit/test_approval.py tests/integration/test_mcp_stdio.py
python3 tools/scan_secrets.py
```

`evaluate` is the mock, `run-all`, `tools/evaluate.py`, `tools/check_fresh_report.py`, a dashboard export, and `dispute-copilot evals`, as written in the workflow. `image` is `sh tools/image_smoke.sh card-disputes:ci` and needs Docker. `azure` is the live-container block below.

### 18. Azure deploy and the live-container check

`tools/azure_live.py` does not print secret values. `gate` with no sign-in exits 0 and prints `Azure sign-in is not configured. The live container was not contacted.` together with the names `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. `plan` prints the remote commands and does not contact the container.

```sh
python3 tools/azure_live.py gate
python3 tools/azure_live.py plan
python3 tools/azure_live.py run --artifact /tmp/azure-artifact
```

`run` is what `.github/workflows/ci.yml` job `azure` calls after `azure/login`. The sign-in names are `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. A repository variable of the same name is also read when the secret is empty.

`.github/workflows/azure-deploy.yml` is `workflow_dispatch` only. It needs `AZURE_DEPLOY_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. It builds and pushes an image, then updates container app `card-disputes` in resource group `card-disputes-rg`. The app secret name is `llm-api-key`. Do not put the value in git.

`tools/azure_inside.py` is what `azure_live.py` copies into the container. Subcommands: `precheck`, `start`, `run`, `evaluate`, `cleanup`. It refuses the demo ledger `/state/run.sqlite` and the demo drafts `/submissions`.

```sh
python3 tools/azure_inside.py --help
```

### 19. Docker

Docker was not on this machine, so these lines are the files, not a run that was watched here.

```sh
docker build -t card-disputes:local .
docker compose up --build
sh tools/image_smoke.sh card-disputes:ci
```

The image default process is `python tools/runtime.py serve --data /data --state /state/run.sqlite --port 8765`. Compose starts `mock` with that serve command and `app` with `sleep infinity` on the mock network. `docs/AZURE_DEPLOY.md` is the longer container note.

### When something fails

- Judge unreachable: `agent-review` with `FEATURE_LLM_REVIEW=block` prints `Verdict: block` and `Reason: The model reply was not usable.` A connection failure and a timeout take that same path.
- Bad or missing key: the same `Verdict: block` line. `LLM_PROVIDER=null` never calls the model. For a real call, unset `LLM_PROVIDER` and set `LLM_API_KEY` in `.env`.
- Mock not running: `dispute-copilot run` prints `mock health check failed` and exits 1. Start `make mock` first.
- Port in use: `tools/runtime.py serve` exits 1 with `OSError: [Errno 98] Address already in use`. Stop the other process or pass a free `--port` and the matching `--mock-url`.

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

The process reads `.env` from the working directory and from the repository root. A non-empty value already set in the process wins. An empty process value is filled from the file. A line may start with `export`. A later line in the file replaces an earlier one for the same name. Matching quotes are removed. An unquoted ` #` comment is not part of the value. `$NAME` and `${NAME}` expand from names already read in the file, then from the process. A single-quoted value is left as written. Blank lines and `#` comments are skipped.

Copy `.env.example` to `.env` for a local run. That example sets `FEATURE_LLM_REVIEW=block` and leaves `LLM_API_KEY` empty. Check draft still saves the checklist. When the judge cannot run, the page shows a notice that names the missing setting (`LLM_API_KEY` when the key is empty). Submit stays allowed. The notice does not approve. To skip the judge entirely, use:

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
| `LLM_API_KEY` | model client | Haiku 4.5 key. Empty means no Haiku call. |
| `LLM_MODEL`, `AZURE_BASE_URL`, `LLM_API_STYLE`, `LLM_TIMEOUT_S` | model client | Set in `.env`. Unused when the provider is `null`. The model id is `claude-haiku-4-5`. Leave `AZURE_BASE_URL` empty when the run does not call a model. The host is not written in this file. |
| `JEV_BASE_URL` | ranker | Ranker host. Empty means the ranker is not built. Leave it empty in git. |
| `JEV_API_KEY` | ranker | Ranker key. Empty means the ranker is not built. |
| `JEV_MODEL` | ranker | Optional. Empty uses `jev-latest`. |
| `CLEF_API_KEY` | ranker fallback | Cloudflare Clef key. Empty means Clef is not built. Leave it empty in git. |
| `CLEF_ACCOUNT_ID` | ranker fallback | Cloudflare account id. Empty means Clef is not built. Leave it empty in git. |
| `CLEF_MODEL` | ranker fallback | Optional. Empty uses `clef-flash`. |
| `HTTP_TIMEOUT_S`, `RETRY_BASE_DELAY_S`, `RETRY_MAX_DELAY_S` | read tools | Override `config/rules/retry.yaml`. |
| `BREAKER_ENABLED`, `BREAKER_FAILURE_THRESHOLD`, `BREAKER_COOLDOWN_S` | read tools | Override `config/rules/breaker.yaml`. `0`, `false`, `off`, or `no` disables the breaker. |
| `MOCK_PORT`, `MOCK_STATE` | week-10 scripts and `scripts/idempotency_demo.py` | Default port `8766`, default state `.state/week10.sqlite`. |

This checkout tracks drafts, reviews, `submissions/results.json`, `reports/dev.json`, and `reports/audit.json`. Classification run output (`reports/classification/`, `submissions/classification/`) and `reports/metrics.json` are generated and ignored. Point `SUBMISSIONS_DIR`, `REPORTS_DIR`, and `--report` at a directory you do not commit if the tree should stay clean. `.state/` is ignored.

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
                       {run,run-all,compare,agent-review,submit-review,create-record,metrics,evals,dashboard}
                       ...
```

Global flags (also accepted on each subcommand): `--mock-url`, `--data-dir`, `--submissions`, `--label`, `-h`.

| Command | Arguments | Mock required | Exit 0 | Other exits |
|---|---|---|---|---|
| `run CASE` | `--approach 1\|2\|3\|ab`, `--shadow` | yes | draft written | 1 health or bad id; 2 case not written |
| `run-all` | `--approach 1\|2\|3\|ab`, `--shadow` | yes | every case written | 1 health; 2 any case skipped |
| `compare` | `--case CASE`, `--approaches 1,2,3` | yes | one row per approach, no draft | 1 health; 2 a case was not read |
| `agent-review CASE` | one case id | yes (health is checked) | `passed` true | 2 checklist failed, or no draft |
| `submit-review CASE` | one case id | yes | review posted | 2 refused or no draft |
| `create-record CASE` | one case id | yes | created, or existing row reported | 2 gate or missing handle |
| `metrics` | none | no | report printed | |
| `evals` | `--submission PATH`, `--report PATH` | no, when a submission file is already written | scorecard printed and `passed` | 1 when a minimum is missed |
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

## Classification

Approach 1 is the rules and is the default. Set the approach with `--approach` or `CLASSIFIER_APPROACH`. Allowed values are `1`, `2`, `3`, and `ab`. An empty value stays on approach 1.

```sh
uv run --locked dispute-copilot run CARD-D01 --approach 1
uv run --locked dispute-copilot run-all --approach 2
uv run --locked dispute-copilot run-all --approach ab --shadow
```

`--approach 2` asks the configured model after the rules, only on a case the rules leave open. With no model client, approach 2 keeps the rules class and records path `rules_only`. `--approach 3` asks the ranker first. A matching ranker class is kept. When the ranker is not configured, or its call fails, the rules class stays and Haiku 4.5 may still be asked. Manual review remains when the ranker says ambiguous, or when Haiku's class is rejected. `--approach ab` assigns the case from a seeded hash (`config/rules/classifier.yaml`, `ab.seed` and `ab.weights`). The draft uses the assigned approach.

`--shadow` also runs the approaches that were not selected. Each one is written under `submissions/classification/<approach>/CASE.json` and, when reports are configured, `reports/classification/<approach>/CASE.json`. Those directories are generated and ignored. The draft stays on the selected approach. The saved record does not include the complaint.

The model step returns one JSON object with `class`, `reason`, and `evidence`. `evidence` must be exact text copied from the complaint. Whitespace may be collapsed. A paraphrase is rejected and the rules result is kept. The saved record stores the quote check (`evidence_ok`, `evidence_sha256`, `evidence_start`, `evidence_end`) and does not store the quote text. A complaint sentence is removed from the saved reason.

`dispute-copilot compare` runs approaches 1, 2, and 3 on one fetched transaction list and writes the same records. It does not write a draft. `--case CASE` limits the run to one case. `--approaches 1,2` limits the set. `make compare` is the same command.

```sh
uv run --locked dispute-copilot compare --case CARD-D01
make compare
```

On the review page, the narrative is first, then the original complaint. The model note is closed until opened. Saving feedback posts the reviewer name, the correct category, a note, and `haiku_block_opened` (true when that note was opened). The record is `submissions/feedback/CASE.json` and `reports/feedback/CASE.json`. Feedback does not accept or decline the review.

The comparison page is served at `/compare` and written as `compare.html` beside a dashboard export. For each approach the five figures are the wrong-label rate, the auto-routed-but-wrong rate, the manual-review rate, Haiku cost per case, and latency p95. Wrong-label rate compares each saved approach, including a shadow run, with the reviewer category. Auto-routed but wrong counts only cases whose route is `ready_for_review`. Cost per case divides Haiku list-price USD by cases run. A case with no priced call adds zero. Jev is unpriced. A wrong-label sample or an auto-routed sample under `comparison.min_sample` (default 30) is marked too small to conclude. The agreement matrix counts shared cases. Disagreeing rows list case ids only. Technical details stay closed. The page does not include complaint text.

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

`--label NAME` on a command (or `RUN_LABEL`) stores `label` on each trace record. A case and a model call also store `where`: that label when one is set, otherwise the host class of the model endpoint (`azure`, `local`, or `other`). The endpoint, the key, and headers are not stored. The status page lists one row per place and model found in the saved runs. It does not add an empty place.

```sh
uv run --locked dispute-copilot run-all --label local
LOG_STDOUT=1 uv run --locked dispute-copilot run-all --label azure
```

### Status page

`dispute-copilot dashboard` reads traces, `logs/run.jsonl`, `logs/mcp.jsonl`, and `audit.json` when that file is present. It also reads `classification/` and `feedback/` under the reports directory and the submissions directory. It does not contact the mock. With `--export out.html` it writes that file and a sibling `compare.html`, then exits. Without `--export` it serves the status page and the comparison page on `127.0.0.1` only. The default port is `8754`. There is no host flag. GET `/`, GET `/index.html`, GET `/compare`, GET `/compare.html`, and GET `/health` are the successful routes. POST, PUT, PATCH, and DELETE return 405. The page does not approve, reject, reset, or post. The file uses no remote font and no remote script. A theme control switches light and dark; with no choice stored, the page follows the system setting. The comparison link is `compare.html`, next to the status file.

```sh
uv run --locked dispute-copilot dashboard --export reports/status.html
uv run --locked dispute-copilot dashboard
# http://127.0.0.1:8754
```

`make dashboard` is the serve form. Open the HTML file in a browser when you used `--export`.

### Scorecard

`dispute-copilot evals` reads a submission file, the development gold file, the versioned sets under `evals/sets/`, and the traces under `REPORTS_DIR`. It prints one scorecard and writes the JSON report given by `--report` (default `reports/evals.json`). Exit 1 means a category missed the minimum in `config/rules/evals.yaml`. The judge stays off. What each category covers, and the citation-support limit, is in `docs/EVALS.md`.

```sh
uv run --locked dispute-copilot evals --submission submissions/results.json --report reports/evals.json
make evals
```

`make evals` starts the mock on `127.0.0.1` with a temporary state file, drafts every development case, then runs the scorecard. A public mock URL is refused before the health check. The refusal is a `guardrail.boundary` event. See `docs/GUARDRAILS.md`.

The page opens on a short strip: cases run, cases passed, the good-case rate against the latency target, the alert count, and one safety line (injections flagged, gate refusals, records created without approval). Under that is the request path, then four cards: latency (p50, p95, the slowest case, and a small chart), cost (a total per case when a model call was priced, or a line that no model call was recorded), safety and approval, and where it runs (one row per place and model in the saved runs: cases, passed, p50, p95, tokens in, tokens out, and cost). A single place also shows a line that other environments appear when their runs are loaded. Full tables, the log, and identifiers stay under Technical details, which is closed until opened. A pass means the case was drafted inside the latency target. It is not a gold score and it is not an approval.

The request path is a left-to-right diagram of one saved run. Columns follow the case: intake, evidence reads side by side, ledger, rules and routing, draft, checklist, then a separate human stage. Known steps keep those columns. A saved name outside that list still appears, in Other steps. Retries sit on the step as chips with each status code and duration. A failed read is its own failed step, with a dashed line to the route that was taken. Routing shows three outcomes; the chosen one is marked and the other two stay quiet. An injection flag is a badge plus a side branch labelled treated as data. A step with no saved record stays on the diagram, dashed, and reads not run. The line that was taken is heavier; unused lines stay faint. The parallel reads and the join are labelled. Two runs stack on the same columns. Replay lights steps and the line between them in the saved order, at 0.5x, 1x, 2x, or 5x, with pause, restart, and a position control. Zoom, and fit to width, sit on the diagram. Choosing a step shows the saved detail and the log lines for it. The same diagram is in the exported file. The log caption, inside Technical details, counts the rows still visible after a filter. The latency chart uses a log scale so a short step stays visible.

Token prices live in `config/rules/cost.yaml` as USD per million tokens. A listed model is labelled `estimate from list price`. A model with no entry is labelled `no price configured for <model>`. Compute cost is `estimate, unmeasured` unless `compute.usd` is filled with a measured figure. The page runs the same redactor as the logs before it is written.

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

`make lint` runs `uv run --locked python tools/lint_gates.py` and prints `lint gates passed` with the source-file count. Exit 1 lists problems on stderr. The gate scans `src/**/*.py` for a mention of the fixture file, a restricted marker, an `approve` definition or call, and a contract name spelled outside `fields.py`.

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

Make targets that exist: `sync`, `lint`, `test`, `check`, `daily`, `mock`, `regen-gold`, `metrics`, `dashboard`, `secrets`, `evals`, `mcp`, `week10`, `week10-duplicate`. The GitHub workflow and the six job names used for branch protection are in `docs/CI.md`.

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
