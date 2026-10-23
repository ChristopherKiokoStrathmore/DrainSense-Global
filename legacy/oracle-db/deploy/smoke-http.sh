#!/usr/bin/env bash
# GET the public APEX home page. Used after a real deploy, and by the local self-test.
set -euo pipefail

url="${NGAO_SMOKE_URL:-https://g93fcef1cec31ea-ngaodb.adb.eu-frankfurt-1.oraclecloudapps.com/ords/r/ngao/ngao-mvuasmart/home}"
attempts="${NGAO_SMOKE_ATTEMPTS:-5}"
sleep_override="${NGAO_SMOKE_SLEEP_SECONDS-}"

host="$(python3 - "$url" <<'PY'
import sys
from urllib.parse import urlparse
print(urlparse(sys.argv[1]).hostname or "")
PY
)"

if [[ "$url" != https://* ]]; then
  if [[ "${NGAO_SMOKE_ALLOW_HTTP:-}" == "1" && "$host" == "127.0.0.1" ]]; then
    :
  else
    echo "::error::Smoke URL must be https (got ${url}). Set NGAO_SMOKE_URL to the public app home URL."
    exit 1
  fi
fi

body="$(mktemp)"
curl_err="${body}.err"
cookie_jar="${body}.cookies"
# shellcheck disable=SC2317  # invoked by the EXIT trap
cleanup() {
  rm -f "$body" "$curl_err" "$cookie_jar"
}
trap cleanup EXIT

code=""
final=""
curl_rc=0
for ((attempt = 1; attempt <= attempts; attempt++)); do
  set +e
  # APEX friendly URLs answer 302 and set ORA_WWV_APP_100. Without the cookie
  # the next request is a new session and the redirect repeats.
  curl_out="$(curl --silent --show-error --location --max-redirs 10 --max-time 60 \
    --cookie-jar "$cookie_jar" --cookie "$cookie_jar" \
    --user-agent 'NgaoDeploySmoke/1.0' \
    --output "$body" --write-out '%{http_code} %{url_effective}' \
    "$url" 2>"$curl_err")"
  curl_rc=$?
  set -e
  read -r code final <<<"$curl_out" || true
  if [[ "$curl_rc" -eq 0 && "$code" == "200" && -s "$body" ]]; then
    echo "Smoke check OK: HTTP 200 ${final}"
    exit 0
  fi
  echo "Smoke attempt ${attempt}/${attempts}: curl_status=${curl_rc} http=${code:-none} final=${final:-none}"
  if [[ -s "$curl_err" ]]; then
    echo "curl error:"
    cat "$curl_err"
  fi
  if [[ "$attempt" -lt "$attempts" ]]; then
    if [[ -n "$sleep_override" ]]; then
      sleep "$sleep_override"
    else
      sleep $((attempt * 5))
    fi
  fi
done

echo "::error::Post-deploy smoke check failed: ${url} did not return HTTP 200 with a body."
if [[ -s "$body" ]]; then
  python3 - "$body" <<'PY'
import re
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
if match:
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    print(f"Response title: {title[:200]}")
PY
fi
exit 1
