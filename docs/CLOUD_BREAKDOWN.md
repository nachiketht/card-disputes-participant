# Cloud breakdown

Prepared for the Monday 2026-10-26 review. Cloud deployment is allowed. Nothing in this document has been deployed, and no cloud bill has been measured. Every cost cell is null.

The assessed path stays local until a facilitator confirms in writing that the mock may run inside a cloud task. Local Docker is the baseline. It is not built yet. The mock that runs today is a process on `127.0.0.1:8765`.

One cloud is in scope. This account already calls Bedrock in `us-east-1`, so the cloud to deploy first is AWS ECS Fargate in that region. Azure Container Apps is the written alternative if AWS access stops. The mock binds `127.0.0.1` inside the task and has no ingress and no public address.

## What leaves the machine

The case files, the draft, and the mock stay in the task. The only outbound model call is the step-1 judge to Bedrock Converse in `us-east-1` when `FEATURE_LLM_REVIEW=block`. That call sends the checklist facts and the draft narrative. It does not send the API key to the mock, and the mock never calls Bedrock. With `FEATURE_LLM_REVIEW=off`, nothing leaves the task for a model.

## AWS ECS Fargate (deploy first)

| Item | Plan |
|---|---|
| Region | `us-east-1`, same region as the Nova judge |
| Image | One image with the app and `tools/runtime.py`. App and mock are two containers in one Fargate task so they share localhost |
| Registry | ECR |
| Data | One read-only data root mounted at the same path in both containers. The mock reads `dataset.json`. The app reads `documents/` and `policies/` |
| State | The `--state` SQLite file is mounted into the mock container only. The app does not mount it |
| Network | Private subnet, no public IP, no ingress. NAT or VPC endpoints for ECR, logs, and `ssmmessages` |
| Roles | Execution role pulls the image, writes logs, and reads secrets. Task role is for runtime, plus `ssmmessages:CreateControlChannel`, `CreateDataChannel`, `OpenControlChannel`, and `OpenDataChannel` |
| Exec | The service is created with `enableExecuteCommand: true` |
| Secrets | `AWS_API_KEY` in Secrets Manager, injected as an environment variable. It is not baked into the image |
| Approval | `aws ecs execute-command --container mock ... -- python tools/runtime.py approve <review_id> --reviewer <name> --state <same path>` |
| Monthly cost | null |

## Azure Container Apps (written alternative)

| Item | Plan |
|---|---|
| Image | Same image. One Container App with two containers sharing localhost |
| Registry | ACR |
| Data | Same read-only mount in both containers |
| State | `--state` mounted into the mock container only |
| Network | Internal ingress or none. The mock is not published |
| Identity | Managed identity for ACR pull and Key Vault |
| Secrets | `AWS_API_KEY` in Key Vault if the judge stays on Bedrock |
| Approval | `az containerapp exec --container mock ... --command "python tools/runtime.py approve <review_id> --reviewer <name> --state <same path>"` |
| Monthly cost | null |

## Not in this breakdown

No load balancer in front of the mock. No second cloud running at the same time. No circuit breaker. Approval stays a human command against the mock's state file. The app never approves.
