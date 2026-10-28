# Change request log

| CR | Date | Requested by | What | Class | Brief impact | Decision | Status |
|---|---|---|---|---|---|---|---|
| CR-17 | 2026-10-28 | Pod | Classifier prompt v3 and compact Jev/Clef class text; same signals, no duplicated paragraphs | D | Approach 2 and 3 class text only. Scored rules fields unchanged. Rollback is pinning classifier to 2. | Accepted | Built. |
| CR-16 | 2026-10-28 | Pod | Classifier prompt v2 with few-shot examples; richer Jev/Clef class criteria and state | D | Approach 2 and 3 class text only. Scored rules fields unchanged. Rollback is pinning classifier to 1. | Accepted | Built. |
| CR-01 | 2026-10-21 | Pod | Thin MCP wrapper, stdio only, no network port, no approve or create tools | S | Later callers use the same service. The wrapper cannot write. | Accepted | Not built. Days 6–8. |
| CR-02 | 2026-10-21 | Pod | Second review, app-enforced, via one pure check before create | S | Exactly 100000 XCR needs one approval. A higher amount, a null amount, or another currency needs two. No create when the check fails. | Accepted | Not built. The current create path posts one approved review. |
| CR-03 | 2026-10-21 | Pod | Ask once, then manual review | S | The first scored draft stays a request for information. A rerun writes the next draft version. | Accepted | Not built. First drafts of those cases stay a request for information. The rerun command is later. |
| CR-04 | 2026-10-21 | Pod | Stop tracking the mock database. Runs use a separate state file. | D | Do not commit a database that contains approvals. | Required | Done. `.state/` is ignored. The tracked database is removed from the index, and the file stays on disk. |
| CR-05 | 2026-10-21 | Pod | Alias registry: every alias has a reason | D | Unseen wording uses a reviewed alias or is not guessed. | Accepted | Not built. This checkpoint matches exact labels only. A missing or unfamiliar label is not guessed. |
| CR-15 | 2026-10-25 | Pod | Week 10 hardening: retry backoff with capped jitter, request timeouts retried, cross-case circuit breaker (supersedes ADR-24), per-case traces, JSON logs, metrics and alerts, runbook | S | No scored output changes with default settings. D07/D10 attempt counts unchanged. | Accepted; ADR-30 | Built on `mvp-v1`. |
