# Model choice

The model is Claude Haiku 4.5, id `claude-haiku-4-5`. It is Azure-hosted. The same id is the checklist judge and the classifier on approach 2 and approach 3. It does not approve a case, and it does not post a record.

The checklist judge runs after the deterministic checklist has already passed, and only when `FEATURE_LLM_REVIEW=block`. It returns `pass` or `block` plus a short paragraph for the reviewer. It does not set classification, routing, policy, facts, deadline, or citations. The customer message stays the fixed template, so a judge failure cannot change that sentence.

Approaches 2 and 3 call that same model only when the rules leave the case open. A labeled denial, a conflict, and a rules class of unsupported, duplicate, or service are not sent. Ambiguous with no conflict may be sent. Unauthorized from complaint wording may be sent only when authorization is not supplied. The complaint is redacted before the call. A proposed class is kept only when the labeled authorization allows it: unauthorized only when the label is not supplied or denied; duplicate and service only when the label is confirmed or not supplied. A confirmed label on an unauthorized proposal does not stand, and the case goes to manual review. A denied label never reaches the model. Any other class with that label is a hard failure. Evidence is accepted only when it is exact complaint text after whitespace is collapsed. A quote that is not in the complaint is rejected, and approach 2 then keeps the rules class.

Approach 3 asks the ranker first. The ranker is not Haiku 4.5. Its model name defaults to `jev-latest`. The threshold in `config/rules/classifier.yaml` is a placeholder, `0.70`. A tie, a probability under that threshold, or an ambiguous option does not assign a class. When the ranker returns a class and it matches the rules, Haiku 4.5 is not called. Haiku 4.5 is called when those answers differ, and it may also be called when the ranker is not configured or its call fails. Jev cost is unpriced (`status` `unpriced`, no USD figure). An empty `JEV_BASE_URL` or `JEV_API_KEY` skips the ranker. A failed ranker call does not assign a class either. The rules class stays. Manual review remains when the ranker says ambiguous, or when Haiku's class is rejected.

| Item | Value |
|---|---|
| Model id | `claude-haiku-4-5` |
| API style | Anthropic Messages. `LLM_API_STYLE=anthropic`. The host is `AZURE_BASE_URL` in `.env`. It is not written in this file. A model call fails when that variable is empty. |
| Credential | `LLM_API_KEY` in `.env`, sent as `x-api-key`. Empty means no Haiku call. |
| Ranker | `JEV_BASE_URL`, `JEV_API_KEY`, `JEV_MODEL`. Leave the url and the key empty until a host is chosen. |
| Checklist judge | `dispute-copilot agent-review`, after the checklist passes, when `FEATURE_LLM_REVIEW=block` |
| Classifier | Approach 2, and approach 3 on an eligible case when the ranker is absent, failed, or disagrees with the rules |
| Judge fallback | One hop. Timeout, HTTP error, or an unusable reply blocks the review with the stored note "The model reply was not usable." No second model. `FEATURE_LLM_REVIEW=off` skips the call and leaves the checklist result |
| CI / tests | `LLM_PROVIDER=null` and approach 1. Run the suite with `FEATURE_LLM_REVIEW=off`. A missing key cannot fail the live check, and the jobs do not set `CLASSIFIER_APPROACH` |
| Haiku cost | List price in `config/rules/cost.yaml`: 1.00 USD per million input tokens, 5.00 USD per million output tokens. That is an estimate. No measured token counts are recorded here |
| Jev cost | Unpriced. A stored number is not treated as a price |

Structured output is parsed in the app from one JSON object. There is no schema enforcement on the provider call and no repair retry. A judge reply that is not `pass` or `block` with two to four sentences is stored as a block. A classifier reply is one JSON object with `class`, `reason`, and `evidence`.
