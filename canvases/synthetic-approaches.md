# Synthetic bake-off — class, cost, latency

100 gold cases (CARD-S001–S100). Official score is **full eval** (every check must pass). Classification is the bake-off target. Drafter was off.

**Source:** local run 2026-10-28. A1 = `a1-now` (rules). A2 = `a2-now` (rules then gpt-6-luna). A3 = `a3-now` (Jev first; Clef only if Jev is missing or fails; then gpt-6-luna on disagreement). Classifier v3 pinned. Cost from classification-audit usage: gpt-6-luna $0.10 / $0.50 per million in/out; Jev $0.042 / $0.00. Clef did not run. Latency is case-span p50/p95 from `metrics.json`.

A2 is the accuracy winner at 97/100 class and full eval, **$0.0046** list for 100 cases. A3 is 96/100. Jev is priced: 55 ranker calls add $0.0017, so A3 totals **$0.0054**. Both beat the 2000 ms case SLO (A2 0.98, A3 0.99, objective 0.95).

## Headline

| | A1 rules | A2 model | A3 Jev then model |
|---|---:|---:|---:|
| Class / full eval | 59 / 59 | **97 / 97** | 96 / 96 |
| List-price USD (100 cases) | $0 | $0.004632 | $0.005363 |
| Case-span p95 | 67 ms | 1743 ms | 1633 ms |
| SLO (case ≤ 2000 ms) | 100% | 98% | 99% |
| Full-run clock | 2.1 s | 75.6 s | 64.0 s |

### Approach 1 — rules only

No model. Keyword phrases plus duplicate pair check.

- Class 59/100 · full eval 59/100
- $0 · p95 67 ms
- Model calls 0 · full-run clock 2.1 s · SLO 100%
- Routes: manual review 74 · ready 24 · request information 2

### Approach 2 — rules then gpt-6-luna

Model only on eligible cases.

- Class 97/100 · full eval 97/100
- $0.004632 · p95 1743 ms
- Model calls 55 · full-run clock 75.6 s · SLO 98%
- Routes: manual review 45 · ready 34 · request information 21

### Approach 3 — Jev then model

Clef is a fallback if Jev is missing or fails. Unused on this run.

- Class 96/100 · full eval 96/100
- $0.005363 · p95 1633 ms
- Model calls 44 · Jev 55 · full-run clock 64.0 s · SLO 99%
- Routes: manual review 46 · ready 33 · request information 21

## Cost and tokens

List price from provider usage on this run. A1 is $0. Jev output is uncharged.

USD per 1,000 cases: A1 $0; A2 $0.04632 (all gpt-6-luna); A3 $0.05363 ($0.03639 gpt-6-luna + $0.01724 Jev).

Mean input tokens per call: A2 gpt-6-luna 460.5; A3 gpt-6-luna 461.4; A3 Jev 749.7.

| Approach | Model calls | Jev calls | gpt-6-luna USD | Jev USD | USD total | USD / case |
|---|---:|---:|---:|---:|---:|---:|
| A1 rules | 0 | 0 | $0 | $0 | $0 | $0 |
| A2 | 55 | 0 | $0.004632 | $0 | $0.004632 | $0.000046 |
| A3 | 44 | 55 | $0.003639 | $0.001724 | $0.005363 | $0.000054 |

A2 gpt-6-luna: 25,328 in / 4,196 out. A3 gpt-6-luna: 20,301 in / 3,212 out. A3 Jev: 41,231 in / 4,081 out (output uncharged). Source: sum of priced `haiku.cost.usd` and `jev.cost.usd` on `a1-now` / `a2-now` / `a3-now` audits.

## Latency

Case-span is clock time **per case**. A3 median is low because Jev-ambiguous and rules-only cases skip gpt-6-luna. The p95 is still the model tail. SLO: case drafted within 2000 ms, objective 0.95 (5 bad cases allowed).

| Approach | p50 | p95 | Max | Full-run clock (100 cases) | SLO good-case rate | Bad cases (allowed 5) |
|---|---:|---:|---:|---:|---:|---:|
| A1 rules | 11 ms | 67 ms | 164 ms | 2.1 s | 100% | 0 |
| A2 | 1091 ms | 1743 ms | 2561 ms | 75.6 s | 98% | 2 |
| A3 | 115 ms | 1633 ms | 3347 ms | 64.0 s | 99% | 1 |

Source: `metrics.json` `case_latency_ms`.

## Five comparison rates

ADR-41 bake-off rates. Wrong-label is 1 − class match. Auto-routed but wrong is `ready_for_review` with the wrong class. Manual-review is the share routed to an analyst. USD/case is list price. Latency is case-span p95.

| Approach | Wrong-label | Auto-routed but wrong | Manual-review | USD / case | Latency p95 |
|---|---:|---:|---:|---:|---:|
| A1 rules | 41% | 2 / 24 ready | 74% | $0 | 67 ms |
| A2 | 3% | 2 / 34 ready | 45% | $0.000046 | 1743 ms |
| A3 | 4% | 2 / 33 ready | 46% | $0.000054 | 1633 ms |

The two auto-routed-wrong cases on A2 and A3 are CARD-S054 (gold unsupported, predicted unauthorized, routed ready) and CARD-S060 (gold ambiguous, predicted duplicate_charge, routed ready). CARD-S024 is wrong but `manual_review`, so it is not in that rate.

