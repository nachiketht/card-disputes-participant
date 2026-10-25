# Model choice

Chosen provider: Amazon Bedrock, Amazon Nova 2 Lite.

The model is the step-1 judge only. It runs after the deterministic checklist has already passed, and only when `FEATURE_LLM_REVIEW=block`. It returns `pass` or `block` plus a short paragraph for the reviewer. It does not set classification, routing, policy, facts, deadline, or citations. The customer message stays the fixed template, so a model failure cannot change that sentence.

| Item | Value |
|---|---|
| Model id | `us.amazon.nova-2-lite-v1:0` (US inference profile) |
| API | Bedrock Converse, `POST https://bedrock-runtime.us-east-1.amazonaws.com/model/us.amazon.nova-2-lite-v1%3A0/converse` |
| Credential | `AWS_API_KEY` in `.env`, sent as `Authorization: Bearer`. Also accepted: `AWS_BEARER_TOKEN_BEDROCK`, `LLM_API_KEY` |
| Region | `us-east-1` |
| Style | `LLM_API_STYLE=converse` |
| Where it runs | `dispute-copilot agent-review`, after the checklist passes |
| Fallback | One hop. Timeout, HTTP error, or an unusable reply blocks the review with the stored note "The model reply was not usable." No second model. `FEATURE_LLM_REVIEW=off` skips the call and leaves the checklist result |
| CI / tests | `LLM_PROVIDER=null`. Run the suite with `FEATURE_LLM_REVIEW=off` so a missing key cannot fail the live check |
| Cost per case | null. No bake-off token counts were recorded. `config/prices.yaml` is still the Haiku table and does not apply to this model |

## What was tried

| Candidate | Result |
|---|---|
| Claude Haiku 5.5, `us.anthropic.claude-haiku-5-5`, Anthropic Messages | Listed in the model catalog. On 2026-10-23 Bedrock returned: "Access to this model is not available for channel program accounts." The same refusal appeared for a key in `us-east-1`. Not usable on this account |
| Amazon Nova 2 Lite, Converse, `us-east-1` | Playground answered. A Converse call on 2026-10-24 succeeded. `agent-review` for CARD-D01 and for a case outside the development set both returned `verdict: pass` with a usable paragraph |
| Nova Chat Completions on `bedrock-runtime` and on Mantle | Rejected. The working path is Converse with the `us.` profile, not the bare foundation-model id |

Latency and dollars per case were not measured. A short Converse probe on 2026-10-24 reported `latencyMs` 616, `inputTokens` 62, and `outputTokens` 41. That probe was not a dispute case, so it is not a per-case figure.

Structured output is parsed in the app from one JSON object. There is no schema enforcement on the provider call and no repair retry. A reply that is not `pass` or `block` with two to four sentences is stored as a block.
