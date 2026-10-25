# Cloud breakdown

Azure Container Apps is the deployment that is running. AWS ECS Fargate is an unbuilt alternative only. This note does not add a second cloud's deployment file.

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
| Judge | Azure-hosted `claude-haiku-4-5` at `<model-endpoint>`. Secret name `llm-api-key` (not in git). It only checks a finished draft. It does not approve. |
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

Case files, drafts, and the mock stay in the replica. The checklist judge, when it is on, calls `<model-endpoint>`. That call can send the checklist facts and the draft narrative. It does not send the key to the mock, and the mock does not call the model. With `FEATURE_LLM_REVIEW=off`, that call is not made. The key is the Container App secret `llm-api-key`.

## AWS ECS Fargate (unbuilt alternative)

Not deployed. The table is the earlier plan only. Cost is unmeasured.

| Item | Plan |
|---|---|
| Region | `us-east-1` |
| Image | One image with the app and `tools/runtime.py`. App and mock are two containers in one Fargate task so they share localhost |
| Registry | ECR |
| Data | One read-only data root. The mock reads `dataset.json`. The app reads `documents/` and `policies/` |
| State | The `--state` SQLite file is mounted into the mock container only |
| Network | Private subnet, no public IP, no ingress |
| Approval | `aws ecs execute-command` into the mock container, `tools/runtime.py approve`, same `--state` path |
| Monthly cost | unmeasured |

## Not in this breakdown

No load balancer in front of the mock. No second cloud running. Approval stays a human command against the mock's state file. The app never approves.