## Score vs gold

Cases passing each eval check out of 100. Source: `reports/synthetic/a1-now`, `a2-now`, `a3-now` `eval.json`.

| Check | A1 | A2 | A3 |
|---|---:|---:|---:|
| classification | 59 | 97 | 96 |
| routing | 69 | 98 | 97 |
| missing_information | 81 | 100 | 100 |
| deadline | 67 | 98 | 97 |
| required_evidence | 99 | 99 | 99 |
| targeted_questions | 81 | 100 | 100 |
| citations_valid | 100 | 100 | 100 |
| facts | 100 | 100 | 100 |
| policy_selection | 100 | 100 | 100 |
| narrative_present | 100 | 100 | 100 |
| no_restricted_values | 100 | 100 | 100 |

## Recall by gold class

Gold mix: unauthorized 23, duplicate 14, service_not_received 19, ambiguous 29, unsupported 15.

| Gold class | A1 | A2 | A3 |
|---|---:|---:|---:|
| unauthorized | 69.6% | 100% | 100% |
| duplicate | 57.1% | 100% | 100% |
| service_not_received | 5.3% | 100% | 94.7% |
| ambiguous | 96.6% | 96.6% | 96.6% |
| unsupported | 40.0% | 86.7% | 86.7% |

## Where the 100 cases went

| Approach | Manual review | Ready for review | Request information |
|---|---:|---:|---:|
| A1 | 74 | 24 | 2 |
| A2 | 45 | 34 | 21 |
| A3 | 46 | 33 | 21 |

## Confusion vs gold

Predicted columns: unauthorized, duplicate, service_not_received, ambiguous, unsupported.

### A1 — 59 correct

| gold \ pred | unauth | dup | snr | amb | unsup |
|---|---:|---:|---:|---:|---:|
| unauthorized 23 | 16 | 0 | 0 | 7 | 0 |
| duplicate 14 | 0 | 8 | 0 | 6 | 0 |
| SNR 19 | 0 | 0 | 1 | 18 | 0 |
| ambiguous 29 | 0 | 1 | 0 | 28 | 0 |
| unsupported 15 | 1 | 0 | 0 | 8 | 6 |

### A2 — 97 correct

| gold \ pred | unauth | dup | snr | amb | unsup |
|---|---:|---:|---:|---:|---:|
| unauthorized 23 | 23 | 0 | 0 | 0 | 0 |
| duplicate 14 | 0 | 14 | 0 | 0 | 0 |
| SNR 19 | 0 | 0 | 19 | 0 | 0 |
| ambiguous 29 | 0 | 1 | 0 | 28 | 0 |
| unsupported 15 | 1 | 0 | 0 | 1 | 13 |

### A3 — 96 correct

| gold \ pred | unauth | dup | snr | amb | unsup |
|---|---:|---:|---:|---:|---:|
| unauthorized 23 | 23 | 0 | 0 | 0 | 0 |
| duplicate 14 | 0 | 14 | 0 | 0 | 0 |
| SNR 19 | 0 | 0 | 18 | 1 | 0 |
| ambiguous 29 | 0 | 1 | 0 | 28 | 0 |
| unsupported 15 | 1 | 0 | 0 | 1 | 13 |

## Classifier path (correct / n)

**A1**

| Path | ok / n |
|---|---:|
| rules_only | 58 / 99 |
| duplicate_unmatched | 1 / 1 |

**A2**

| Path | ok / n |
|---|---:|
| haiku_assigned | 39 / 39 |
| rules_only | 41 / 44 |
| haiku_unclear | 9 / 9 |
| authorization_conflict | 7 / 7 |
| duplicate_unmatched | 1 / 1 |

**A3**

| Path | ok / n |
|---|---:|
| haiku_assigned | 37 / 37 |
| rules_only | 41 / 44 |
| jev_ambiguous | 9 / 10 |
| authorization_conflict | 7 / 7 |
| jev_agreed | 1 / 1 |
| duplicate_unmatched | 1 / 1 |

Pairwise agreement: A1 vs A2 62, A1 vs A3 63, A2 vs A3 99. The only A2/A3 split is CARD-S078 (A2 SNR, A3 ambiguous; gold SNR).

## Remaining class misses

| Case | Gold | A2 | A3 | Why it still misses |
|---|---|---|---|---|
| CARD-S024 | unsupported | ambiguous | ambiguous | ATM paraphrase plus Authorization: denied. Rules dump to ambiguous; model never asked. |
| CARD-S054 | unsupported | unauthorized | unauthorized | Same cash-plus-denied pattern; assigned unauthorized and ready_for_review. |
| CARD-S060 | ambiguous | duplicate_charge | duplicate_charge | Intake looks like a duplicate; follow-up retracts it. Rules only read Authorization labels. |
| CARD-S078 | service_not_received | service_not_received | ambiguous | Cancelled renewal. A2 hits. A3 Jev/model still unclear. |

## Ops extras (same on all three runs)

| | |
|---|---:|
| Untrusted-instruction cases | 1 |
| Circuit-breaker opens | 0 |
| Tool-call exhausted | 5 |
| Tool calls | 200 |

Injection and tool-budget figures are corpus properties, not classifier properties.
