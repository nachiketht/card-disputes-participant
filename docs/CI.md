# CI

Workflow file: `.github/workflows/ci.yml`. It runs on every push and every pull request. The six local jobs use no repository secrets. `LLM_PROVIDER=null` and `FEATURE_LLM_REVIEW=off`, so the checklist judge does not run. Classification stays on approach 1 unless `CLASSIFIER_APPROACH` is set. The workflow does not set it, and a null provider does not build a Haiku client, so these jobs do not call the configured model or the ranker. Package install is the only network use on those jobs. A push does not build a new revision and does not alter the running container app.

Job `azure` is the one exception. It runs only on `push`, after the six local jobs pass, and it signs in with GitHub OIDC when the repository secrets below are set. It does not run on `pull_request`. A fork, and a repository that has not set the sign-in, skips that job with a notice and exit 0.

Required check names, in workflow order, for branch protection:

| Job | What a failure means |
|---|---|
| `lint` | `uv sync --locked --extra mcp`, package validation, or `tools/lint_gates.py` failed |
| `test` | the full pytest suite failed |
| `evaluate` | the loopback mock, `run-all`, or `tools/evaluate.py` failed, `reports/dev.json` differs from that report, or the scorecard missed a minimum |
| `security` | adversarial, leak, approval, or stdio-server tests failed, or the server registers an approve, reject, or reset call |
| `secrets` | a tracked file matches a credential pattern, or `.env` is tracked |
| `image` | `docker build` failed, or one case did not draft through the image's loopback mock |
| `azure` | the live container was unreachable, a development case failed, the checklist was not all passed, or `tools/evaluate.py` was not a full pass |

Do not mark `azure` required on a pull request. That job does not start for `pull_request`, so a required check would stay waiting. Require it on push once the sign-in secrets exist. Until those secrets exist the job skips and exits 0, so a required check would stay green without contacting the container.

`evaluate` starts `tools/runtime.py serve` on `127.0.0.1` with a temporary state file. It does not publish that port. A fresh `run-all` must pass every development case. `tools/check_fresh_report.py` then compares `reports/dev.json` with that report. After the mock is stopped, `dispute-copilot evals` scores that same submission. A category under its minimum in `config/rules/evals.yaml` fails the job. The judge stays off. The job uploads the traces, `metrics.json`, the fresh evaluation, the scorecard, and the status-page HTML as the artifact `traces-metrics-dashboard`.

`image` runs `tools/image_smoke.sh`. That script builds `card-disputes:ci` and does not push. The mock process inside the image is checked on `127.0.0.1:8765`. A second container shares that network namespace and drafts `CARD-D01`. No host port is published.

`secrets` runs `tools/scan_secrets.py` with the system Python. It does not install the package.

Each local job uses concurrency group `ci-<ref>-<job>` and cancels an older run of that same job on the same ref. Job `azure` uses group `azure-live-container` with cancel turned off. Every branch shares that group, because there is one replica. A second push waits. A local job that is cancelled does not start `azure`. An `azure` run that has already started is left to finish.

Local equivalents:

```sh
uv sync --locked --extra mcp
uv run --locked --extra mcp python tools/lint_gates.py
uv run --locked --extra mcp pytest
python3 tools/scan_secrets.py
uv run --locked --extra mcp python tools/check_mcp_names.py
uv run --locked python tools/run_evals.py
python3 tools/azure_live.py plan
```

`plan` prints the container commands and does not sign in. The development score still needs a mock on `127.0.0.1` and a temporary state file, then `run-all` and `tools/evaluate.py`. The pre-commit hook (`.githooks/pre-commit`) runs the staged lint gate and the secret scan. It does not start the mock or build the image.

## Live container on every push

`azure` checks the revision that is already running. It does not call `az containerapp update`, `docker push`, or `az acr login`. A green `azure` job means that revision passed. It is not proof that the commit just pushed is the image inside the container. The tag recorded in `docs/AZURE_DEPLOY.md` is `card-disputes:promptfix`. If that image rejects `--label` or a command this job sends, the job fails.

