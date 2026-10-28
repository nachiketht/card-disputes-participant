# Evals

`dispute-copilot evals` and `make evals` print one scorecard and write `reports/evals.json`. The judge stays off (`LLM_PROVIDER=null`, `FEATURE_LLM_REVIEW=off`). Classification stays on approach 1 unless `CLASSIFIER_APPROACH` is set. The eval commands do not set it, so the scorecard is the rules draft and does not call the configured model or the ranker. It does not score approach 2, approach 3, shadow audits, or the comparison page. Development duplicate cases already contain a matching merchant, amount, and currency pair, so those gold classes stay `duplicate_charge` under the duplicate guard. A category under its minimum exits 1. The command does not approve a case and does not post a record. Reviewer login is phase 2. The wrong-label rate and the auto-routed-but-wrong rate from saved feedback are on the comparison page, not in this scorecard.

`make evals` starts the mock on `127.0.0.1` with a temporary state file, drafts the development cases, then runs the scorecard. The written report is generated. It is not committed.

Thresholds live in `config/rules/evals.yaml`. A category with a minimum fails when its score is missing or below that minimum. Cost has no numeric cap until `cost.maximum_usd` is set from a measured figure, so an unpriced run does not fail the cost row.

## Sets

Versioned sets are `evals/sets/<id>/v1.json`.

| Set | What it runs |
|---|---|
| `dev` | `tools/evaluate.py` on the development gold file |
| `adversarial` | the synthetic cases listed in that set |
| `combined` | the duplicate-plus-another-issue case, also listed under adversarial |

A case listed in more than one synthetic set is counted once per category. Each set still prints its own pass count.

## Categories

| Category | What the score is | What it does not cover |
|---|---|---|
| workflow correctness | development cases passed by `tools/evaluate.py`, then the lower of that rate and the synthetic cases tagged for this category | a hidden corpus, narrative quality |
| deadlines | official `deadline` check rate, lowered by any tagged synthetic miss | a clock outside the supplied policy text |
| routing | official `routing` check rate, lowered by any tagged synthetic miss | whether a person would pick a different route |
| grounding | citation support, below | semantic support |
| escalation | development cases whose gold route includes manual review or a request for information, and whose written route is inside that gold list, lowered by tagged synthetic misses | a second reviewer |
| injection resistance | synthetic cases tagged for this category | a novel instruction pattern that the scanner does not name |
| pii and leak safety | official `no_restricted_values` rate, lowered by tagged synthetic misses | every phone or account shape outside the redaction patterns |
| no-commitment language | share of development cases whose narrative is non-empty and is not rejected by the phrase list | a promise phrased outside that list |
| idempotency and approval gating | synthetic case where a draft changed after approval is not created | a live reviewer session |
| latency | good-case rate from the traces of this run (`duration_ms` at or under the latency target) | a hosted metrics product; a missing trace fails the row |
| cost | no model calls, or calls with no price, pass while `maximum_usd` is unset; a priced total fails only when it exceeds a configured cap. A default eval does not call the configured model. Jev is list-priced at 0.042 USD per million input tokens when usage tokens are present; a missing usage count is not treated as 0 USD | a measured token or compute bill; list prices in `config/rules/cost.yaml` are USD per million tokens (`claude-haiku-4-5` is 1.00 in and 5.00 out; `claude-haiku-5-5` and `gpt-6-luna` are 0.10 in and 0.50 out from list; `gpt-6-luna` is Azure Global Standard short context; `jev-latest` is 0.042 in and 0.00 out) |

## Citation support

A citation counts as supported only when the source id exists and the quote appears in that source text. The scorecard prints this limit with the count:

> This check does not judge semantic support. It only checks that the source id exists and the quote appears in that source text.

Markdown source ids are read from the policy and document files. Payload source ids (transaction and merchant reads) are read from the texts `tools/source_index.py` prints for that data directory. Code under `src/` does not open the fixture file. File text wins when an id is in the files. A missing index fails those payload ids closed.

This is separate from the official `citations_valid` check. Grounding uses this substring count only.
