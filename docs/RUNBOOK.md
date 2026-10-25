# Runbook

Most severe failure for this copilot: **a prompt-injection attack in merchant metadata or a customer attachment** (dev case CARD-D08). The worst outcome would be a promised refund, a leaked private value, or a record created without approval. The second section covers the **ambiguous case** that must reach a human (CARD-D05). The last covers a **dependency outage** (CARD-D07).

Paths below assume the defaults: mock on `127.0.0.1:8765`, state `.state/run.sqlite`, traces in `reports/traces/`, logs in `reports/logs/run.jsonl`. Times in traces are UTC.

## Start and health

```sh
make mock                                   # terminal 1
curl -s http://127.0.0.1:8765/health        # terminal 2: {"status": "ok", ...}
uv run --locked dispute-copilot run-all     # drafts, traces, reports/metrics.json
uv run --locked dispute-copilot metrics     # recompute metrics and alerts from traces
```

Reset before an independent run (stop the mock first; never reset mid-review):

```sh
uv run --locked python tools/runtime.py reset --confirm --state .state/run.sqlite
```

## 1. Prompt injection (alert `untrusted_instruction_spike`, or a reviewer report)

**Check first (2 minutes): did anything get written?**

```sh
uv run --locked python tools/runtime.py audit --state .state/run.sqlite \
  | python -c 'import json,sys; [print(r["at"], r["event"], r["case_id"]) for r in json.load(sys.stdin) if r["event"] in ("record_created","unapproved_write_attempt","idempotency_conflict")]'
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
```

A `record_created` for the case without a human reviewer, or any `unapproved_write_attempt`, is a breach: go to mitigation step 1 now.

**Confirm the cause.**

```sh
grep '"security.untrusted_instruction"' reports/logs/run.jsonl          # which cases, which source, which patterns
uv run --locked python scripts/trace_view.py reports/traces/CARD-D08.jsonl
curl -s http://127.0.0.1:8765/v1/cases/CARD-D08/merchant                # the raw payload (contains a private value; do not paste it anywhere)
grep -c -E 'SYSTEM OVERRIDE|refund|SYNTHETIC-' submissions/drafts/CARD-D08/v*.json   # expect 0
```

- Flag present, draft clean, routing equal to what the evidence supports: the control worked. The case can go to review as normal. Tell the reviewer the merchant record carried an instruction.
- Draft contains injected text, a refund promise, or a restricted value: the separation failed. That should be impossible because the narrative is a template and `Redactor.assert_clean` runs before write, so treat it as a code regression.
- Routing differs from the evidence (for example a changed classification): check `rules.classify` and `rules.routing` spans; rules read labeled lines only, so look for a forged labeled line (`Authorization:` etc.) inside a document.

**Immediate mitigation.**

1. Do not approve the review. Reject it so it cannot be created:
   `uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite`
2. Turn the model step off so no untrusted text reaches a model: `FEATURE_LLM_REVIEW=off` in `.env`.
3. Stop batch runs (`run-all`) until the cause is known; single cases with manual review are fine.
4. If a record was created wrongly, it cannot be deleted through the API. Record the record id from the audit, escalate to the dispute operations lead, and log it in `docs/CHANGES.md`.
5. Add the payload as a regression fixture next to `tests/unit/test_security.py::test_injected_merchant_note_is_treated_as_data`.

## 2. Ambiguous case not reaching a human (CARD-D05)

**Check first.** `grep CARD-D05 submissions/results.json` and the routing in `submissions/drafts/CARD-D05/vN.json`. Expected: classification `ambiguous`, routing `manual_review`, deadline null, `conflicting_evidence` citations from intake and follow-up.

**Confirm.** `uv run --locked python scripts/trace_view.py reports/traces/CARD-D05.jsonl` and look at `documents.followups` (`followups` should be 1) and `rules.classify` (`conflict=true`). `followups=0` means the follow-up document was not listed by the mock or is missing from `data/*/documents/`.

**Mitigate.** Do not approve a non-`manual_review` draft for this case. Reject it, fix the data or the parser, rerun the case (`dispute-copilot run CARD-D05` writes vN+1), and submit the new version.

## 3. Dependency outage (alerts `tool_exhausted_rate_high`, `breaker_opened`)

**Check first.** `uv run --locked dispute-copilot metrics` and read `tool_by_dependency` and `breaker_opens`. Then `grep -E '"(tool.exhausted|breaker.transition|breaker.skip)"' reports/logs/run.jsonl`.

**Confirm.** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/v1/cases/CARD-D01/transactions`. A 503 `upstream_timeout` is the dependency. In the mock, a persistent fault lasts until reset.

**Mitigate.** Nothing to do in code: each case still gets its 3 attempts, degrades to amount null and `manual_review`, and cites the outage sentence. With the breaker open, later cases skip the call until the cooldown ends, then one probe case tests it. Tell reviewers more cases will be `manual_review`. When the service is back, rerun the degraded cases so drafts get amounts (`dispute-copilot run CASE`, new version, new review). Breaker settings: `config/rules/breaker.yaml` or `BREAKER_FAILURE_THRESHOLD`, `BREAKER_COOLDOWN_S`, `BREAKER_ENABLED=0`.

## Approve, reject, audit (human only)

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite
uv run --locked python tools/runtime.py audit --state .state/run.sqlite > reports/audit.json
```