Sign-in is `azure/login@v2` with `client-id`, `tenant-id`, and `subscription-id`. The workflow reads each one from the repository secret of that name, and from the repository variable of the same name when the secret is empty. Store them as secrets so the log masks the values. There is no password and no long-lived key in the workflow. Do not put the Container App secret `llm-api-key` in GitHub.

The gate step writes `run=skip` or `run=yes` and prints the notice on stderr. It skips when `github.event.repository.fork` is true, or when `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, or `AZURE_SUBSCRIPTION_ID` is unset in secrets and in variables. The notice names those three secret names and says the live container was not contacted. A fork push says the run is from a fork. The step does not print the id values.

When the gate says `run=yes`, `tools/azure_live.py` drives `card-disputes` in resource group `card-disputes-rg` with `az containerapp exec`. No public ingress is added and no new endpoint is created. The runner wraps the call as `script --return -q -c '<az containerapp exec ...>' /dev/null`, because that command expects a terminal. The log line is `exec container=app` or `exec container=mock`. The command body is not printed. A line that contains `llm-api-key` or `llm_api_key` is replaced with `[line omitted]`. A line longer than 200 characters is omitted the same way. The job does not run `az containerapp secret show`.

Inside the containers the order is:

1. In `app`, GET `http://127.0.0.1:8765/health`. That path does not write the calls table. Then prove `/data/dataset.json` is absent. `fixture is visible` fails the job. `fixture is not visible` is the pass. `mock did not answer` fails the job.
2. In `mock`, start a second `tools/runtime.py serve` on `127.0.0.1:8766` with state `/tmp/ci<run id>.sqlite`. The data directory is the mock share `/data`, which is the only place the fixture is mounted. The process is detached with a double fork so one console session can exit. The parent waits for health on 8766.
3. In `app`, `dispute-copilot run-all` and then `agent-review` for each case, against `http://127.0.0.1:8766`, with `--label azure`. Drafts and reports go under `/tmp/ci<run id>/`. The child environment sets `LLM_PROVIDER=null`, `FEATURE_LLM_REVIEW=off`, and `RUN_LABEL=azure`, and removes `LLM_API_KEY` from that child only. It does not set `CLASSIFIER_APPROACH`, so classification stays on approach 1 unless the replica environment already set it. A null provider does not build a Haiku client and does not need `AZURE_BASE_URL`. The ranker runs only when that child still has `JEV_BASE_URL` and `JEV_API_KEY`. The container template does not set them. `dispute-copilot metrics` writes the metrics file. The checklist is `reports/checklist.json`.
4. In `mock`, `tools/evaluate.py` scores the submission. Only the mock container may see `dataset.json`, so the official evaluate step runs there. The gold file is not in the image. The runner uploads `feedback/dev_gold.json` from the checkout into `/tmp` on the mock, in 900-character pieces, and cleanup deletes it. The app container is not given the fixture.

The remote command prints `AZURE_CI_RESULT code=N`. A missing marker, an exec error, or a timeout is treated as unreachable and the job exits 1. There is no fallback to port 8765 for `run-all`. The job also exits 1 when `run-all` misses a case, when any checklist row is not passed, or when evaluate is not a full pass.

The job does not approve, reject, submit a review, create a record, or reset the mock. `agent-review` is the local checklist. It does not post a review. Paths under `/state` and under `/submissions` are refused, including a path that resolves to those directories. The demo ledger `/state/run.sqlite` is never opened. Reviews that already exist on that ledger are not read and not changed.

Cleanup runs even when a later step fails. On the mock it stops a process only when the pid is greater than 1 and `/proc/<pid>/cmdline` contains that `/tmp` state path, `runtime.py`, and `serve`. It then deletes the `/tmp` state file, the pid file, the log, the uploaded gold file, the uploaded submission, the uploaded script, and the piece files. On the app it deletes `/tmp/ci<run id>/` and the uploaded script. It does not delete `/submissions`. If cleanup does not finish, the log says so in these words: the demo ledger `/state/run.sqlite` was not opened, drafts under `/submissions` were not deleted, a side process may still be listening on `127.0.0.1` with a state file under `/tmp`, and this job does not stop a process it cannot identify. If the side mock does not stay up, the job fails and says the demo ledger was not used. It does not switch the draft run onto port 8765.

