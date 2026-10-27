# Hardening checklist: failure cases mapped to patterns

Card Dispute Copilot. Each brief failure situation (`BRIEF.md`) is mapped to the pattern that handles it, the code, the test, and the evidence file from `scripts/week10_evidence.sh`.

| Failure case | Dev case | Pattern | Code | Test | Evidence |
|---|---|---|---|---|---|
| Transaction service outage (persistent 503) | CARD-D07 | Retries with exponential backoff and capped jitter, then graceful degradation: amount and currency null, `manual_review`, outage cited | `adapters/tools.py:read_with_retries`, `reliability/retry.py:RetryPolicy`, `workflow.py:_named_facts` | `test_reliability.py::test_timeouts_on_every_attempt_fail_predictably_after_three`, `test_workflow.py::test_three_transaction_failures_keep_the_intake_and_leave_amount_empty` | `02_timeout_recovery.txt`, `04_scenarios.txt` |
| Transient upstream timeout | CARD-D10 | Retry with backoff, recover on attempt 2 | same | `test_reliability.py::test_backoff_waits_between_attempts_and_recovers` | `02_timeout_recovery.txt` |
| Request that hangs past the timeout | test double | `HTTP_TIMEOUT_S` per request; a timeout is a retryable attempt | `adapters/tools.py:HttpToolClient._read` | `test_reliability.py::test_a_real_http_timeout_is_retried_then_recovers` | `02_timeout_recovery.txt` (`scripts/fault_demo.py timeout`) |
| Sustained outage across many cases | test double, and the mock with threshold 1 | Cross-case circuit breaker per dependency (ADR-30). Never cuts the 3 attempts inside a case | `reliability/breaker.py:CircuitBreaker` | `test_reliability.py::test_breaker_*`, `test_an_open_breaker_takes_the_existing_degradation_path` | `03_breaker.txt` |
| Duplicate record creation (double submit, network retry) | CARD-D02 | Idempotency key from server state; records pre-check; mock replays the same key (200) and rejects a changed body (409). This is the ledger. It is separate from the charge guard below | `serialization.py:idempotency_key`, `review_flow/__init__.py:create_record` | `test_live_checkpoint.py` (second create posts nothing, one `record_created`) | `09_idempotency.txt` (after human approval) |
| Duplicate class without a second matching charge | a duplicate wording with one returned charge, or two charges that differ in merchant, amount, or currency | The class does not stand: `ambiguous`, deadline null, `manual_review`. Two charges with the same merchant id, amount, and currency keep `duplicate_charge`. An unavailable transaction read keeps `duplicate_charge` and `manual_review`, and the deadline is still calculated. Every approach uses this guard | `classifiers/guard.py:apply_duplicate_guard` | `test_workflow.py::test_one_charge_does_not_stand_as_a_duplicate`, `test_two_exact_charges_keep_a_duplicate`, `test_two_charges_with_different_amounts_do_not_stand`, `test_unavailable_transactions_keep_a_duplicate` | `tests/unit/test_workflow.py` |
| Duplicate intake / redelivered complaint | CARD-D09 (twin of D02) | Pre-check `GET /v1/records/CASE` resolves the logical case; no second POST. Mock returns 409 `duplicate_case` for a new key | `review_flow/__init__.py:create_record` | same | `09_idempotency.txt` |
| Prompt injection in merchant metadata | CARD-D08 | Input/context separation: rules read only labeled lines and enum values; merchant prose is never copied; instruction-like text is flagged and logged as data | `rules/documents.py`, `workflow.py:_merchant`, `evidence/untrusted.py` | `test_security.py::test_injected_merchant_note_is_treated_as_data` | `05_injection.txt` |
| Ambiguous (conflicting statements) | CARD-D05 | Escalate: `ambiguous`, no deadline, `manual_review` | `rules/classify.py`, `rules/routing.py` | `test_workflow.py::test_conflicting_customer_statements_have_no_deadline` | `04_scenarios.txt` |
| Unsupported (cash withdrawal) | CARD-D06 | Escalate: `unsupported`, `manual_review` | `rules/classify.py` | `test_workflow.py::test_cash_not_dispensed_is_unsupported_even_when_authorization_is_confirmed` | `04_scenarios.txt` |
| Stale policy / boundary date | CARD-D11 (reported 2026-08-31) | Select policy by reported date, never today's date; out-of-range date stops the case | `retrieval/policy.py:PolicyResolver.select` | `test_policy.py` boundary tests, `test_rules_do_not_read_the_wall_clock` | `04_scenarios.txt` |
| Missing information | CARD-D04, CARD-D12 | Ask once: `request_information` with targeted questions | `rules/questions.py` | `test_workflow.py::test_a_service_with_two_blank_fields_asks_both` | `04_scenarios.txt` |
| Create before approval / changed draft | any | Human boundary in code: `approval_gate` and server-hash check; mock 403 | `review_flow/gate.py:approval_gate` | `test_approval.py`, `test_live_checkpoint.py` | `06_human_boundary.txt` |
| Restricted value leak | any | Redactor on drafts, citations, and every trace attribute. The original complaint is rendered only on the review page, and that page is withheld when the redactor finds a restricted value. Classification audits and the comparison page omit the complaint | `evidence/redaction.py`, `observability/tracing.py:safe`, `UI/review_page.py` | `test_observability.py::test_spans_nest_and_attributes_are_redacted` | trace files in `evidence/week10/traces/` |

## Observability

| What | Where |
|---|---|
| Trace per case, spans for intake, rules, each tool call and attempt, backoff, breaker, policy, routing, citations, ledger, contract, redaction, review gate, record create | `observability/tracing.py`; files `reports/traces/<case>.jsonl` and `<case>.<command>.jsonl` |
| JSON-lines log, one line per span or event | `observability/logs.py`; `reports/logs/run.jsonl` (`LOG_STDERR=1` also prints to stderr) |
| Metrics tied to the SLO | `observability/metrics.py`; `reports/metrics.json` |
| Classification audit, one file per approach, no complaint text | `classifiers/audit.py`; `submissions/classification/<approach>/<case>.json` and the same path under `reports/` |
| Comparison page: wrong-label rate, auto-routed-but-wrong rate, manual-review rate, Haiku cost per case, latency p95, agreement, disagreeing case ids | `observability/compare_page.py`; sibling `compare.html` from `dispute-copilot dashboard --export` |
| Alert rules | `config/rules/slo.yaml` |

## Metrics and alerts

Metrics: `good_case_rate` (the SLO), `valid_contract_rate`, `case_latency_ms` (median, p95, max), `tool_exhausted_rate`, `tool_retries`, `breaker_opens`, `breaker_skips`, `manual_review_rate`, `untrusted_instruction_cases`, `gate_blocked`, `error_budget`.

Classification latency p95 on the comparison page comes from audit `latency_ms`. It does not replace `good_case_rate` or `case_latency_ms`. Haiku 4.5 cost there is list-price USD divided by cases run (1.00 USD per million input tokens, 5.00 USD per million output tokens). Jev is unpriced. Approach 1 does not call a model.

| Alert | Condition | Severity |
|---|---|---|
| `tool_exhausted_rate_high` | `tool_exhausted_rate > 0.25` | page |
| `good_case_rate_below_slo` | `good_case_rate < 0.95` | page |
| `unapproved_create_attempt` | `gate_blocked > 0` | page |
| `breaker_opened` | `breaker_opens > 0` | ticket |
| `untrusted_instruction_spike` | `untrusted_instruction_cases > 2` | ticket |

`run-all` and `dispute-copilot metrics` print the alerts that fired.
