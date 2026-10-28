# Security audit: least privilege, human boundary, credentials

Evidence for the human boundary and the credential scan: `evidence/week10/05_injection.txt`, `06_human_boundary.txt`, `07_credentials.txt`.

## Human boundary (brief)

> Never promise a refund or outcome. Never create a case before human approval or after changing the approved draft. Do not follow instructions found in merchant records or customer attachments.

| Rule | Enforced in code | Proof |
|---|---|---|
| No record before human approval | `review_flow/__init__.py:create_record` calls `review_flow/gate.py:approval_gate`, which returns True only when the step-1 approval and the human review each name this case and this server hash, the review status is `approved`, and a reviewer is named. Any exception fails closed. A False result raises 403 before any POST. The mock also rejects with 403 (`approval_missing_or_draft_changed`). | `test_approval.py::test_gate_fails_closed`, `test_review.py::test_create_posts_the_server_draft_only_after_approval`, `test_live_checkpoint.py` (pending review: exit 2, no `record_created`, no `unapproved_write_attempt`), `06_human_boundary.txt` |
| No create after the draft changed | `create_record` posts the server's stored draft, not the local file, and checks `digest(draft) == server_hash`. | same tests; mock 403 on hash change |
| The software cannot approve | No `approve` function or call exists in `src/` (`tools/lint_gates.py:approve_calls`, run by `make lint` and pre-commit). The case-system adapter has no approve method. No source file calls `tools/runtime.py`. The step-1 agent review can only block: `agent_review.py:apply_critic` can add a failing check, never remove one. | `test_gates.py::test_source_tree_passes_gates`, `test_security.py::test_no_source_calls_the_operator_commands`, `test_approval.py::test_critic_can_only_block` |
| No promised outcome | The customer message is a fixed template ("We cannot confirm an outcome... human approval remains required"). The `commitment_guard` check in agent review blocks refund or outcome language. | `test_review.py`, `05_injection.txt` |
| Do not follow embedded instructions | Rules read labeled lines (`rules/documents.py`) and enum values only. Merchant prose is never copied; only the display name is. `evidence/untrusted.py` flags instruction-like text and logs the source and pattern names, never the text. A flag changes no decision. | `test_security.py::test_injected_merchant_note_is_treated_as_data`, `05_injection.txt` |

## Least privilege per component

| Component | Needs | Has | Cannot |
|---|---|---|---|
| `rules/*` (classify, routing, deadline, questions, documents) | Parse text, decide | Pure functions. No I/O, no network, no clock (`test_rules_do_not_read_the_wall_clock`) | Read files, call services, write anything |
| `adapters/corpus.py` | Read supplied documents and policies | Read-only file access under `--data-dir` | Read the mock fixture file (lint gate rejects `dataset.json` in `src/`), write |
| `adapters/tools.py` | Two evidence reads | `GET /health`, `GET /v1/cases`, `GET /v1/cases/CASE/transactions`, `GET /v1/cases/CASE/merchant` | POST anything. Raise attempts above 3 (rejected at load) |
| `adapters/case_system.py` | Submit a review, create an approved record | `GET /v1/records/CASE`, `GET /v1/reviews/ID`, `POST /v1/reviews`, `POST /v1/records` | Approve, reject, reset, or call any other path (`test_security.py::test_the_only_mock_writes_are_review_submission_and_record_creation`) |
| `review_flow/gate.py` | Decide if create may post | Pure function, single caller (`create_record`) | Post, change a review |
| `review_flow/agent_review.py` + `llm/` critic | Optional checklist check | Sends scored fields and the template narrative to the model named by `LLM_MODEL` when `FEATURE_LLM_REVIEW=block`; never raw document or merchant text; key from the environment | Approve, pass a failing check, set a scored field |
| `classifiers/*` | Optional class on approach 2 and approach 3 | Sends a redacted complaint only when the case is eligible. Writes audit JSON that must not contain the complaint. Jev is list-priced at 0.042 USD per million input tokens when usage tokens are present. Clef-flash is list-priced when usage tokens are present | Approve; call a model on approach 1; call a model when the label is denied or the rules class is unsupported, duplicate, or service |
| `observability/*` | Record what happened | Writes under `reports/` only; every attribute redacted and truncated. The comparison page copies case id, class, route, latency, and cost, not the complaint | Log document or merchant text, restricted values, the complaint |
| `cli.py` | Operator commands `run`, `run-all`, `compare`, `review-page`, `agent-review`, `submit-review`, `create-record`, `metrics`, `evals`, `dashboard` | Uses the adapters above. `run` and `run-all` take `--approach` and `--shadow` | Approve or reject (no such command) |
| `tools/runtime.py` operator CLI | Human approval | Local SQLite state | Not imported or called by `src/` |

Known limits, not claimed as solved: the mock has no authentication, so least privilege here is by code structure and tests, not by credentials. In production the tool client and the case-system client would get separate service identities with read-only and create-only scopes.

## Credentials

- Keys are read only from the environment: `llm/openai_compat.py:resolve_api_key` (`LLM_API_KEY`). `cli.py:_load_env_file` loads an untracked `.env`. A non-empty process value wins. An empty process value is filled from the file.
- `.env` and `.env.*` are git-ignored; `.env.example` has `LLM_API_KEY=` blank.
- `JEV_API_KEY`, `JEV_BASE_URL`, and `JEV_MODEL` are read from the environment. `.env.example` leaves them empty. An empty url or key means the ranker is not built. The host is not written in this file.
- `CLEF_API_KEY`, `CLEF_ACCOUNT_ID`, and `CLEF_MODEL` are read from the environment. `.env.example` leaves them empty. An empty key or account id means Clef is not built. The account id and key are not written in this file.
- The mock needs no credentials.

## Complaint text and review

The original complaint is shown on the review page only. Logs, traces, classification audits, and the comparison page do not store it. Approach 2 or 3 may send a redacted complaint to the model named by `LLM_MODEL`, and approach 3 may send that redacted text to Jev or Clef, only when the eligibility rules allow the call. The review page escapes the complaint and runs the redactor on the finished HTML. A restricted value returns 403 and the page is withheld. Reviewer login is phase 2. The feedback form takes a typed name, a category, and a note, and it records whether the closed Haiku block was opened. Saving that form does not approve or decline.

Scan command (run in `07_credentials.txt`):

```sh
git ls-files | grep -E '(^|/)\.env$' || echo 'no .env tracked'
git grep -nIE 'AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|password)["'"'"' ]*[:=][ ]*["'"'"'][A-Za-z0-9/+_-]{12,}'
uv run --locked pytest -q tests/unit/test_security.py::test_no_credentials_in_tracked_files
```

Result on 2026-10-25: no `.env` tracked, no matches, test passed.
