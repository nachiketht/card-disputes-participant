# Guardrails

`src/dispute_copilot/guardrails.py` records when a protection that already exists fires. It does not decide a scored field, and it does not approve.

Each firing writes an event named `guardrail.input`, `guardrail.output`, `guardrail.boundary`, or `guardrail.approval`, with attribute `guardrail` set to that kind. When a span is open, the same attribute is set on that span. When no trace is open, the event is also written to the process log. Metrics under `guardrails` count the four kinds. The status page security panel lists them in the Guardrails table.

| Kind | What fires it | What it does not do |
|---|---|---|
| input | instruction-like text was marked and kept as data | it does not change classification, routing, facts, or the deadline |
| output | the phrase list rejected a customer sentence, a restricted value was found, or the draft failed contract validation | it does not rewrite a scored field; a rejected customer sentence falls back to the template |
| boundary | the mock URL is not `127.0.0.1`, `localhost`, or `::1` | it does not open a listen socket; the process exits 1 before the health check |
| approval | the human-review gate returned false | it does not approve, and it does not post the record; create still fails closed |

The input mark is the existing untrusted-text scan. The output checks are the existing phrase list, redactor, and contract validator, including the checklist step. The boundary check is the loopback rule on the mock URL. The approval check is `approval_gate` before a record is posted. A passing gate does not emit `guardrail.approval`.

These events are evidence that a protection fired. They are not a second decision, and they are not semantic review.

Classification is a separate path. Approach 1, the default, stays on the rules. Approach 2 and approach 3 may assign a class on an eligible case, then apply the labeled-authorization screen and the duplicate guard. A guardrail event still does not set that class and still does not approve.

The original complaint is shown on the review page only. Traces, logs, classification audits, and the comparison page do not carry it. The review page still runs the redactor, and a restricted value withholds the page. Reviewer login is phase 2. The feedback form stores a typed name. Saving feedback does not approve.