The artifact name is `azure`. It holds `results.json`, `reports/traces` (label `azure`), `reports/metrics.json`, `reports/checklist.json`, and `dev.json`, when those files came back. The status page can compare a local run with an Azure run by that label.

`python3 tools/azure_inside.py` is the script copied into the containers. It is not a second product entry point.

## What to create in Azure and GitHub

This workspace cannot reach the subscription. The commands below were not run. Replace `<subscription>` with the subscription id. The repository is `nachiketht/card-disputes-participant`. Its public id is `1424384880` (`gh api repos/nachiketht/card-disputes-participant` field `id`). The owner id on that same record is `318480473`.

Create the exec application. This identity is the one the push job uses. It must not be able to push an image or update the container app.

```sh
az ad app create --display-name card-disputes-github-exec
APP_ID=$(az ad app list --display-name card-disputes-github-exec --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"
APP_OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)
```

`AZURE_CLIENT_ID` is `$APP_ID`, the application id, not the object id.

Save this file as `exec-federated.json`. The expression matches a push on any branch head of this repository, and it also requires the repository id claim. It does not match a pull request subject or a fork.

```json
{
  "name": "github-exec-branches",
  "issuer": "https://token.actions.githubusercontent.com",
  "audiences": ["api://AzureADTokenExchange"],
  "claimsMatchingExpression": {
    "value": "claims['sub'] matches 'repo:nachiketht/card-disputes-participant:ref:refs/heads/*' and claims['repository_id'] eq '1424384880'",
    "languageVersion": 1
  }
}
```

`az ad app federated-credential create` still rejects a credential that has no `subject` (`Property 'subject' cannot be empty`, Azure CLI issue 32816). Create the flexible credential with Graph:

```sh
az rest --method POST \
  --uri "https://graph.microsoft.com/beta/applications/${APP_OBJECT_ID}/federatedIdentityCredentials" \
  --headers "Content-Type=application/json" \
  --body @exec-federated.json
```

If the tenant rejects `claimsMatchingExpression`, create one exact subject per branch instead. Do not also keep the flexible credential for a branch you add this way. For `mvp-v1`, save `exec-mvp-v1.json`:

```json
{
  "name": "github-exec-mvp-v1",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:nachiketht/card-disputes-participant:ref:refs/heads/mvp-v1",
  "audiences": ["api://AzureADTokenExchange"]
}
```

```sh
az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters exec-mvp-v1.json
```

Repeat that file with a new name and subject for each other branch that should run the job. If GitHub later presents an immutable subject and sign-in fails on the subject, change the `sub` pattern to `repo:nachiketht@318480473/card-disputes-participant@1424384880:ref:refs/heads/*` and keep the `repository_id` clause. Add `and claims['repository_owner_id'] eq '318480473'` when the credential should also require that owner.

Save `exec-role.json`. `Microsoft.App/containerApps/exec/action` is a data action. `debug` and `listSecrets` are not in this role.

```json
{
  "Name": "card-disputes-exec",
  "Description": "Read card-disputes and open a container console. No secret list and no write.",
  "Actions": [
    "Microsoft.App/containerApps/read",
    "Microsoft.App/containerApps/revisions/read",
    "Microsoft.App/containerApps/revisions/replicas/read",
    "Microsoft.App/containerApps/getAuthToken/action",
    "Microsoft.App/managedEnvironments/read"
  ],
  "NotActions": [],
  "DataActions": [
    "Microsoft.App/containerApps/exec/action"
  ],
  "NotDataActions": [],
  "AssignableScopes": [
    "/subscriptions/<subscription>/resourceGroups/card-disputes-rg"
  ]
}
```

```sh
az role definition create --role-definition exec-role.json
az role assignment create \
  --assignee "$APP_ID" \
  --role card-disputes-exec \
  --scope "/subscriptions/<subscription>/resourceGroups/card-disputes-rg"
```

On the GitHub repository, create these secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | exec application id (`$APP_ID` above) |
| `AZURE_TENANT_ID` | tenant id (`az account show --query tenantId -o tsv`) |
| `AZURE_SUBSCRIPTION_ID` | `<subscription>` |

