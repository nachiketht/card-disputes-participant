# Limitations

Checked on `mvp-v1` before this note was added. `make lint` reported 46 source files. `pytest` reported 122 passed. `tools/evaluate.py` against a fresh `run-all` on the development corpus, with the mock on `127.0.0.1` and a temporary state file, reported `passed` true, 12 of 12. The committed `reports/dev.json` matched that evaluate output. Nothing below is a claim about a hidden corpus or a live bank.

## What the program will not do

- It does not approve. Approval is the separate `approve` command in `tools/runtime.py`. There is no approve route on the mock.
- Code under `src/` and `scripts/` does not read `dataset.json`. The lint gate fails a `src/` file that names that file. `scripts/` was searched and does not name it.
- Scored fields (classification, routing, facts, deadline, missing information, policy ids) come from the rules. A model is off unless `LLM_PROVIDER` and `FEATURE_LLM_REVIEW` are set. In `block` mode the critic can only fail the checklist. It is not given the scored fields to rewrite. This session did not re-run `FEATURE_LLM_REVIEW=block`; that refusal is what the checklist code does.
- The mock listen address in `create_server` is `127.0.0.1`. Health was requested on that address. `ss` was not installed here, so a second look at the socket table was not taken.

## Known gaps

- A customer reference or other restricted value inside a cited quote is masked, and the case continues, when readable words remain. If the quote would be only the mask, that quote is dropped and routing becomes manual review. Classification and the transaction facts stay as the rules computed them. A masked quote is no longer an exact excerpt, so `citations_valid` would fail for that case. The twelve development complaints keep the reference on a labeled line, which is not cited, so the 12 of 12 result does not exercise masking.
- Card detection keeps 13–19 digits that pass a Luhn check, with or without single spaces or dashes. A value that is only a `span_id`, `trace_id`, `parent_id`, or `run_id` hex token is left as-is, including a 16-digit token that would pass Luhn, so an identifier is not rewritten. IBAN-shaped text is masked when the checksum matches. Account numbers are masked when a label (`account`, `acct`, or `a/c`) sits in front of 8–17 digits. Bare digit strings are not treated as accounts, because that would collide with other ids. Email addresses use a conventional form. Phone detection covers a leading `+` and 8–15 digits, one spaced international form, and a 3-3-4 national form. Other national layouts can pass through.
- Instruction-like text in a complaint, attachment, follow-up, or merchant payload is not allowed to change a scored field. The trace records `injection_detected` with the case id, source id, and pattern category. The raw sentence is not written to the log or the trace. The checklist note names the categories. A pass of that checklist still does not approve.
- Editing the local draft file after approval does not make `create-record` fail. The create step posts the draft stored on the mock at submit time, so the local edit is not the body that is saved. Editing that stored draft after approval, while leaving its hash unchanged, makes `create-record` exit 2 with `record was not created (status 403)`, and no record is written. The gate was not changed. A stricter rule that also fails the local-file edit is not implemented.
- `good_case_rate` counts drafted case spans whose `duration_ms` is at or under `latency_target_ms` (2000) divided by all case spans. It does not read the route. `config/rules/slo.yaml` uses that same sentence. A fast draft whose route is manual review still counts as good. See `docs/SLO.md`.
- `reports/dev.json` scores the development gold file only. It does not score narrative quality, approval, or a second corpus.

## Not verified here

- Image build, compose, and any hosted container run. Those were not executed in this session.
- A socket listing that the mock refused non-loopback binds, beyond the fixed address in code and the health call to `127.0.0.1`.
- The critic with a live model key. No key was used. Default runs kept the model off.
- Phone and account shapes outside the patterns above. No extra examples were supplied.
- Data other than `data/dev`.

## What would come next

Keep the rules as the only writer of scored fields. A later model may write the customer-message paragraph or an advisory note, still off by default, and a bad or empty reply may only fail the checklist. Restricted values stay masked before they are logged, traced, or placed in a prompt. Approval stays the human command in `tools/runtime.py`.

Worth doing after that, without loosening those gates: decide whether a local draft edit after approval should fail `create-record`; add phone or account patterns only from examples the team provides; run the image build in an environment that has a container engine.
