# Model choice

Chosen provider: Azure AI, Claude Haiku 4.5.

The model is the step-1 judge only. It runs after the deterministic checklist has already passed, and only when `FEATURE_LLM_REVIEW=block`. It returns `pass` or `block` plus a short paragraph for the reviewer. It does not set classification, routing, policy, facts, deadline, or citations. The customer message stays the fixed template, so a model failure cannot change that sentence.

| Item | Value |
|---|---|
| Model id | `claude-haiku-4-5` |
| API | Anthropic Messages, `POST https://pod3-resource.services.ai.azure.com/anthropic/v1/messages` |
| Credential | `LLM_API_KEY` in `.env`, sent as `x-api-key` |
| Resource | `pod3-resource` (`services.ai.azure.com`) |
| Style | `LLM_API_STYLE=anthropic` |
| Where it runs | `dispute-copilot agent-review`, after the checklist passes |
| Fallback | One hop. Timeout, HTTP error, or an unusable reply blocks the review with the stored note "The model reply was not usable." No second model. `FEATURE_LLM_REVIEW=off` skips the call and leaves the checklist result |
| CI / tests | `LLM_PROVIDER=null`. Run the suite with `FEATURE_LLM_REVIEW=off` so a missing key cannot fail the live check |
| Cost per case | List price in `config/rules/cost.yaml`: 1.00 USD per million input tokens, 5.00 USD per million output tokens. That is an estimate of the Azure invoice. No bake-off token counts were recorded |

Structured output is parsed in the app from one JSON object. There is no schema enforcement on the provider call and no repair retry. A reply that is not `pass` or `block` with two to four sentences is stored as a block.
