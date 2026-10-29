#!/usr/bin/env bash
# Build card-disputes:prod-approach3 and start cd-approach3.
# Run in Cloud Shell from a checkout of prod. This does not update cd-review or the
# card-disputes app, does not start cd-batch, and does not print secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only starts cd-approach3 on card-disputes-env-vnet." >&2
  exit 1
fi

RG=card-disputes-rg
ENV_NAME=card-disputes-env-vnet
JOB=cd-approach3
ACCOUNT=stcd319c1259
VAULT=kv-carddisputes
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-approach3

if [ "$ENV_NAME" != "card-disputes-env-vnet" ] || [ "$JOB" != "cd-approach3" ]; then
  echo "This script only starts cd-approach3 on card-disputes-env-vnet." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

for name in jev-api-key jev-base-url clef-api-key clef-account-id llm-api-key azure-base-url pg-login pg-login-password; do
  if ! az keyvault secret show --vault-name "$VAULT" --name "$name" --query id --output tsv >/dev/null; then
    echo "missing secret ${name}" >&2
    exit 1
  fi
done

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

az acr build --registry pod3disputes --image card-disputes:prod-approach3 "$ROOT"

az containerapp job create \
  --name "$JOB" \
  --resource-group "$RG" \
  --environment "$ENV_NAME" \
  --trigger-type Manual \
  --replica-timeout 1800 \
  --replica-retry-limit 0 \
  --replica-completion-count 1 \
  --parallelism 1 \
  --image "$IMAGE" \
  --container-name "$JOB" \
  --cpu 0.5 \
  --memory 1.0Gi \
  --command python \
  --args tools/approach3_run.py \
  --mi-user-assigned "$JOB_ID" \
  --registry-server pod3disputes.azurecr.io \
  --registry-identity "$JOB_ID" \
  --secrets \
    "pg-login=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login,identityref:${JOB_ID}" \
    "pg-login-password=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login-password,identityref:${JOB_ID}" \
    "llm-api-key=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/llm-api-key,identityref:${JOB_ID}" \
    "azure-base-url=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/azure-base-url,identityref:${JOB_ID}" \
    "jev-api-key=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/jev-api-key,identityref:${JOB_ID}" \
    "jev-base-url=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/jev-base-url,identityref:${JOB_ID}" \
    "clef-api-key=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/clef-api-key,identityref:${JOB_ID}" \
    "clef-account-id=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/clef-account-id,identityref:${JOB_ID}" \
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
    LLM_MODEL=gpt-6-luna \
    LLM_API_STYLE=openai \
    LLM_API_KEY=secretref:llm-api-key \
    AZURE_BASE_URL=secretref:azure-base-url \
    JEV_MODEL=jev-latest \
    JEV_API_KEY=secretref:jev-api-key \
    JEV_BASE_URL=secretref:jev-base-url \
    CLEF_MODEL=clef-flash \
    CLEF_API_KEY=secretref:clef-api-key \
    CLEF_ACCOUNT_ID=secretref:clef-account-id

EXECUTION=$(az containerapp job start --name "$JOB" --resource-group "$RG" --query name --output tsv)
echo "execution ${EXECUTION}"
az containerapp job logs show --name "$JOB" --resource-group "$RG" --container "$JOB" --execution "$EXECUTION"
