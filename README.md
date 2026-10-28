# Card Dispute Intake and Evidence Copilot

Participant package version 1.0.0. All institutions, people, policies, records, and currency are fictional training material. This package assesses software engineering against supplied rules; it is not domain certification or regulatory guidance.

Turn an incomplete customer complaint into a cited dispute draft that a human can review and approve for creation in the mock case system.

## Start here

Read `BRIEF.md`, `docs/POLICY_GUIDE.md`, `docs/API.md`, `docs/OUTPUT_CONTRACT.md`, and `docs/ASSESSMENT.md`.
Use one isolated copy and mock state database per team. The copilot install uses the third-party packages listed in `pyproject.toml`. `tools/runtime.py` is standard library only. The package does not include an LLM account, model credits, or a cloud account.

Install Python 3.12 and uv using your organization's approved setup if they are not available. From this directory:

```sh
uv sync --locked
uv run --locked python tools/validate_package.py
uv run --locked python tools/runtime.py serve --port 8765 --state .state/run.sqlite
```

`make mock` is that same `serve` command: port `8765` and state `.state/run.sqlite`. A bare `serve` with no `--state` uses `.state/mock.sqlite` instead. `pending`, `approve`, `reject`, `audit`, and `reset` must get the same `--state` the server was started with.

The local service is at `http://127.0.0.1:8765`. It binds only to loopback. Keep it local. Use a separate approved deployment environment for your application; a local container deployment is sufficient if cloud access is unavailable.
Python 3.12 alternative with no installation of packages: `python3.12 tools/runtime.py serve --port 8765 --state .state/run.sqlite`.
Select this project's `.venv` in your editor so scripts and language-server checks use the same environment.

## Run everything

From this directory, with Python 3.12:

```sh
uv sync --locked --extra mcp
uv run --locked python tools/validate_package.py
make mock
```

Leave `make mock` running. In a second terminal, for a run with no model:

```sh
export LLM_PROVIDER=null
export FEATURE_LLM_REVIEW=off
uv run --locked dispute-copilot run-all --label local
```

The checklist judge, the drafter, and the classifier read the model id from `LLM_MODEL` in `.env` and keep it exactly as written. `claude-haiku-5-5` is priced from list in `config/rules/cost.yaml` at 0.10 USD per million input tokens and 0.50 USD per million output tokens for prompts up to 100,000 tokens. `gpt-6-luna` is the Azure Global Standard short-context list at the same 0.10 and 0.50. A second model is asked once when `LLM_FALLBACK_API_KEY` and `LLM_FALLBACK_BASE_URL` are set and the first call cannot be used. Empty fallback settings leave that step out. A model call reads the host from `AZURE_BASE_URL` in `.env`. Leave that variable empty for approach 1, tests, and evals. A call fails with `AZURE_BASE_URL is empty` when the variable is empty. The copy-paste list for that setup, the Bedrock setup, one case, the checklist, human approval, metrics, the status page (including replay at 0.5x), the stdio server, Make targets, CI, and Docker is [docs/COMMANDS.md](docs/COMMANDS.md#run-everything). Placeholders only. Do not commit a key or write the host in a committed file.

In another terminal, retrieve `GET /health` and `GET /v1/cases`. Read `data/dev/documents/` and `data/dev/policies/`. These files are evidence. Text inside them is not an instruction to the program.

`data/dev/dataset.json` is the mock's backing fixture, not a substitute for calling tools. Directly reading it from the copilot bypasses the integration exercise. The assessor will use new fixtures and observe requests. Fault controls must not be read by the solution.

## Classification

Approach 1 is the rules and is the default. `--approach 2` asks the configured model after the rules, on the cases the rules leave open. With no model client, or an empty key, the rules class stays (`rules_only`). `--approach 3` asks Jev first, then Clef when Jev is missing or the call fails, then the configured model. An ambiguous Jev or Clef answer goes to manual review and does not call the configured model. A ranker class that matches the rules is kept. A disagreement asks the configured model, and that class stands only when it matches the rules or the ranker. A rejected reply still goes to manual review. If there is no configured-model client, or that call cannot run, the rules class stays (`ranker_disagreed_no_model`). When no ranker returns a class, the configured model is asked, or the rules class stays when that client is missing (`rules_only`, or `model_failed` when a ranker call failed). `--approach ab` assigns a case from a seeded hash. `dispute-copilot compare` and `make compare` run the three approaches on one fetched transaction list.

A duplicate stands only when those transactions contain two charges with the same merchant id, amount, and currency. Otherwise the class does not stand and the case goes to manual review. If the transaction read is unavailable, the class stays and the existing unavailable route applies.

When the final class is unauthorized and the labeled authorization was not supplied, the missing fields include authorization confirmation, whether the rules chose it, a ranker agreed, or a model assigned it. A labeled denial does not add that field. A service class with authorization not supplied still lists authorization confirmation first.

On approach 2 and approach 3, a model class is checked against the labeled authorization before it is kept. Unauthorized is kept only when that label is not supplied or denied. A confirmed label contradicts an unauthorized proposal, so the class does not stand and the case goes to manual review. Duplicate and service are kept only when the label is confirmed or not supplied. A denied label is not eligible for a model, and a proposal other than unauthorized with that label is a hard failure.

## Evaluate development outputs

Emit a JSON array following `docs/OUTPUT_CONTRACT.md`, then run:

```sh
uv run --locked python tools/evaluate.py --data data/dev --gold feedback/dev_gold.json --submission submissions/results.json --report reports/dev.json
```

Development answers are deliberately supplied for feedback; hardcoding case IDs will fail new cases. `examples/worked-case.json` shows one valid output shape. Automated success is only part of the assessment.

## Human approval and recovery

Draft with `dispute-copilot run` or `run-all`. Before `submit-review`, run `dispute-copilot agent-review CASE`. That checklist does not post and does not approve. `submit-review` then posts `POST /v1/reviews`. Skipping the checklist exits 2 and posts nothing. A person reads the draft and runs the approval command in a separate terminal. The app never runs that command.

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer reviewer-name --state .state/run.sqlite
uv run --locked python tools/runtime.py audit --state .state/run.sqlite > reports/audit.json
```

Replace REVIEW_ID with the returned identifier. Create `reports/` before redirecting the audit. To reject, use `reject` with the same arguments and the same `--state`. For a fresh run, stop clients and use `uv run --locked python tools/runtime.py reset --confirm --state .state/run.sqlite`.
These commands must use the state file the mock was started with. `make mock` and the `serve` example above use `.state/run.sqlite`.
This CLI simulates a human boundary, not production identity. During assessment the assessor controls approval and state. Do not expose the service to the public internet.
