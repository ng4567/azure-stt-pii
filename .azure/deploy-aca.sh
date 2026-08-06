#!/usr/bin/env bash

set -euo pipefail

SUBSCRIPTION_ID="fd918039-a89e-49a7-8e32-af614b3765f9"
LOCATION="eastus"
RESOURCE_GROUP="rg-stt-pii-aca"
ACR_NAME="acrsttpiifd9180"
ENVIRONMENT_NAME="cae-stt-pii-poc"
BACKEND_APP="ca-stt-pii-api"
FRONTEND_APP="ca-stt-pii-web"
: "${SPEECH_RESOURCE_GROUP:?Set SPEECH_RESOURCE_GROUP}"
: "${SPEECH_RESOURCE_NAME:?Set SPEECH_RESOURCE_NAME}"
SPEECH_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${SPEECH_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${SPEECH_RESOURCE_NAME}"
FOUNDRY_RESOURCE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/finance-app-ng/providers/Microsoft.CognitiveServices/accounts/finance-app-resource"
FOUNDRY_ENDPOINT="https://finance-app-resource.cognitiveservices.azure.com"
FOUNDRY_DEPLOYMENT="DeepSeek-V4-Flash"
DETAILS_FILE=".azure/aca-deployment.local.json"
IMAGE_TAG="$(git rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)"
BOOTSTRAP_IMAGE="mcr.microsoft.com/k8se/quickstart:latest"

az account set --subscription "$SUBSCRIPTION_ID"

az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --tags application=azure-stt-pii environment=poc managed-by=copilot \
  --output none

if ! az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" >/dev/null 2>&1; then
  az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --location "$LOCATION" \
    --sku Basic \
    --admin-enabled false \
    --output none
fi

if ! az containerapp env show --resource-group "$RESOURCE_GROUP" --name "$ENVIRONMENT_NAME" >/dev/null 2>&1; then
  az containerapp env create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ENVIRONMENT_NAME" \
    --location "$LOCATION" \
    --output none
fi

create_bootstrap_app() {
  local app_name="$1"
  local ingress="$2"
  if ! az containerapp show --resource-group "$RESOURCE_GROUP" --name "$app_name" >/dev/null 2>&1; then
    az containerapp create \
      --resource-group "$RESOURCE_GROUP" \
      --name "$app_name" \
      --environment "$ENVIRONMENT_NAME" \
      --image "$BOOTSTRAP_IMAGE" \
      --ingress "$ingress" \
      --target-port 80 \
      --system-assigned \
      --min-replicas 1 \
      --max-replicas 1 \
      --output none
  fi
}

create_bootstrap_app "$BACKEND_APP" internal
create_bootstrap_app "$FRONTEND_APP" external

BACKEND_PRINCIPAL_ID="$(az containerapp identity show --resource-group "$RESOURCE_GROUP" --name "$BACKEND_APP" --query principalId -o tsv)"
FRONTEND_PRINCIPAL_ID="$(az containerapp identity show --resource-group "$RESOURCE_GROUP" --name "$FRONTEND_APP" --query principalId -o tsv)"
ACR_ID="$(az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --query id -o tsv)"
ACR_SERVER="$(az acr show --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --query loginServer -o tsv)"

ensure_role() {
  local principal_id="$1"
  local role="$2"
  local scope="$3"
  if [[ "$(az role assignment list --assignee-object-id "$principal_id" --scope "$scope" --query "[?roleDefinitionName=='${role}'] | length(@)" -o tsv)" == "0" ]]; then
    az role assignment create \
      --assignee-object-id "$principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

ensure_role "$BACKEND_PRINCIPAL_ID" AcrPull "$ACR_ID"
ensure_role "$FRONTEND_PRINCIPAL_ID" AcrPull "$ACR_ID"
ensure_role "$BACKEND_PRINCIPAL_ID" "Cognitive Services User" "$SPEECH_RESOURCE_ID"
ensure_role "$BACKEND_PRINCIPAL_ID" "Cognitive Services User" "$FOUNDRY_RESOURCE_ID"

wait_for_acr_pull() {
  local principal_id="$1"
  local app_name="$2"
  for attempt in 1 2 3 4 5; do
    if [[ "$(az role assignment list --assignee-object-id "$principal_id" --scope "$ACR_ID" --query "[?roleDefinitionName=='AcrPull'] | length(@)" -o tsv)" != "0" ]]; then
      printf 'AcrPull confirmed for %s.\n' "$app_name"
      return 0
    fi
    if [[ "$attempt" == "5" ]]; then
      printf 'AcrPull did not propagate for %s within five minutes.\n' "$app_name" >&2
      return 1
    fi
    printf 'Waiting for AcrPull propagation for %s (attempt %s/5)...\n' "$app_name" "$attempt"
    sleep 60
  done
}

wait_for_acr_pull "$BACKEND_PRINCIPAL_ID" "$BACKEND_APP"
wait_for_acr_pull "$FRONTEND_PRINCIPAL_ID" "$FRONTEND_APP"

az acr build \
  --registry "$ACR_NAME" \
  --image "azure-stt-pii-backend:${IMAGE_TAG}" \
  --file backend/Dockerfile \
  .

az acr build \
  --registry "$ACR_NAME" \
  --image "azure-stt-pii-frontend:${IMAGE_TAG}" \
  --file frontend/Dockerfile \
  .

az containerapp registry set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BACKEND_APP" \
  --server "$ACR_SERVER" \
  --identity system \
  --output none

az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BACKEND_APP" \
  --image "${ACR_SERVER}/azure-stt-pii-backend:${IMAGE_TAG}" \
  --set-env-vars \
    AZURE_LANGUAGE_ENDPOINT="$FOUNDRY_ENDPOINT" \
    AZURE_FOUNDRY_ENDPOINT="$FOUNDRY_ENDPOINT" \
    AZURE_FOUNDRY_DEPLOYMENT="$FOUNDRY_DEPLOYMENT" \
    ALLOWED_ORIGINS="*" \
    MAX_CONCURRENT_JOBS=1 \
  --cpu 2.0 \
  --memory 4Gi \
  --min-replicas 1 \
  --max-replicas 1 \
  --output none

az containerapp ingress update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BACKEND_APP" \
  --target-port 8000 \
  --transport http \
  --output none

BACKEND_FQDN="$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$BACKEND_APP" --query properties.configuration.ingress.fqdn -o tsv)"

az containerapp registry set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FRONTEND_APP" \
  --server "$ACR_SERVER" \
  --identity system \
  --output none

az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FRONTEND_APP" \
  --image "${ACR_SERVER}/azure-stt-pii-frontend:${IMAGE_TAG}" \
  --set-env-vars BACKEND_URL="https://${BACKEND_FQDN}" PORT=3000 \
  --cpu 0.5 \
  --memory 1Gi \
  --min-replicas 0 \
  --max-replicas 1 \
  --output none

az containerapp ingress update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$FRONTEND_APP" \
  --target-port 3000 \
  --transport http \
  --output none

FRONTEND_FQDN="$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$FRONTEND_APP" --query properties.configuration.ingress.fqdn -o tsv)"

