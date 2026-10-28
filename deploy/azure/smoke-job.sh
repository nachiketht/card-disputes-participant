#!/usr/bin/env bash
# Build card-disputes:prod-check and start manual job cd-smoke.
# Run on a machine that has this repo, Docker, and az. This does not update
# the card-disputes app and does not print secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only starts cd-smoke on card-disputes-env-vnet." >&2
  exit 1
fi

RG=card-disputes-rg
ENV_NAME=card-disputes-env-vnet
JOB=cd-smoke
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-check

if [ "$ENV_NAME" != "card-disputes-env-vnet" ] || [ "$JOB" != "cd-smoke" ]; then
  echo "This script only starts cd-smoke on card-disputes-env-vnet." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

JOB_ID=$(az identity show --resource-group "$RG" --name id-job --query id --output tsv)
JOB_CLIENT=$(az identity show --resource-group "$RG" --name id-job --query clientId --output tsv)
PULL_ID=$(az identity show --resource-group "$RG" --name card-disputes-pull --query id --output tsv)

az acr login --name pod3disputes
docker build -t "$IMAGE" "$ROOT"
docker push "$IMAGE"

az containerapp job create \
  --name "$JOB" \
  --resource-group "$RG" \
  --environment "$ENV_NAME" \
  --trigger-type Manual \
  --replica-timeout 300 \
  --replica-retry-limit 0 \
  --replica-completion-count 1 \
  --parallelism 1 \
  --image "$IMAGE" \
  --container-name "$JOB" \
  --cpu 0.5 \
  --memory 1.0Gi \
  --command python \
  --args tools/cloud_check.py \
  --mi-user-assigned "$JOB_ID" "$PULL_ID" \
  --registry-server pod3disputes.azurecr.io \
  --registry-identity "$PULL_ID" \
  --secrets \
    "pg-login=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login,identityref:${JOB_ID}" \
    "pg-login-password=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login-password,identityref:${JOB_ID}" \
  --env-vars \
    POSTGRES_HOST=pg-cd-wus3-02.postgres.database.azure.com \
    POSTGRES_PORT=5432 \
    POSTGRES_DB=carddisputes \
    POSTGRES_USER=secretref:pg-login \
    POSTGRES_PASSWORD=secretref:pg-login-password \
    AZURE_STORAGE_ACCOUNT=stcd319c1259 \
    CASES_CONTAINER=cases \
    AUDIT_CONTAINER=audit \
    "AZURE_CLIENT_ID=${JOB_CLIENT}"

EXECUTION=$(az containerapp job start --name "$JOB" --resource-group "$RG" --query name --output tsv)
echo "execution ${EXECUTION}"
az containerapp job logs show --name "$JOB" --resource-group "$RG" --container "$JOB" --execution "$EXECUTION"
