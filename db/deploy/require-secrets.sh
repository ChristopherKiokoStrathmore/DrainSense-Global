#!/usr/bin/env bash
# Stop the deploy before any wallet decode or database call when secrets are absent.
set -euo pipefail

blank() {
  local value="${1-}"
  value="${value//[[:space:]]/}"
  [[ -z "$value" ]]
}

required=(
  NGAO_DB_WALLET_B64
  NGAO_DB_USER
  NGAO_DB_PASSWORD
  NGAO_DB_SERVICE
)

missing=()
for name in "${required[@]}"; do
  value="${!name-}"
  if blank "$value"; then
    missing+=("$name")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "::error::Missing GitHub Actions secrets: ${missing[*]}."
  echo "Add them under Settings → Secrets and variables → Actions → Repository secrets."
  echo "The names, and how to produce NGAO_DB_WALLET_B64 from the ADB wallet zip, are in docs/DEPLOY.md."
  echo "No wallet was decoded and no connection to NgaoDB was attempted."
  exit 1
fi

echo "Required NgaoDB secrets are present (values not printed)."
