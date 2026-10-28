#!/usr/bin/env bash
# Point cd-review at the judge image and the existing Key Vault model secrets.
# Run in Cloud Shell from a checkout of prod. This does not update the card-disputes app
# and does not print secret values.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "This script takes no arguments. It only updates cd-review." >&2
  exit 1
fi

RG=card-disputes-rg
APP=cd-review
IMAGE=pod3disputes.azurecr.io/card-disputes:prod-review-judge

if [ "$APP" != "cd-review" ]; then
  echo "This script only updates cd-review." >&2
  exit 1
fi

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

REVIEW_ID=$(az identity show --resource-group "$RG" --name id-review --query id --output tsv)

az acr build --registry pod3disputes --image card-disputes:prod-review-judge "$ROOT"

az containerapp secret set \
  --name "$APP" \
  --resource-group "$RG" \
  --secrets \
    "llm-api-key=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/llm-api-key,identityref:${REVIEW_ID}" \
    "azure-base-url=keyvaultref:https://kv-carddisputes.vault.azure.net/secrets/azure-base-url,identityref:${REVIEW_ID}"

az containerapp update \
  --name "$APP" \
  --resource-group "$RG" \
  --image "$IMAGE" \
  --remove-env-vars LLM_PROVIDER \
  --set-env-vars \
    FEATURE_LLM_REVIEW=block \
    LLM_MODEL=gpt-6-luna \
    LLM_API_STYLE=openai \
    LLM_API_KEY=secretref:llm-api-key \
    AZURE_BASE_URL=secretref:azure-base-url
