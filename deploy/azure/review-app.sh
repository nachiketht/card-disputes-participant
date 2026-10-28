#!/usr/bin/env bash
# Build card-disputes:prod-review and create scale-to-zero app cd-review.
# Run in Cloud Shell from a checkout of prod. This does not update the card-disputes app
# and does not print secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only creates cd-review on card-disputes-env-vnet." >&2
  exit 1
fi

RG=card-disputes-rg
ENV_NAME=card-disputes-env-vnet
APP=cd-review
ACCOUNT=stcd319c1259
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-review

if [ "$ENV_NAME" != "card-disputes-env-vnet" ] || [ "$APP" != "cd-review" ]; then
  echo "This script only creates cd-review on card-disputes-env-vnet." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

JOB_ID=$(az identity show --resource-group "$RG" --name id-job --query id --output tsv)
REVIEW_ID=$(az identity show --resource-group "$RG" --name id-review --query id --output tsv)
REVIEW_CLIENT=$(az identity show --resource-group "$RG" --name id-review --query clientId --output tsv)

az acr build --registry pod3disputes --image card-disputes:prod-review "$ROOT"

az containerapp create \
  --name "$APP" \
  --resource-group "$RG" \
  --environment "$ENV_NAME" \
  --image "$IMAGE" \
  --ingress external \
  --target-port 8080 \
  --min-replicas 0 \
  --max-replicas 1 \
  --cpu 0.5 \
  --memory 1.0Gi \
  --command python \
  --args tools/review_run.py \
  --user-assigned "$REVIEW_ID" "$JOB_ID" \
  --registry-server pod3disputes.azurecr.io \
  --registry-identity "$JOB_ID" \
  --secrets \
    "pg-login=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login,identityref:${REVIEW_ID}" \
    "pg-login-password=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/pg-login-password,identityref:${REVIEW_ID}" \
  --env-vars \
    POSTGRES_HOST=pg-cd-wus3-02.postgres.database.azure.com \
    POSTGRES_PORT=5432 \
    POSTGRES_DB=carddisputes \
    POSTGRES_USER=secretref:pg-login \
    POSTGRES_PASSWORD=secretref:pg-login-password \
    AZURE_STORAGE_ACCOUNT="$ACCOUNT" \
    CASES_CONTAINER=cases \
    "AZURE_CLIENT_ID=${REVIEW_CLIENT}" \
    CASES_BACKEND=blob \
    LEDGER_BACKEND=postgres \
    REVIEW_BACKEND=postgres \
    FEATURE_LLM_REVIEW=off \
    LLM_PROVIDER=null \
    MOCK_BASE_URL=http://127.0.0.1:8765

FQDN=$(az containerapp show --name "$APP" --resource-group "$RG" --query properties.configuration.ingress.fqdn --output tsv)
echo "https://${FQDN}"
