#!/usr/bin/env bash
# Build card-disputes:prod-dashboard, point cd-approach3 at that image, and create cd-dashboard.
# Run in Cloud Shell from a checkout of prod. This does not start cd-approach3, does not
# start cd-batch, does not update cd-review or the card-disputes app, and does not print
# secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only creates cd-dashboard on card-disputes-env-vnet." >&2
  exit 1
fi

RG=card-disputes-rg
ENV_NAME=card-disputes-env-vnet
APP=cd-dashboard
JOB=cd-approach3
ACCOUNT=stcd319c1259
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-dashboard

if [ "$ENV_NAME" != "card-disputes-env-vnet" ] || [ "$APP" != "cd-dashboard" ] || [ "$JOB" != "cd-approach3" ]; then
  echo "This script only creates cd-dashboard and updates cd-approach3 on card-disputes-env-vnet." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

JOB_ID=$(az identity show --resource-group "$RG" --name id-job --query id --output tsv)
REVIEW_ID=$(az identity show --resource-group "$RG" --name id-review --query id --output tsv)
REVIEW_CLIENT=$(az identity show --resource-group "$RG" --name id-review --query clientId --output tsv)
REVIEW_PRINCIPAL=$(az identity show --resource-group "$RG" --name id-review --query principalId --output tsv)
ACCOUNT_ID=$(az storage account show --name "$ACCOUNT" --resource-group "$RG" --query id --output tsv)

READER=$(az role assignment list \
  --assignee-object-id "$REVIEW_PRINCIPAL" \
  --scope "$ACCOUNT_ID" \
  --role "Storage Blob Data Reader" \
  --query "[0].id" \
  --output tsv)
if [ -z "$READER" ]; then
  az role assignment create \
    --assignee-object-id "$REVIEW_PRINCIPAL" \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Reader" \
    --scope "${ACCOUNT_ID}/blobServices/default/containers/reports"
fi

az acr build --registry pod3disputes --image card-disputes:prod-dashboard "$ROOT"

az containerapp job update \
  --name "$JOB" \
  --resource-group "$RG" \
  --container-name "$JOB" \
  --image "$IMAGE" \
  --set-env-vars REPORTS_CONTAINER=reports

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
  --args tools/dashboard_run.py \
  --user-assigned "$REVIEW_ID" "$JOB_ID" \
  --registry-server pod3disputes.azurecr.io \
  --registry-identity "$JOB_ID" \
  --env-vars \
    AZURE_STORAGE_ACCOUNT="$ACCOUNT" \
    REPORTS_CONTAINER=reports \
    "AZURE_CLIENT_ID=${REVIEW_CLIENT}"

FQDN=$(az containerapp show --name "$APP" --resource-group "$RG" --query properties.configuration.ingress.fqdn --output tsv)
echo "https://${FQDN}"
