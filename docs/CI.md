# CI

Workflow file: `.github/workflows/ci.yml`. It runs on every push and every pull request. It uses no repository secrets. `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off`, so the judge does not run and no model endpoint is called. Package install is the only network use. There is no cloud deploy job.

Required check names, in workflow order, for branch protection:

| Job | What a failure means |
|---|---|
| `lint` | `uv sync --locked --extra mcp`, package validation, or `tools/lint_gates.py` failed |
| `test` | the full pytest suite failed |
| `evaluate` | the loopback mock, `run-all`, or `tools/evaluate.py` failed, `reports/dev.json` differs from that fresh report, or the scorecard missed a minimum |
| `security` | adversarial, leak, approval, or stdio-server tests failed, or the server registers an approve, reject, or reset call |
| `secrets` | a tracked file matches a credential pattern, or `.env` is tracked |
| `image` | `docker build` failed, or one case did not draft through the image's loopback mock |

`evaluate` starts `tools/runtime.py serve` on `127.0.0.1` with a temporary state file. It does not publish that port. A fresh `run-all` must pass every development case. `tools/check_fresh_report.py` then compares `reports/dev.json` with that report. After the mock is stopped, `dispute-copilot evals` scores that same submission. A category under its minimum in `config/rules/evals.yaml` fails the job. The judge stays off. The job uploads the traces, `metrics.json`, the fresh evaluation, the scorecard, and the status-page HTML as the artifact `traces-metrics-dashboard`.

`image` runs `tools/image_smoke.sh`. That script builds `card-disputes:ci` and does not push. The mock process inside the image is checked on `127.0.0.1:8765`. A second container shares that network namespace and drafts `CARD-D01`. No host port is published.

`secrets` runs `tools/scan_secrets.py` with the system Python. It does not install the package.

Local equivalents:

```sh
uv sync --locked --extra mcp
uv run --locked --extra mcp python tools/lint_gates.py
uv run --locked --extra mcp pytest
python3 tools/scan_secrets.py
uv run --locked --extra mcp python tools/check_mcp_names.py
uv run --locked python tools/run_evals.py
```

The development score still needs a mock on `127.0.0.1` and a temporary state file, then `run-all` and `tools/evaluate.py`. The pre-commit hook (`.githooks/pre-commit`) runs the staged lint gate and the secret scan. It does not start the mock or build the image.

This workspace did not run the `image` job. `docker` was not installed here. The evaluate path was run locally with a temporary state file. `tools/evaluate.py` passed 12 of 12, and `reports/dev.json` matched that report.
