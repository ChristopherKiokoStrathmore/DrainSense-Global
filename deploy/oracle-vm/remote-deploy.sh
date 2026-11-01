#!/usr/bin/env bash
# Runs on the Oracle VM, from /opt/drainsense, called by the deploy job in .github/workflows/ci.yml.
#
#   printf '%s\n' "$GHCR_TOKEN" | bash remote-deploy.sh <ghcr-user> <site-address>
#
# 1. Pull the API and desk images named in .env and start them (Compose project "drainsense").
#    If either is not healthy within two minutes, the previous images are started again.
# 2. Route <site-address> through the Caddy that NGAO's stack already runs on :80/:443.
#    DrainSense's site block lives in Caddy's config volume (/config/sites/drainsense.caddy), and
#    NGAO's Caddyfile gets one line, `import /config/sites/*.caddy`. The host's Caddyfile is
#    validated in a throwaway Caddy first; on failure it is restored and nothing changes.
#    Then Caddy reloads gracefully, unless its single-file bind mount still points at an older
#    copy of the Caddyfile (NGAO's deploy replaces the file): then the Caddy container alone is
#    recreated from NGAO's own Compose files, a few seconds of downtime for both sites.
#
# Settings (environment, all optional):
#   EDGE_PROJECT   Compose project that owns Caddy (default ngao)
#   EDGE_DIR       directory holding that project's Caddyfile (default /opt/ngao)
#   EDGE_NETWORK   Docker network Caddy and DrainSense share (default ngao_default)
#   DRAINSENSE_LOCAL_IMAGES=1   skip the pull (images were loaded with docker load)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ghcr_user="${1:?ghcr user}"
site="${2:?site address, e.g. drainsense.158.180.57.75.sslip.io}"
IFS= read -r ghcr_token || true

EDGE_PROJECT="${EDGE_PROJECT:-ngao}"
EDGE_DIR="${EDGE_DIR:-/opt/ngao}"
EDGE_NETWORK="${EDGE_NETWORK:-ngao_default}"
IMPORT_LINE='import /config/sites/*.caddy'

log() { printf '== %s\n' "$*"; }
fail() { printf '::error::%s\n' "$*"; exit 1; }

caddy_id="$(docker ps -q --filter "label=com.docker.compose.project=${EDGE_PROJECT}" \
  --filter "label=com.docker.compose.service=caddy" | head -1)"
[[ -n "$caddy_id" ]] || fail "No running Caddy in Compose project '${EDGE_PROJECT}'. DrainSense is served through it; start that stack first."
docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 \
  || fail "Docker network '${EDGE_NETWORK}' not found. Set EDGE_NETWORK to the network Caddy is on."
[[ -f "${EDGE_DIR}/Caddyfile" ]] || fail "${EDGE_DIR}/Caddyfile not found. Set EDGE_DIR."

scratch="$(mktemp -d)"
cleanup() {
  rm -rf "$scratch"
  [[ -n "$ghcr_token" ]] && docker logout ghcr.io >/dev/null 2>&1
  return 0
}
trap cleanup EXIT
if [[ -n "$ghcr_token" ]]; then
  printf '%s' "$ghcr_token" | docker login ghcr.io -u "$ghcr_user" --password-stdin >/dev/null
fi

export EDGE_NETWORK
if [[ "${DRAINSENSE_LOCAL_IMAGES:-0}" == "1" ]]; then
  log "pull skipped (DRAINSENSE_LOCAL_IMAGES=1)"
else
  log "pull"
  docker compose pull --quiet
fi

healthy() {
  local service="$1" id state
  id="$(docker compose ps -q "$service")"
  [[ -n "$id" ]] || return 1
  state="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo missing)"
  [[ "$state" == "healthy" ]]
}

wait_healthy() {
  for _ in $(seq 1 40); do
    if healthy drainsense-api && healthy drainsense-web; then return 0; fi
    sleep 3
  done
  return 1
}

log "start $(grep -E '^DRAINSENSE_(API|WEB)_IMAGE=' .env | cut -d= -f2 | tr '\n' ' ')"
docker compose up -d --remove-orphans
if ! wait_healthy; then
  docker compose logs --tail 60 drainsense-api drainsense-web || true
  if [[ -f .env.previous ]]; then
    log "not healthy: starting the previous images again"
    cp .env .env.failed
    cp .env.previous .env
    docker compose up -d --remove-orphans
    wait_healthy || true
  fi
  fail "DrainSense containers did not become healthy; see the logs above. The failed image list is in .env.failed."
fi
cp .env .env.previous

log "route ${site} through ${EDGE_PROJECT}'s Caddy"
sed "s|__SITE__|${site}|g" drainsense.caddy | docker exec -i "$caddy_id" sh -c 'mkdir -p /config/sites && cat > /config/sites/drainsense.caddy'

caddyfile="${EDGE_DIR}/Caddyfile"
backup=""
if ! grep -qxF "$IMPORT_LINE" "$caddyfile"; then
  backup="${caddyfile}.before-drainsense.$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$caddyfile" "$backup"
  # Append in place: the Caddyfile is a single-file bind mount, and a new inode would not reach the container.
  printf '\n# Sites added by other stacks (DrainSense writes /config/sites/drainsense.caddy).\n%s\n' "$IMPORT_LINE" >> "$caddyfile"
  log "added '${IMPORT_LINE}' to ${caddyfile} (backup ${backup})"
fi

# Validate the host's Caddyfile as Caddy will read it: same image, environment and config volume.
config_volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Name}}{{end}}{{end}}' "$caddy_id")"
caddy_image="$(docker inspect -f '{{.Config.Image}}' "$caddy_id")"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$caddy_id" | grep -v '^$' > "$scratch/caddy.env"
if ! docker run --rm --network none --env-file "$scratch/caddy.env" \
    -v "${caddyfile}:/etc/caddy/Caddyfile:ro" -v "${config_volume}:/config:ro" "$caddy_image" \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >"$scratch/validate.log" 2>&1; then
  cat "$scratch/validate.log"
  [[ -n "$backup" ]] && cat "$backup" > "$caddyfile"
  docker exec "$caddy_id" rm -f /config/sites/drainsense.caddy
  fail "Caddy rejected the config, so it was not applied. ${caddyfile} is back to how it was."
fi

if docker exec "$caddy_id" cat /etc/caddy/Caddyfile | cmp -s - "$caddyfile"; then
  docker exec "$caddy_id" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  log "Caddy reloaded"
else
  log "${EDGE_PROJECT}'s Caddy reads an older copy of ${caddyfile} (the file was replaced after it started): recreating that container"
  compose_files=()
  IFS=',' read -r -a listed <<< "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$caddy_id")"
  for file in "${listed[@]}"; do compose_files+=(-f "$file"); done
  (cd "$EDGE_DIR" && docker compose -p "$EDGE_PROJECT" "${compose_files[@]}" up -d --no-deps --force-recreate caddy)
  caddy_id="$(docker ps -q --filter "label=com.docker.compose.project=${EDGE_PROJECT}" \
    --filter "label=com.docker.compose.service=caddy" | head -1)"
  docker exec "$caddy_id" cat /etc/caddy/Caddyfile | cmp -s - "$caddyfile" \
    || fail "The recreated Caddy still does not see ${caddyfile}."
  log "Caddy recreated and reading the current Caddyfile"
fi

docker image prune -f >/dev/null
docker compose ps
