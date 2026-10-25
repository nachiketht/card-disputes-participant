# SLO and error budget

## SLO

**95% of cases get a contract-valid draft, with the correct escalation route, within 2 seconds of the case starting, measured over a rolling 7 days.**

- A case is *good* when `run` writes a draft that passes `validate_draft` and the case span (`case` in `reports/traces/<case>.jsonl`) lasts 2000 ms or less.
- A case is *bad* when it is not written (skipped, crashed, leak refused) or is slower than 2000 ms.
- Measured from traces by `dispute-copilot metrics` into `reports/metrics.json` (`good_case_rate`). Thresholds live in `config/rules/slo.yaml`.
- A correct `manual_review` on an outage is a good case. The procedure says to degrade, not to fail.

## Why 2 seconds

Measured on the local mock with default settings (`evidence/week10/04_scenarios.txt`, 2026-10-25):

| Measure | Value |
|---|---|
| Cases | 12 |
| Median case latency | 9.8 ms |
| p95 / max | 145 ms (CARD-D07: 3 attempts plus two backoff waits) |
| Transient retry case (CARD-D10) | 44 ms |
| Valid-contract rate | 12/12 |

The worst case today is three attempts with backoff capped at 0.2 s. With the 10 s HTTP timeout, a hung dependency can cost up to about 30 s per case, which is what the SLO is meant to catch. 2 s leaves room for real network latency to the transaction and merchant services while still flagging a hung dependency.

## Error budget

- Budget: 5% of cases may be bad.
- At 200 cases a day, that is 1,400 cases a week and **70 bad cases a week** (10 a day).
- On a 12-case dev run it is 0.6 cases, so **any** bad case in a dev run breaks the SLO for that run.
- `reports/metrics.json` reports `error_budget.allowed_bad_cases`, `bad_cases` and `remaining_fraction`.

## Near exhaustion (under 25% of the weekly budget left)

1. Freeze changes to rules, retry settings and prompts. Only fixes for the cause go in.
2. Find the cause from metrics: `tool_exhausted_rate` and `tool_by_dependency` (outage), `case_latency_ms.p95` (slow dependency), `valid_contract_rate` (contract or leak refusals).
3. If a dependency is down, let the breaker open (it is already on) and tell reviewers to expect more `manual_review`. Do not raise retry attempts above 3; the procedure fixes that.
4. If drafts are failing validation, stop `run-all` and fix before more cases are processed. Bad drafts are never submitted, so the risk is delay, not wrong records.
5. Record the incident and the fix in `docs/CHANGES.md`.

Once the budget is spent, the rule is the same plus: no feature work until a full 7 days is back inside the SLO.
