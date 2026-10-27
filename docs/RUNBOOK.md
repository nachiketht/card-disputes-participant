# Runbook

Most severe failure for this copilot: **a prompt-injection attack in merchant metadata or a customer attachment** (dev case CARD-D08). The worst outcome would be a promised refund, a leaked private value, or a record created without approval. The second section covers the **ambiguous case** that must reach a human (CARD-D05). The last covers a **dependency outage** (CARD-D07).

Paths below assume the defaults: mock on `127.0.0.1:8765`, state `.state/run.sqlite`, traces in `reports/traces/`, logs in `reports/logs/run.jsonl`. Times in traces are UTC.

## Start and health

```sh
make mock                                   # terminal 1; state .state/run.sqlite, port 8765
curl -s http://127.0.0.1:8765/health        # terminal 2: {"status": "ok", ...}
uv run --locked dispute-copilot run-all     # drafts, traces, reports/metrics.json
uv run --locked dispute-copilot agent-review CASE
uv run --locked dispute-copilot submit-review CASE
uv run --locked dispute-copilot metrics     # recompute metrics and alerts from traces
```

`submit-review` before a passing `agent-review` for that draft exits 2 and does not post. Approval is the human command at the bottom of this page. The app never approves. Command order and exits are in `docs/COMMANDS.md`.

Default classification is approach 1 (rules only). Pass `--approach 2`, `--approach 3`, or `--approach ab` on `run` and `run-all` to select another approach. `--shadow` writes an audit for every approach and leaves the draft on the selected approach. `dispute-copilot compare` runs approaches 1, 2, and 3 on one fetched transaction list.

Leave `JEV_BASE_URL` and `JEV_API_KEY` empty to skip the ranker. `JEV_MODEL` is optional and defaults to `jev-latest`. Approach 3 with no ranker does not call Haiku 4.5 for that case; the class is ambiguous and the route is `manual_review`. The threshold placeholder is `0.70` in `config/rules/classifier.yaml`. Jev cost is unpriced. Haiku 4.5 list price is 1.00 USD per million input tokens and 5.00 USD per million output tokens.

A duplicate stands only when the fetched transactions contain two charges with the same merchant id, amount, and currency. One charge, or two charges that differ, does not keep that class: the class is ambiguous, the deadline is null, and the route is `manual_review`. An unavailable transaction read keeps `duplicate_charge`, uses `manual_review`, and still calculates the deadline. The check runs on every approach.

A model is asked only on an eligible case: ambiguous with no conflict, or unauthorized from complaint wording while authorization is not supplied. A labeled denial, a conflict, and a rules class of unsupported, duplicate, or service are not sent. A proposed unauthorized class is kept only when the label is not supplied or denied. A confirmed label on that proposal does not stand. Duplicate and service are kept only when the label is confirmed or not supplied.

Classification audits under `submissions/classification/` and `reports/` do not store the complaint. The review page (`dispute-copilot review-page`) is the only page that shows the original complaint, then the narrative, then a closed Haiku block. The redactor still runs on that page. A restricted value withholds it. Feedback is stored under `submissions/feedback/` and, when reports are configured, `reports/feedback/`. The record includes whether that block was opened (`haiku_block_opened`). Saving feedback does not approve. Reviewer login is phase 2.

Open the status page after a run. It is read-only and, when served, binds `127.0.0.1` only:

```sh
uv run --locked dispute-copilot dashboard --export reports/status.html
uv run --locked dispute-copilot dashboard
```

The first command writes the status HTML and a sibling `compare.html`. The second serves `http://127.0.0.1:8754`, including `/compare` and `/compare.html`. Neither command approves a case. Tag a run with `--label local` or `--label azure` when that place should appear on the status page. A place is listed only when a saved run has it. List prices are in `config/rules/cost.yaml`.

The comparison page reads saved classification audits. Per approach it shows cases run, the share that called a model, the manual-review rate, an agreement matrix, disagreeing case ids, latency p50 and p95, Haiku cost from list price, Jev as unpriced, and the wrong-label rate from reviewer feedback with the sample size. A sample under `comparison.min_sample` (30) is marked too small to conclude. Disagreeing rows are case ids only. The page does not include the complaint. Those latency figures are not the case-span SLO in `docs/SLO.md`.

The page opens with the case counts, the request-path diagram, and four short cards. Technical details stays closed. The diagram lists every run id saved under `REPORTS_DIR`. Pick a case and a run, or show a second run under the first; the columns stay aligned. A failed read, a retry chip, an injection badge, and a step marked not run all come from the saved record. The parallel reads and the join are labelled. Replay walks that saved order on the diagram at 0.5x, 1x, 2x, or 5x. It does not call the mock, the app, or a model, and it does not write. The theme control is in the header. Leave it unset to follow the system light or dark setting. Open the exported file the same way: the diagram and the replay are already in that file.

