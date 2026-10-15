#!/usr/bin/env bash
# Download Oracle's current SQLcl zip and put sql on PATH.
# JDK 21 (or 17) must already be installed. The workflow uses actions/setup-java.
set -euo pipefail

if ! command -v java >/dev/null 2>&1; then
  echo "::error::A JDK is not on PATH. Install Temurin 21 with actions/setup-java before this step. SQLcl was not started."
  exit 1
fi

dest="${SQLCL_INSTALL_DIR:-${RUNNER_TEMP:-/tmp}/sqlcl-install}"
mkdir -p "$dest"
archive="$dest/sqlcl-latest.zip"

echo "Downloading Oracle SQLcl (sqlcl-latest.zip)."
curl --fail --location --show-error --retry 5 --retry-delay 2 \
  -o "$archive" \
  "https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip"

unzip -qo "$archive" -d "$dest"
sql_bin="$dest/sqlcl/bin/sql"
if [[ ! -f "$sql_bin" ]]; then
  echo "::error::SQLcl zip did not contain sqlcl/bin/sql."
  exit 1
fi
chmod +x "$sql_bin"

if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$dest/sqlcl/bin" >> "$GITHUB_PATH"
fi

# The current step can call sql immediately. Later steps see GITHUB_PATH.
export PATH="$dest/sqlcl/bin:${PATH}"
echo "SQLCL_BIN=$sql_bin" >> "${GITHUB_ENV:-/dev/null}"

"$sql_bin" -version
