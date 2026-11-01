# Deploying DrainSense

Production: **https://drainsense.158.180.57.75.sslip.io** (desk), Swagger UI at
**https://drainsense.158.180.57.75.sslip.io/docs**. It runs on the Oracle VM `158.180.57.75`
(Ubuntu, x86_64), next to NGAO, which stays at https://158.180.57.75.sslip.io.

sslip.io resolves any name ending in the VM's IP to that IP, so the DrainSense hostname needs no
DNS record, and Caddy gets its own Let's Encrypt certificate for it.

## How it runs on the VM

| Piece | Where | Notes |
|---|---|---|
| `drainsense-api` | Compose project `drainsense`, `/opt/drainsense` | FastAPI on :8000, no host port. SQLite log in the `drainsense_api-data` volume. |
| `drainsense-web` | same | Next.js desk on :3000, no host port. Calls the API on its own origin. |
| HTTPS and routing | NGAO's Caddy (`ngao-caddy-1`, `/opt/ngao`) | Owns :80/:443. Reads `/config/sites/drainsense.caddy` through one `import` line in `/opt/ngao/Caddyfile`. `/api/*`, `/docs`, `/redoc`, `/openapi.json` go to the API, everything else to the desk. |

Both DrainSense containers also join `ngao_default`, under names NGAO does not use, so NGAO's
`api` never resolves to DrainSense.

## CI/CD: deploy on every push to main

`.github/workflows/ci.yml` runs on every push and pull request:

1. **test**: pytest, the web build, shellcheck on the deploy script.
2. **images**: builds `drainsense-api` and `drainsense-web`. On `main` it pushes them to
   `ghcr.io/<owner>/drainsense-api` and `drainsense-web` for linux/amd64 and linux/arm64, tagged
   with the commit SHA and `main`.
3. **deploy** (`main` only, GitHub environment `production`): SSH to the VM, upload
   `deploy/oracle-vm/` to `/opt/drainsense`, run `remote-deploy.sh`, then smoke-test the public URL.

### One-time setup (repository owner)

GitHub → the repository → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `ORACLE_VM_HOST` | `158.180.57.75` |
| `ORACLE_VM_USER` | `ubuntu` |
| `ORACLE_VM_SSH_KEY` | the whole private key file used for `ssh -i … ubuntu@158.180.57.75` |
| `ORACLE_VM_KNOWN_HOSTS` | optional but recommended: the output of `ssh-keyscan 158.180.57.75` |
| `ORACLE_VM_SSH_PORT` | optional, default 22 |

Optional repository **variable** `DRAINSENSE_SITE_ADDRESS` overrides the hostname. Without it the
deploy uses `drainsense.<ORACLE_VM_HOST>.sslip.io`.

Optional repository **variable** `CARTO_BASEMAPS_KEY`: a free CARTO basemaps key
(<https://carto.com/basemaps/apikey>), baked into the desk image as `NEXT_PUBLIC_CARTO_KEY`. The map
uses CARTO's vector Dark Matter style, which loads without a key today; CARTO stopped serving its
raster tiles without one on 2026-09-25 and may do the same for vector. The key is visible to every
visitor, so restrict it to the DrainSense hostname in CARTO's dashboard. It is the only key the desk
uses. For a manual build, pass it with `--build-arg NEXT_PUBLIC_CARTO_KEY=…`.

Until the three required secrets exist, the deploy job stops and names the missing ones. Tests and
images still run.

## What `remote-deploy.sh` does on the VM

1. Pulls the images named in `/opt/drainsense/.env` and starts them. If either container is not
   healthy within two minutes, the previous images (`.env.previous`) are started again and the
   deploy fails.
2. Writes `drainsense.caddy` into Caddy's config volume and appends
   `import /config/sites/*.caddy` to `/opt/ngao/Caddyfile` if missing (with a timestamped backup).
3. Validates the host's Caddyfile in a throwaway Caddy. On failure the Caddyfile is restored and
   nothing changes.
4. Reloads Caddy gracefully. If Caddy's single-file bind mount still points at an older copy of the
   Caddyfile (NGAO's own deploy replaces the file), it recreates the Caddy container alone from
   NGAO's Compose files instead.

## Manual deploy (no CI)

This is how the first deploy on 2026-10-29 was done. From the repository root:

```bash
KEY=~/ssh-key-2026-10-15.key; VM=ubuntu@158.180.57.75
tar -czf - --exclude=api/data --exclude='__pycache__' --exclude=web/node_modules --exclude=web/.next \
    .dockerignore api model/artifacts web deploy/oracle-vm \
  | ssh -i $KEY $VM 'rm -rf /opt/drainsense/src && mkdir -p /opt/drainsense/src && tar -xzf - -C /opt/drainsense/src \
      && cp /opt/drainsense/src/deploy/oracle-vm/{docker-compose.yml,drainsense.caddy,remote-deploy.sh} /opt/drainsense/'
ssh -i $KEY $VM 'set -e; cd /opt/drainsense/src; tag=manual-$(date -u +%Y%m%d%H%M)
  docker build -q -f api/Dockerfile -t drainsense-api:$tag .
  docker build -q -t drainsense-web:$tag web
  printf "DRAINSENSE_API_IMAGE=drainsense-api:%s\nDRAINSENSE_WEB_IMAGE=drainsense-web:%s\n" $tag $tag > /opt/drainsense/.env
  printf "\n" | DRAINSENSE_LOCAL_IMAGES=1 bash /opt/drainsense/remote-deploy.sh ubuntu drainsense.158.180.57.75.sslip.io'
```

## Operations

```bash
ssh -i ~/ssh-key-2026-10-15.key ubuntu@158.180.57.75
cd /opt/drainsense
docker compose ps                      # both should be (healthy)
docker compose logs -f --tail 100      # API and desk logs
docker logs ngao-caddy-1 --tail 100    # routing and certificate logs
```

- **Roll back**: `cp .env.previous .env && docker compose up -d` (or put any earlier image tags in `.env`).
- **Take DrainSense offline** without touching NGAO: `docker compose down`, then
  `docker exec ngao-caddy-1 rm /config/sites/drainsense.caddy` and
  `docker exec ngao-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.
- **If NGAO is redeployed** with its archived workflow, its Caddyfile comes back without the
  `import` line and the DrainSense hostname stops answering. The next DrainSense deploy restores it.