The same three names may be repository variables. A secret wins when it is set. Prefer secrets.

Create a second application for the manual image update. Do not assign `card-disputes-exec` to it, and do not assign the roles below to the exec application.

```sh
az ad app create --display-name card-disputes-github-deploy
DEPLOY_APP_ID=$(az ad app list --display-name card-disputes-github-deploy --query "[0].appId" -o tsv)
az ad sp create --id "$DEPLOY_APP_ID"
DEPLOY_OBJECT_ID=$(az ad app show --id "$DEPLOY_APP_ID" --query id -o tsv)
```

Use the same federated-credential JSON with `"name": "github-deploy-branches"` and the same expression, posted to `applications/${DEPLOY_OBJECT_ID}/federatedIdentityCredentials`. The fallback subject file uses a different name, for example `github-deploy-mvp-v1`.

```sh
ACR_ID=$(az acr show --name pod3disputes --query id -o tsv)
az role assignment create --assignee "$DEPLOY_APP_ID" --role AcrPush --scope "$ACR_ID"
az role assignment create \
  --assignee "$DEPLOY_APP_ID" \
  --role "Container Apps Contributor" \
  --scope "/subscriptions/<subscription>/resourceGroups/card-disputes-rg"
```

GitHub secret `AZURE_DEPLOY_CLIENT_ID` is `$DEPLOY_APP_ID`. Tenant and subscription stay the secrets already created.

## Risks

A console in the container is a shell as the container user. That shell can read the process environment, and the running app holds the judge key as the Container App secret `llm-api-key`. `getAuthToken` is required for the console and is enough, with `exec`, to open that shell. Keep the exec role on resource group `card-disputes-rg`. Do not grant Contributor, `Microsoft.App/containerApps/debug/action`, or any `listSecrets` action to the push identity. The workflow omits key-shaped lines from its own log. That does not stop a person who holds this role from opening their own console. Do not print secret values in a later step. `az containerapp secret list --query "[].name"` prints names only. `az containerapp secret show` prints values and is not in the workflow.

`Container Apps Contributor` on the deploy identity can read secret values. That is why it is a different application from the one used on every push. The role is also broader than a container image write. A narrower `Microsoft.App/containerApps/write` might update the image, and it might also drop `llm-api-key` if the CLI sends a full template. This was not tried. After the first manual run, `az containerapp secret list --name card-disputes --resource-group card-disputes-rg --query "[].name" -o tsv` should still show the name `llm-api-key` and must not show the value.

## Manual image update

`.github/workflows/azure-deploy.yml` is `workflow_dispatch` only. Push does not start it. The file is marked unverified until a person runs it. It was not executed from this workspace. It signs in with `AZURE_DEPLOY_CLIENT_ID` and the same tenant and subscription. A missing deploy client id fails the run. It does not skip, because a person started it.

The image tag is `pod3disputes.azurecr.io/card-disputes:${GITHUB_SHA}`. The job runs `az acr login --name pod3disputes`, `docker build`, `docker push`, then `az containerapp update` once for container `mock` and once for container `app`. It does not pass `--ingress`. A new revision clears the temporary ledger at `/state` and the temporary drafts at `/submissions`. Files on the shares stay. The last step lists secret names only.

## What was run here

The local rehearsal is `tests/unit/test_azure_live.py`. It is included in the pytest count in `docs/LIMITATIONS.md`. On that rehearsal the demo ledger kept review `rev_seed` at `pending` and the calls row `CARD-D01` / `transactions` at 7. The side ledger had no reviews and at least one transaction read. Evaluate reported `passed` true, 12 of 12. The checklist had 12 rows and every row passed. A missing fixture path printed `fixture is not visible`. The real fixture path printed `fixture is visible` and exited 1.

This workspace did not run `az containerapp exec`, `az login`, `azure/login`, `az acr login`, or `az containerapp update`. `docker` is not installed here, so the `image` job and the manual image update were not executed. The `script --return` wrapper was not run against the live replica. If that replica ends the detached side process when the console session closes, the next step fails closed and does not use port 8765.
