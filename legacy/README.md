# Archived NGAO pieces

DrainSense does not run on Oracle APEX. These paths were moved off the active tree so the flood desk, the FastAPI service, and CI do not depend on them.

| Path | What it was |
|---|---|
| `ngao-apex-frontend/` | Oracle APEX app export and PWA notes (`frontend/` in NGAO) |
| `oracle-db/` | Autonomous Database schema, deploy SQL, seeds |
| `ngao-vm-deploy/` | Oracle VM / Caddy deploy scripts |
| `ORACLE_APEX.md` | APEX, ORDS, and Select AI notes |
| `deploy-ngaodb.yml`, `deploy-vm.yml` | GitHub Actions that deployed NgaoDB and the VM. They are not in `.github/workflows/`, so they do not run here. |

The Django API, USSD service, and seasonal-outlook code stay in `backend/` and `ussd/` because the flood model reuses `HEAVY_RAIN_MM` from `backend/scoring/heuristics.py`. They are the NGAO services, not the DrainSense API.

NGAO's own README is the `README.md` at the history-import commit. `git show f8ec981:README.md` prints it (the import merge keeps that blob).
