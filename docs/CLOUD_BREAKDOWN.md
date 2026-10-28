# Cloud breakdown

Azure Container Apps is the deployment that is running. This note does not add a second cloud's deployment file.

This session could not sign in to `<subscription>`, so it did not query the resource group or the bill. Every cost cell is still unmeasured.

The mock binds `127.0.0.1` inside the app. There is no public ingress and no public address. The app never approves.

## What is running

| Item | Value supplied for the running app |
|---|---|
| Region | West US 3 (`westus3`) |
| Resource group | `card-disputes-rg` |
| Registry | `pod3disputes` |
| Image | `card-disputes:promptfix` (code only; case files are not in the image) |
| Storage account | `pod3disputesdata` |
| Mock share | `mock-data`, only `dataset.json`, mounted at `/data` for the mock |
| App share | `app-data`, `documents/` and `policies/`, mounted at `/data` for the copilot |
| Environment | `card-disputes-env` |
| App | `card-disputes`, one replica, no public ingress |
| Mock container | `mock`, port 8765, share `mock-data`, ledger in temporary storage at `/state` |
| App container | `app`, share `app-data`, drafts in temporary storage at `/submissions`, calls `http://127.0.0.1:8765` |
| Model | Model id from `LLM_MODEL` in `.env` (currently `claude-haiku-5-5`). Secret name `llm-api-key` (not in git). The checklist judge checks a finished draft when it is on. Approaches 2 and 3 may call the same model on an eligible case. It does not approve. The host is not written in this file. `claude-haiku-5-5` has no price row in `config/rules/cost.yaml`. |
| Ranker | Optional. `JEV_BASE_URL`, `JEV_API_KEY`, `JEV_MODEL`. Empty url or key skips Jev. Clef-flash fallback uses `CLEF_API_KEY`, `CLEF_ACCOUNT_ID`, `CLEF_MODEL`. Empty key or account skips Clef. Not set on the container template. Jev cost is unpriced. |
| Monthly cost | unmeasured |

The app container cannot see `dataset.json`. That split is intentional. A restart clears the ledger and the drafts. Files on the shares stay.

The two containers share localhost. Approval is a human command inside the mock container, against the same state file:

```sh
az containerapp exec \
  --name card-disputes \
  --resource-group card-disputes-rg \
  --container mock \
  --command "python tools/runtime.py approve <review_id> --reviewer <name> --state /state/run.sqlite"
```

## What leaves the app

Case files, drafts, and the mock stay in the replica. The checklist judge, when it is on, calls the configured model host. That call can send the checklist facts and the draft narrative. Approach 2 and approach 3, when selected and the case is eligible, can send a redacted complaint to the model named by `LLM_MODEL`. Approach 3 can also send that redacted complaint to Jev when `JEV_BASE_URL` and `JEV_API_KEY` are set, and to Clef-flash when `CLEF_API_KEY` and `CLEF_ACCOUNT_ID` are set and Jev is missing or the Jev call fails. Classification audits do not store the complaint. Jev cost is unpriced. Those calls do not send the key to the mock, and the mock does not call the model. With `FEATURE_LLM_REVIEW=off` and approach 1 (the default), those model calls are not made. The key is the Container App secret `llm-api-key`. The host is not written in this file.

## Not in this breakdown

No load balancer in front of the mock. No second cloud running. Approval stays a human command against the mock's state file. The app never approves.
