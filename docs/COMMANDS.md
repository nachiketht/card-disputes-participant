
# Commands

Run these from the repository root. Python is 3.12. The mock listens only on 127.0.0.1.

The copilot drafts and creates records. It never approves. Approval is a separate command against the same state file the mock was started with.

## Install

```sh
uv sync --locked
uv run --locked python tools/validate_package.py
```

`make sync` is the same install.

Optional model client, not used by the current draft path:

```sh
uv sync --locked --extra openai
```

## Mock

Start it in one terminal and leave it running. Assessed runs use `.state/run.sqlite`, not the old seed database.

```sh
make mock
```

That is:

```sh
uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite
```

Other data or port:

```sh
uv run --locked python tools/runtime.py serve \
  --data data/dev \
  --state .state/run.sqlite \
  --port 8765
```

Check it from a second terminal:

```sh
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/v1/cases
```

Stop the server with Ctrl-C before a reset. Pass the same `--state` to every operator command.

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
mkdir -p reports
uv run --locked python tools/runtime.py audit --state .state/run.sqlite > reports/audit.json
uv run --locked python tools/runtime.py reset --confirm --state .state/run.sqlite
```

## Draft, review, create

Flags may come before or after the subcommand. Defaults are `http://127.0.0.1:8765`, `data/dev`, and `submissions/`. The same values can be set with `MOCK_BASE_URL`, `DATA_DIR`, and `SUBMISSIONS_DIR`.

```sh
uv run --locked dispute-copilot --help
uv run --locked dispute-copilot run CARD-D01
uv run --locked dispute-copilot submit-review CARD-D01
uv run --locked dispute-copilot create-record CARD-D01
```

Explicit paths:

```sh
uv run --locked dispute-copilot \
  --mock-url http://127.0.0.1:8765 \
  --data-dir data/dev \
  --submissions submissions \
  run CARD-D01
```

`run` writes `submissions/drafts/CARD-D01/vN.json` and upserts that case into `submissions/results.json`. It does not submit a review.

`submit-review` posts that saved draft and writes `submissions/reviews/CARD-D01.json`.

`create-record` checks for an existing record, re-reads the review, checks the server hash, and posts the server draft. If the review is still pending, it exits without posting. Approve with `runtime.py approve` in between, using the `review_id` from the review file.

`run` drafts every development case the procedure can classify. A case with no readable intake, or a reported date outside every loaded policy, exits without a draft and prints the reason.

```sh
uv run --locked dispute-copilot run-all
```

`run-all` writes one draft per case the mock lists. It exits 0 when every case was written, and 2 when any case was skipped.

The original one-file CARD-D01 script is still available. It talks to whatever mock is on the port. It does not choose the state file.

```sh
uv run --locked python scripts/normal_case.py run
uv run --locked python scripts/normal_case.py submit-review
uv run --locked python scripts/normal_case.py create
```

## Evaluate

Score a submission. Full development gold has 12 cases. `run-all` against the development mock writes all 12. A submission that omits a case fails that case.

```sh
mkdir -p reports
uv run --locked python tools/evaluate.py \
  --data data/dev \
  --gold feedback/dev_gold.json \
  --submission submissions/results.json \
  --report reports/dev.json
```

## Tests and gates

```sh
make lint
make test
make check
make daily
```

`make check` is lint plus pytest. `make daily` is the same check, then a note that the rules engine drafts every development case.

Direct equivalents:

```sh
uv run --locked python tools/lint_gates.py
uv run --locked pytest
uv run --locked pytest tests/unit
uv run --locked pytest tests/integration
```

Staged-file gate, which is also what the pre-commit hook runs:

```sh
uv run --locked python tools/lint_gates.py --staged
```

Enable the committed hook for this clone:

```sh
git config core.hooksPath .githooks
```

`make regen-gold` exists and exits 2 on purpose. It refuses to rewrite `feedback/dev_gold.json`. Hidden-style gold is not in this checkpoint.

## Reliability, tracing and metrics

Read tools retry a 503 or a request timeout up to 3 total attempts with exponential backoff and capped jitter (`config/rules/retry.yaml`). A cross-case circuit breaker per dependency (`config/rules/breaker.yaml`, ADR-30) opens only after consecutive cases exhausted every attempt. Overrides:

```sh
HTTP_TIMEOUT_S=10 RETRY_BASE_DELAY_S=0.05 RETRY_MAX_DELAY_S=0.2
BREAKER_ENABLED=1 BREAKER_FAILURE_THRESHOLD=3 BREAKER_COOLDOWN_S=30
```

Every `run`, `run-all`, `agent-review`, `submit-review` and `create-record` writes a redacted trace to `reports/traces/` and JSON log lines to `reports/logs/run.jsonl`. `REPORTS_DIR` moves both. `LOG_STDERR=1` also prints log lines to stderr.

```sh
uv run --locked dispute-copilot metrics                      # reports/metrics.json and fired alerts
uv run --locked python scripts/trace_view.py reports/traces/CARD-D07.jsonl
```

SLO, alerts and runbook: `docs/SLO.md`, `config/rules/slo.yaml`, `docs/RUNBOOK.md`. Mapping of failure cases to patterns: `docs/HARDENING_CHECKLIST.md`. Audit: `docs/SECURITY_AUDIT.md`.

## Week 10 evidence

```sh
make week10             # scripts/week10_evidence.sh: own mock on :8766, writes evidence/week10/
```

It stops at the human step and prints an approve command for CARD-D02. A person runs that command, then:

```sh
make week10-duplicate   # scripts/week10_duplicate.sh: create once, replay four ways, one record
```

Fault demos against a local test double (hang past the timeout, 503, recovery, breaker open and close):

```sh
uv run --locked python scripts/fault_demo.py timeout
uv run --locked python scripts/fault_demo.py breaker
```

## Bake-offs

These are the planned commands. They are not wired up. There is no `make bakeoff` target and no bake-off package yet. Running them will fail until that work lands.

The bake-off reruns strategy axes one at a time. The other axes stay on the default. It writes `reports/bakeoff/<axis>/<strategy>/results.json`, `reports/bakeoff.md`, and `reports/bakeoff.json`. It does not write `submissions/`. It does not call `submit-review` or `create-record`. It uses its own mock on `127.0.0.1:8766` with a temporary state file, or the fake adapters.

```sh
make bakeoff
make bakeoff AXIS=retrieval
make bakeoff AXIS=retrieval BAKEOFF_ADAPTERS=fake
make bakeoff AXIS=drafter STRATEGIES=default,openai
make bakeoff AXIS=parsing
make bakeoff AXIS=classification
make bakeoff AXIS=deployment TARGET=local
make shadow-eval
make evaluate
```

Environment for a single axis, once those flags exist:

```sh
RETRIEVAL_STRATEGY=label    # label | bm25 | hybrid
LLM_PROVIDER=null           # null | openai | ollama
PARSER_MODE=strict          # strict | aliases
FEATURE_LLM_CLASSIFY=off    # off | shadow
DEPLOY_TARGET=local         # local | aws | azure
BAKEOFF_ADAPTERS=fake       # fake | http
```

A cloud or local-model drafter column also needs the approved `base_url`. No model call is part of the current default path.

The deployment comparison, when that axis is built, is `reports/deployment_comparison.md`. A column that was not run is recorded as `not run: <reason>`.

`make judge` is a later advisory narrative check. It is not a bake-off axis and it is not in CI.
