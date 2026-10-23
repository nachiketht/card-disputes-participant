# Model choice

Chosen provider: Amazon Bedrock, Claude Haiku 5.5.

| Item | Value |
|---|---|
| Model id | `us.anthropic.claude-haiku-5-5` |
| API | Anthropic Messages on Bedrock Runtime (`https://bedrock-runtime.{region}.amazonaws.com/anthropic/v1/messages`) |
| Credential | `AWS_API_KEY` in `.env`, sent only as `x-api-key`. Also accepted: `AWS_BEARER_TOKEN_BEDROCK`, `LLM_API_KEY` |
| Region | `AWS_REGION`. The key in this workspace is scoped to `us-east-2` |
| Where it runs | Customer-message prose only, after scored fields are fixed. Optional block-only critic stays off unless `FEATURE_LLM_REVIEW=block` |
| Fallback | One hop to templates on timeout, HTTP error, empty completion after one repair, guard rejection, or restricted text |
| CI / tests | `LLM_PROVIDER=null` |
| List price | $0.10 / million input tokens and $0.50 / million output tokens, from the published Haiku 5.5 starting rates (`config/prices.yaml`). Not a measured invoice |

A live call on 2026-10-23 authenticated in `us-east-2` and Bedrock answered: "Access to this model is not available for channel program accounts." Until that account can invoke Haiku 5.5, the drafter records the downgrade and the customer message stays on the template. Scored fields do not change.

Thinking is turned off on the request (`thinking.type=disabled`) so a successful reply is the customer message rather than a reasoning trace.
