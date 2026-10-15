#!/usr/bin/env bash
# Runs on the VM in /opt/ngao after the workflow uploaded the bundle (docker-compose.yml, Caddyfile,
# backend.env, ussd.env, .env, wallet/). The GHCR read token arrives on stdin, never on the command line.
#
#   remote-deploy.sh <ghcr-user> <public-base-url>   < token
#
# Order matters: the new backend image checks the live schema first (check_schema). If NgaoDB is
# stopped or a column is missing, the deploy stops here and the running containers are left alone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ghcr_user="${1:?ghcr user}"
base_url="${2:?public base url}"
IFS= read -r ghcr_token || true

compose_version="$(docker compose version --short | sed 's/^v//')"
if [[ "$(printf '%s\n2.30.0\n' "$compose_version" | sort -V | head -1)" != "2.30.0" ]]; then
  echo "::error::Docker Compose $compose_version is too old (need >= 2.30 for env_file format: raw)."
  exit 1
fi

if [[ -n "$ghcr_token" ]]; then
  printf '%s' "$ghcr_token" | docker login ghcr.io -u "$ghcr_user" --password-stdin >/dev/null
  trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
fi

if [[ "${NGAO_LOCAL_IMAGES:-0}" == "1" ]]; then
  echo "== pull skipped (NGAO_LOCAL_IMAGES=1: images were loaded with docker load)"
else
  echo "== pull"
  docker compose pull --quiet
fi

echo "== schema check (new image, live database)"
if ! docker compose run --rm --no-deps -T api python manage.py check_schema; then
  echo "::error::check_schema failed: NgaoDB is unreachable (Always Free stops idle databases) or the schema is missing what the backend writes. Running containers were not replaced."
  exit 1
fi

echo "== start"
docker compose up -d --remove-orphans

echo "== wait for api health"
for _ in $(seq 1 40); do
  state="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || echo starting)"
  [[ "$state" == "healthy" ]] && break
  sleep 3
done
if [[ "$state" != "healthy" ]]; then
  docker compose logs --tail 80 api
  echo "::error::api did not become healthy (last state: $state)"
  exit 1
fi

if [[ "$base_url" == https://* ]]; then
  docker compose exec -T api python manage.py set_backend_config --base-url "$base_url"
fi

docker image prune -f >/dev/null
docker compose ps
