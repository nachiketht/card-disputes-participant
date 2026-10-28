#!/usr/bin/env bash
# Upload the 12 development cases, build card-disputes:prod-batch, and start cd-batch.
# Run in Cloud Shell from a checkout of prod. This does not update the card-disputes app
# and does not print secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only starts cd-batch on card-disputes-env-vnet." >&2
  exit 1
fi

RG=card-disputes-rg
ENV_NAME=card-disputes-env-vnet
JOB=cd-batch
ACCOUNT=stcd319c1259
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-batch

if [ "$ENV_NAME" != "card-disputes-env-vnet" ] || [ "$JOB" != "cd-batch" ]; then
  echo "This script only starts cd-batch on card-disputes-env-vnet." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

JOB_ID=$(az identity show --resource-group "$RG" --name id-job --query id --output tsv)
JOB_CLIENT=$(az identity show --resource-group "$RG" --name id-job --query clientId --output tsv)
JOB_PRINCIPAL=$(az identity show --resource-group "$RG" --name id-job --query principalId --output tsv)
ACR_ID=$(az acr show --name pod3disputes --query id --output tsv)

EXISTING=$(az role assignment list --assignee-object-id "$JOB_PRINCIPAL" --scope "$ACR_ID" --role AcrPull --query "[0].id" --output tsv)
if [ -z "$EXISTING" ]; then
  az role assignment create \
    --assignee-object-id "$JOB_PRINCIPAL" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$ACR_ID"
fi

az storage blob upload \
  --account-name "$ACCOUNT" \
  --container-name cases \
  --name dataset.json \
  --file "$ROOT/data/dev/dataset.json" \
  --auth-mode login \
  --only-show-errors
az storage blob upload-batch \
  --account-name "$ACCOUNT" \
  --destination cases \
  --destination-path documents \
  --source "$ROOT/data/dev/documents" \
  --auth-mode login \
  --only-show-errors
az storage blob upload-batch \
  --account-name "$ACCOUNT" \
  --destination cases \
  --destination-path policies \
  --source "$ROOT/data/dev/policies" \
  --auth-mode login \
  --only-show-errors

az acr build --registry pod3disputes --image card-disputes:prod-batch "$ROOT"

az containerapp job create \
  --name "$JOB" \
  --resource-group "$RG" \
  --environment "$ENV_NAME" \
  --trigger-type Manual \
  --replica-timeout 600 \
  --replica-retry-limit 0 \
  --replica-completion-count 1 \
  --parallelism 1 \
  --image "$IMAGE" \
  --container-name "$JOB" \
  --cpu 0.5 \
  --memory 1.0Gi \
  --command python \
  --args tools/batch_run.py \
  --mi-user-assigned "$JOB_ID" \
  --registry-server pod3disputes.azurecr.io \
  --registry-identity "$JOB_ID" \
  --secrets \
    "pg-login=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login,identityref:${JOB_ID}" \
    "pg-login-password=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login-password,identityref:${JOB_ID}" \
  --env-vars \
    POSTGRES_HOST=pg-cd-wus3-02.postgres.database.azure.com \
    POSTGRES_PORT=5432 \
    POSTGRES_DB=carddisputes \
    POSTGRES_USER=secretref:pg-login \
    POSTGRES_PASSWORD=secretref:pg-login-password \
    AZURE_STORAGE_ACCOUNT="$ACCOUNT" \
    CASES_CONTAINER=cases \
    AUDIT_CONTAINER=audit \
    "AZURE_CLIENT_ID=${JOB_CLIENT}" \
    CASES_BACKEND=blob \
    LEDGER_BACKEND=postgres \
    REVIEW_BACKEND=postgres \
    AUDIT_BACKEND=blob \
    FEATURE_LLM_REVIEW=off \
    LLM_PROVIDER=null

EXECUTION=$(az containerapp job start --name "$JOB" --resource-group "$RG" --query name --output tsv)
echo "execution ${EXECUTION}"
az containerapp job logs show --name "$JOB" --resource-group "$RG" --container "$JOB" --execution "$EXECUTION"
