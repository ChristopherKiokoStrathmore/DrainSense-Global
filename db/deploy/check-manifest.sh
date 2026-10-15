#!/usr/bin/env bash
# Lint db/deploy/manifest.txt. Does not connect to the database.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "$ROOT/db/deploy/check_manifest.py" "$@"
