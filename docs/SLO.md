# SLO and error budget

## SLO

**95% of case spans are drafted within 2 seconds of the case starting, measured over a rolling 7 days.**

- A case is *good* when its `case` span has `outcome` `drafted` and `duration_ms` at or under 2000. `good_case_rate` is that count divided by every `case` span. The metric does not read the route.
- A case is *bad* when that span was not written, or it is slower than 2000 ms.
- Measured from traces by `dispute-copilot metrics` into `reports/metrics.json`. `config/rules/slo.yaml` holds the 2000 ms target, the 0.95 objective, the alert thresholds, and the same good-case sentence: drafted, inside the latency target, route not included. The rate above is what the program counts.
- A drafted outage case inside the time limit counts as good, including `manual_review` after transactions fail. The procedure says to degrade. The metric does not check that the route matches the procedure.

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
