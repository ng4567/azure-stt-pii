#!/usr/bin/env bash

set -euo pipefail

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Install it before deploying." >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "Azure CLI is not logged in. Run 'az login' before deploying." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

az deployment sub create \
  --name stt-pii-foundry \
  --location eastus \
  --template-file "$script_dir/main.bicep" \
  "$@"
