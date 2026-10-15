#!/usr/bin/env bash
# Connect to NgaoDB with the wallet secret and either apply the manifest + APEX
# import, or (dry run) connect and report without changing anything.
# Missing secrets abort in require-secrets.sh before a wallet is decoded.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash "$ROOT/db/deploy/require-secrets.sh"
bash "$ROOT/db/deploy/check-manifest.sh"

mode="apply"
case "${NGAO_DRY_RUN:-0}" in
  1 | true | TRUE | True) mode="dry-run" ;;
esac

workdir="$(mktemp -d)"
cleanup() {
  rm -rf "$workdir"
}
trap cleanup EXIT
chmod 700 "$workdir"

python3 "$ROOT/db/deploy/decode-wallet.py" --out "$workdir/wallet.zip"
export NGAO_WALLET_ZIP="$workdir/wallet.zip"

python3 "$ROOT/db/deploy/render_deploy_sql.py" --mode "$mode" --out "$workdir/session.sql"

if ! command -v sql >/dev/null 2>&1; then
  echo "::error::SQLcl (sql) is not on PATH. Install it with db/deploy/install-sqlcl.sh before deploy.sh. No database session was started."
  exit 1
fi

echo "Starting SQLcl in ${mode} mode. The session script and wallet are kept in a temp directory and deleted when this step exits."

set +e
timeout --signal=TERM 20m sql /nolog @"$workdir/session.sql"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  echo "::error::SQLcl exited with status ${rc}. The HTTP smoke check was not run."
  exit "$rc"
fi

if [[ "$mode" == "dry-run" ]]; then
  echo "Dry run finished. Manifest scripts were not executed and frontend/apex/f100.sql was not imported."
  exit 0
fi

bash "$ROOT/db/deploy/smoke-http.sh"