jq -n \
  --arg deployedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg subscriptionId "$SUBSCRIPTION_ID" \
  --arg resourceGroup "$RESOURCE_GROUP" \
  --arg location "$LOCATION" \
  --arg acrName "$ACR_NAME" \
  --arg environmentName "$ENVIRONMENT_NAME" \
  --arg backendApp "$BACKEND_APP" \
  --arg backendFqdn "$BACKEND_FQDN" \
  --arg backendPrincipalId "$BACKEND_PRINCIPAL_ID" \
  --arg frontendApp "$FRONTEND_APP" \
  --arg frontendFqdn "$FRONTEND_FQDN" \
  --arg frontendPrincipalId "$FRONTEND_PRINCIPAL_ID" \
  --arg imageTag "$IMAGE_TAG" \
  '{
    deployedAt: $deployedAt,
    subscriptionId: $subscriptionId,
    resourceGroup: $resourceGroup,
    location: $location,
    registry: {name: $acrName},
    environment: {name: $environmentName},
    backend: {
      name: $backendApp,
      url: ("https://" + $backendFqdn),
      healthUrl: ("https://" + $backendFqdn + "/api/health"),
      principalId: $backendPrincipalId
    },
    frontend: {
      name: $frontendApp,
      url: ("https://" + $frontendFqdn),
      healthUrl: ("https://" + $frontendFqdn + "/api/health"),
      principalId: $frontendPrincipalId
    },
    imageTag: $imageTag
  }' > "$DETAILS_FILE"

printf 'Frontend: https://%s\n' "$FRONTEND_FQDN"
printf 'Health:   https://%s/api/health\n' "$FRONTEND_FQDN"