On the Azure app the drafts and traces are on temporary storage and a restart clears them. The app container sets `REPORTS_DIR=/submissions/reports`. Copy the files out with exec. Do not add a public ingress for the page. `az containerapp exec` is interactive in some CLI versions; run the export inside that shell, then copy the HTML back:

```sh
az containerapp exec \
  --name card-disputes \
  --resource-group card-disputes-rg \
  --container app \
  --command "dispute-copilot dashboard --export /submissions/reports/status.html"
```

Then, still in the app container, print the file you need (`cat /submissions/reports/status.html` or `cat /submissions/reports/logs/run.jsonl`). `LOG_STDOUT=1` in front of a copilot command also writes the JSON log lines to that process's stdout. The container command is `sleep infinity`, so the platform log stream for the replica does not include an exec session. The file copy is the path that works today. Do not set `LOG_STDOUT` on the stdio context server.

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

- Flag present, draft clean, routing equal to what the evidence supports: the control worked. Run `agent-review`, then `submit-review`. Tell the reviewer the merchant record carried an instruction.
- Draft contains injected text, a refund promise, or a restricted value: the separation failed. That should be impossible because the narrative is a template and `Redactor.assert_clean` runs before write, so treat it as a code regression.
- Routing differs from the evidence (for example a changed classification): check `rules.classify` and `rules.routing` spans; rules read labeled lines only, so look for a forged labeled line (`Authorization:` etc.) inside a document.

**Immediate mitigation.**

1. Do not approve the review. Reject it so it cannot be created:
   `uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite`
2. Keep classification on the rules and keep the checklist judge off, so untrusted text is not sent to a model: leave `CLASSIFIER_APPROACH` unset (or set it to `1`) and set `FEATURE_LLM_REVIEW=off` in `.env`. Approach 2 or 3 can send a redacted complaint on an eligible case. Leave `JEV_BASE_URL` and `JEV_API_KEY` empty so the ranker is not called.
3. Stop batch runs (`run-all`) until the cause is known; single cases with manual review are fine.
4. If a record was created wrongly, it cannot be deleted through the API. Record the record id from the audit, escalate to the dispute operations lead, and log it in `docs/CHANGES.md`.
5. Add the payload as a regression fixture next to `tests/unit/test_security.py::test_injected_merchant_note_is_treated_as_data`.

## 2. Ambiguous case not reaching a human (CARD-D05)

**Check first.** `grep CARD-D05 submissions/results.json` and the routing in `submissions/drafts/CARD-D05/vN.json`. Expected: classification `ambiguous`, routing `manual_review`, deadline null, `conflicting_evidence` citations from intake and follow-up.

**Confirm.** `uv run --locked python scripts/trace_view.py reports/traces/CARD-D05.jsonl` and look at `documents.followups` (`followups` should be 1) and `rules.classify` (`conflict=true`). `followups=0` means the follow-up document was not listed by the mock or is missing from `data/*/documents/`.

**Mitigate.** Do not approve a non-`manual_review` draft for this case. Reject it, fix the data or the parser, rerun the case (`dispute-copilot run CARD-D05` writes vN+1), then `agent-review` and `submit-review` on that new draft.

## 3. Dependency outage (alerts `tool_exhausted_rate_high`, `breaker_opened`)

**Check first.** `uv run --locked dispute-copilot metrics` and read `tool_by_dependency` and `breaker_opens`. Then `grep -E '"(tool.exhausted|breaker.transition|breaker.skip)"' reports/logs/run.jsonl`.

**Confirm.** `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/v1/cases/CARD-D01/transactions`. A 503 `upstream_timeout` is the dependency. In the mock, a persistent fault lasts until reset.

**Mitigate.** Nothing to do in code: each case still gets its 3 attempts, degrades to amount null and `manual_review`, and cites the outage sentence. With the breaker open, later cases skip the call until the cooldown ends, then one probe case tests it. Tell reviewers more cases will be `manual_review`. When the service is back, rerun the degraded cases so drafts get amounts (`dispute-copilot run CASE` writes a new version). That version needs `agent-review`, `submit-review`, and a new human approval. Breaker settings: `config/rules/breaker.yaml` or `BREAKER_FAILURE_THRESHOLD`, `BREAKER_COOLDOWN_S`, `BREAKER_ENABLED=0`.

## Approve, reject, audit (human only)

```sh
uv run --locked python tools/runtime.py pending --state .state/run.sqlite
uv run --locked python tools/runtime.py approve REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite
uv run --locked python tools/runtime.py reject REVIEW_ID --reviewer YOUR_NAME --state .state/run.sqlite
uv run --locked python tools/runtime.py audit --state .state/run.sqlite > reports/audit.json
```
