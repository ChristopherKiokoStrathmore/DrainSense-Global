#!/usr/bin/env bash
# Local and pull-request checks. Nothing here connects to NgaoDB.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    db/deploy/check-manifest.sh \
    db/deploy/require-secrets.sh \
    db/deploy/install-sqlcl.sh \
    db/deploy/smoke-http.sh \
    db/deploy/deploy.sh \
    db/deploy/self-test.sh
else
  echo "shellcheck is not installed; skipping shell lint."
fi

python3 db/deploy/check_manifest.py --self-test
python3 db/deploy/decode-wallet.py --self-test
python3 db/deploy/render_deploy_sql.py --self-test
bash db/deploy/check-manifest.sh

echo "=== require-secrets: missing values must fail before any database work ==="
set +e
missing_out="$(
  env \
    NGAO_DB_WALLET_B64= \
    NGAO_DB_USER='   ' \
    NGAO_DB_PASSWORD= \
    NGAO_DB_SERVICE= \
    bash db/deploy/require-secrets.sh 2>&1
)"
missing_rc=$?
set -e
if [[ "$missing_rc" -eq 0 ]]; then
  echo "require-secrets.sh exited 0 with empty secrets" >&2
  exit 1
fi
for name in NGAO_DB_WALLET_B64 NGAO_DB_USER NGAO_DB_PASSWORD NGAO_DB_SERVICE; do
  if [[ "$missing_out" != *"$name"* ]]; then
    echo "missing-secret message did not name ${name}" >&2
    echo "$missing_out" >&2
    exit 1
  fi
done
if [[ "$missing_out" != *"No wallet was decoded"* ]]; then
  echo "missing-secret message did not say the wallet was left alone" >&2
  echo "$missing_out" >&2
  exit 1
fi

NGAO_DB_WALLET_B64=YQ== \
  NGAO_DB_USER=WKSP_NGAO \
  NGAO_DB_PASSWORD='secret' \
  NGAO_DB_SERVICE=ngaodb_low \
  bash db/deploy/require-secrets.sh >/dev/null

echo "=== deploy.sh: invalid wallet fails before SQLcl is invoked ==="
set +e
wallet_out="$(
  PATH="/usr/bin:/bin" \
    NGAO_DB_WALLET_B64="$(printf '%s' 'hello' | base64 -w 0)" \
    NGAO_DB_USER=WKSP_NGAO \
    NGAO_DB_PASSWORD=secret \
    NGAO_DB_SERVICE=ngaodb_low \
    bash db/deploy/deploy.sh 2>&1
)"
wallet_rc=$?
set -e
if [[ "$wallet_rc" -eq 0 ]]; then
  echo "deploy.sh should fail when the wallet is not a zip" >&2
  exit 1
fi
if [[ "$wallet_out" != *"not a zip"* ]]; then
  echo "deploy.sh did not report a bad wallet" >&2
  echo "$wallet_out" >&2
  exit 1
fi
if [[ "$wallet_out" == *"SQLcl"* ]]; then
  echo "deploy.sh reached SQLcl after a bad wallet" >&2
  echo "$wallet_out" >&2
  exit 1
fi

echo "=== smoke-http: local 200, local 404, and non-local http ==="
python3 - <<'PY'
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path.cwd()
script = root / "db/deploy/smoke-http.sh"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/ok"):
            body = b"<html><title>Ngao MvuaSmart</title><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"<html><title>Missing</title></html>"
        self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
env = os.environ.copy()
env.update({
    "NGAO_SMOKE_ALLOW_HTTP": "1",
    "NGAO_SMOKE_ATTEMPTS": "1",
    "NGAO_SMOKE_SLEEP_SECONDS": "0",
})

def run(url):
    env["NGAO_SMOKE_URL"] = url
    return subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)

try:
    ok = run(f"http://127.0.0.1:{port}/ok")
    if ok.returncode != 0:
        sys.stderr.write(ok.stdout + ok.stderr)
        raise SystemExit("expected HTTP 200 smoke to pass")
    bad = run(f"http://127.0.0.1:{port}/missing")
    if bad.returncode == 0 or "did not return HTTP 200" not in (bad.stdout + bad.stderr):
        sys.stderr.write(bad.stdout + bad.stderr)
        raise SystemExit("expected HTTP 404 smoke to fail")
finally:
    server.shutdown()

env = os.environ.copy()
env["NGAO_SMOKE_URL"] = "http://example.com/home"
env["NGAO_SMOKE_ATTEMPTS"] = "1"
refused = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
if refused.returncode == 0 or "must be https" not in (refused.stdout + refused.stderr):
    sys.stderr.write(refused.stdout + refused.stderr)
    raise SystemExit("non-local http URL should be rejected")
print("smoke-http self-test ok")
PY

echo "self-test ok"
