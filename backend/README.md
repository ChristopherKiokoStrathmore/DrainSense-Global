# backend/ (Dennis · branch `backend-dennis`)

Django + DRF service for Ngao · MvuaSmart. It owns the **REST API** (`/ngao/v1`, including the
USSD contract Louisa calls), the **alert engine**, **Wasiliana SMS** (send, delivery reports,
inbound), the **reply reader** (OCI Generative AI → JSON, with a keyword fallback), the **dryer
router**, scoring/replay fallbacks and the public scorecard. It writes the shared ADB schema `NGAO`
([db/schema.sql](../db/schema.sql)); paths and payloads follow [docs/API_CONTRACT.md](../docs/API_CONTRACT.md).

**Decision D1:** Django/DRF on an OCI Always Free VM, `python-oracledb` thin mode to Autonomous
Database. No Instant Client, no Django tables in ADB (no `migrate` there).

```text
backend/
├── manage.py
├── ngao_core/     settings, URLs (/ngao/v1 and /api/v1), bearer auth, §2.1 errors, request ids, time/phone utils
├── ngao/          models for every NGAO table, demo data, reset-demo, management commands
├── api/           §2.2 jobs (JOB_RUNS) and §2.3 reads, alert approval endpoints
├── alerts/        alert catalogue + sw/en copy, template rendering, alert engine, grading + scorecard
├── scoring/       heuristic-v0 fallback scorer and the Garissa 2006 replay (Trevor's models replace it)
├── sms/           the one Wasiliana adapter, MESSAGES queue dispatcher, webhooks, SMS keywords
├── reply_reader/  OCI GenAI client, §2.4 JSON validation, keyword + number fallback
├── ussd_api/      §5 endpoints for the ussd/ service
├── routing/       greedy dryer router, bookings, operator stop lists
├── postman/       Postman collection + environments + openapi.yaml (generated: manage.py export_postman)
├── tests/         135 tests (pass on SQLite; the 129 pre-docs tests also pass on Oracle 23ai)
├── requirements.txt · .env.example · Dockerfile · docker-compose.yml
```

## Run it locally (5 minutes)

```bash
cd backend
python -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env            # set DJANGO_DEBUG=true for local work
venv/bin/python manage.py migrate          # SQLite only; never against ADB
venv/bin/python manage.py seed_demo        # SYNTHETIC wards, forecasts, subscribers, dryers
venv/bin/python manage.py runserver
venv/bin/python manage.py test tests       # 135 tests
```

With `SMS_BACKEND=console` every SMS is logged instead of sent. Then walk through the demo script:

```bash
B=http://127.0.0.1:8000/ngao/v1; A="Authorization: Bearer change-me-apex"; U="Authorization: Bearer change-me-ussd"
curl -H "$U" -H 'Content-Type: application/json' -d '{"phone":"+254711000001","preferred_lang":"sw"}' $B/ussd/language
curl -H "$U" -H 'Content-Type: application/json' -d '{"phone":"+254711000001","ward_id":12,"role":"farmer","consent":true}' $B/ussd/register
curl -H "$A" -H 'Content-Type: application/json' -d '{}' $B/jobs/score          # Moiben A1 ACT, Kapseret A2 ACT
curl -H "$A" -H 'Content-Type: application/json' -d '{}' $B/jobs/send-alerts    # drafts PENDING_APPROVAL
curl -H "$A" "$B/alerts?status=PENDING_APPROVAL"                                  # officer approves in APEX, or:
curl -H "$A" -H 'Content-Type: application/json' -d '{"approved_by":"me"}' $B/alerts/<id>/approve
curl -H "$A" -H 'Content-Type: application/json' -d '{"phone":"+254711000001","text":"Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu"}' $B/demo/inbound-sms
curl -H "$A" -H 'Content-Type: application/json' -d '{}' $B/jobs/plan-routes    # booking SMS, "saa 2 asubuhi"
curl -H 'Content-Type: application/json' -d '{"phone":"254711000001","message":"1"}' $B/sms/inbound   # farmer confirms
```

## API docs: Swagger UI, ReDoc, Postman

| What | URL / file |
|---|---|
| **Swagger UI** (try every call, *Authorize* with a token) | `http://<host>/ngao/v1/docs/` (`/` redirects here) |
| ReDoc (read-only reference) | `http://<host>/ngao/v1/redoc/` |
| OpenAPI 3 schema | `http://<host>/ngao/v1/schema` (YAML), `?format=json`; static copy `postman/openapi.yaml` |
| **Postman collection** | `postman/ngao-backend.postman_collection.json` + `ngao-local` / `ngao-oci` environments |

The docs are generated from the code (drf-spectacular). Every endpoint carries a summary, a
description, request/response schemas, contract examples and its error responses. Swagger UI and
ReDoc assets are bundled (no CDN), so the docs work on venue Wi-Fi. Set `API_DOCS_PUBLIC=false` to
require an API token. `tests/test_openapi.py` checks that the schema builds with no warnings and
that real responses for the whole demo flow match it.

**Postman.** Import the collection and an environment, set `api_token`/`ussd_token` to your `.env`
values, then run **0. Demo walkthrough** (Collection Runner) top to bottom. Its test scripts capture
`alert_id`, `report_id`, `booking_id` and `job_id`, so the reference folders (one per API area) work
right after. From the shell:

```bash
npx newman run postman/ngao-backend.postman_collection.json -e postman/ngao-local.postman_environment.json
#  -> 57 requests, 78 assertions, 0 failures against a fresh `seed_demo` server
```

Regenerate after any API change (add `--capture-from` to save real responses as examples):

```bash
python manage.py export_postman --capture-from http://127.0.0.1:8000/ngao/v1 --api-token change-me-apex --ussd-token change-me-ussd
```

## Endpoints

Served under **`/ngao/v1/`** (what `ussd/` calls) and **`/api/v1/`** (the contract's Django base URL).

| Caller | Method + path | Contract | Notes |
|---|---|---|---|
| APEX / scheduler | `POST /jobs/score` | §2.2 | fallback scorer; never overwrites another `model_version` unless `"overwrite":true` |
| | `POST /jobs/send-alerts` | §2.2 | drafts `ALERTS` (`PENDING_APPROVAL`), fans out anything already `APPROVED` |
| | `POST /jobs/plan-routes` | §2.2 | `{"route_date"}` default tomorrow; 409 once a booking is confirmed |
| | `POST /jobs/replay` | §2.2 | `garissa_rvf_2006`; returns first ACT date and lead days vs 30 Nov 2006 |
| | `POST /jobs/reset-demo` | §2.2 | presenter phones (`DEMO_PHONES`), today's alerts, routes from today |
| | `POST /jobs/dispatch`, `POST /jobs/grade-outcomes`, `GET /jobs/{id}/status` | additive | |
| | `GET /wards`, `/wards/{id}/risk`, `/reports`, `PATCH /reports/{id}`, `GET /routes`, `POST /bookings/{id}/confirm` | §2.3 | |
| | `GET /alerts`, `POST /alerts/{id}/approve`, `/reject` | additive | same effect as the APEX Alert Approvals page |
| | `POST /demo/inbound-sms` `{"phone","text"}` | additive | the reply path without Wasiliana forwarding |
| Louisa (`USSD_SERVICE_TOKEN`) | `POST /ussd/language`, `/ussd/register`, `/ussd/report`, `GET /ussd/ward-status`, `POST /ussd/unsubscribe` | §5 | DB only, no GenAI; 0.1–0.3 s measured against local Oracle 23ai (not yet timed on ADB) |
| Wasiliana (public) | `POST /sms/delivery`, `POST /sms/inbound` | §3.3–3.4 | always `200`; `?k=<WEBHOOK_SECRET>` when set |
| Public | `GET /public/scorecard`, `/public/calibration`, `GET /health`, `/docs/`, `/redoc/`, `/schema` | §2.3 | |

Errors always use the §2.1 body, with `request_id` matching the `X-Request-ID` response header.

## How the moving parts fit

```text
RISK_SCORES (Trevor, or /jobs/score) ──ACT──► alert engine ──► ALERTS PENDING_APPROVAL (one per score × language)
                                                                    │ officer approves (APEX page 3 or REST)
                                                                    ▼
          per-subscriber fan-out (consent, ACTIVE, audience role, 48 h cooldown, 3/week cap)
                                                                    ▼
                         MESSAGES QUEUED ──dispatcher (SKIP LOCKED)──► Wasiliana ──► SENT ──DLR──► DELIVERED/FAILED
Wasiliana inbound ──► MESSAGES IN ──► keywords (STOP, LUGHA/LANG, 1/NDIYO, RVF) ──► else reply reader ──► REPORTS
REPORTS wet_bags ──► dryer router (wet bags × P(rain 48 h) × aflatoxin) ──► ROUTES/STOPS/BOOKINGS + A5 SMS
```

Safety rules built in:
- **Watch/Abstain never send SMS.** Replay scores (`is_replay='Y'`) and approvals older than
  `ALERT_MAX_AGE_HOURS` are never sent, even if someone approves them.
- **Synthetic subscribers never reach Wasiliana** (`is_demo='Y'` → `SENT` with
  `provider_msg_id='SIMULATED'`). Presenter phones in `DEMO_PHONES` are sent for real.
- **AI only reads.** The GenAI output must match the §2.4 schema exactly. `confidence < 0.7`,
  `other`, any failure or AI-detected `stop`/`booking_confirm` → `NEEDS_REVIEW`. Actions only follow exact keywords.

## Processes

| Process | Command | When |
|---|---|---|
| API | `gunicorn ngao_core.wsgi:application -b 0.0.0.0:8000 -w 2 --threads 4` | always |
| Scheduler | `python manage.py scheduler` | the README daily cycle: 06:00 send-alerts, 18:00 plan-routes, Mon grading; drains the SMS queue every 15 s |
| SMS worker | `python manage.py sms_worker` | only if `SMS_DISPATCH=worker` (otherwise the API sends from a background thread) |
| One-off | `python manage.py run_job send-alerts --param dry_run=true` | cron, debugging |

`docker compose up -d` runs the API and the scheduler from the same image.

## Deploy against Autonomous Database

1. Download the ADB wallet, unzip it to `backend/wallet/` (git-ignored). Set `DB_ENGINE=oracle`,
   `DB_DSN=ngaodb_low`, `DB_USER`, `DB_PASSWORD`, `DB_WALLET_DIR`, `DB_WALLET_PASSWORD`. The APEX app
   parses schema **`WKSP_NGAO`**: if you log in as another user, set `DB_SCHEMA=WKSP_NGAO`.
2. Bring the schema up to date (re-runnable, keeps data): run
   [`db/migrations/001_backend_alignment.sql`](../db/migrations/001_backend_alignment.sql) in SQL Worksheet.
3. `python manage.py check_schema`: every table/column the backend uses, the CHECK constraints, and
   timestamps the thin driver cannot read. **Fix anything it reports before the demo.**
4. `python manage.py seed_reference` inserts missing `ALERT_TYPES` rows (never changes existing ones).
   Add `--templates` only if `ALERT_TEMPLATE` has no rows for our hazards (see below).
5. Put HTTPS in front (Caddy: `ngao-api.example.org { reverse_proxy 127.0.0.1:8000 }`), open 443 in the
   VCN security list, set `DJANGO_ALLOWED_HOSTS`.
6. In the Wasiliana dashboard set the delivery-report and inbound callbacks to
   `https://<host>/ngao/v1/sms/delivery?k=<WEBHOOK_SECRET>` and `.../sms/inbound?k=<WEBHOOK_SECRET>`.
7. `SMS_BACKEND=wasiliana`, `WASILIANA_API_KEY`, `WASILIANA_SENDER_ID`, `DEMO_PHONES=<presenter phones>`.
   Send one SMS to yourself with `run_job dispatch` after queueing.

### For Chris (APEX)
- The **Alert Approvals** page works unchanged: when it sets `status='APPROVED'`, the scheduler (or
  `POST /jobs/dispatch`) fans the alert out and sets `SENT`/`sent_at`. Alerts carry `hazard`,
  `severity`, `lang`, `body`, so `v_alert_queue` can read them directly.
- Template lookup is `ALERT_TEMPLATE (hazard, severity, lang)`. We map alert types to hazards with
  `NGAO_TEMPLATE_HAZARDS` (default `A1→dry_window`, `A2→heavy_rain`, `B3→rvf`, see
  `alerts/catalogue.py`) and ACT → severity `warning`. **Set the env var to your 6 hazard names**
  and the officer-edited copy is used. Without a match we fall back to `ALERT_TYPES.template_*`,
  then to the built-in copy.
- Demo-control buttons can call REST from PL/SQL (ADB needs an ACL for the host, HTTPS only; keep the
  token in an APEX Web Credential, not in page code):
  ```sql
  l_clob := apex_web_service.make_rest_request(
      p_url => 'https://<host>/ngao/v1/jobs/plan-routes', p_http_method => 'POST',
      p_body => '{}', p_credential_static_id => 'NGAO_API');  -- HTTP header "Authorization: Bearer ..."
  ```

### For Louisa (USSD)
Base URL `https://<host>/ngao/v1`, header `Authorization: Bearer <USSD_SERVICE_TOKEN>`. Phones may be
sent as `07…`, `2547…` or `+2547…`; responses use E.164. `ward-status` always answers `200` with a
`text` line in the subscriber's language (also for unregistered phones, `"registered": false`).
4xx bodies use §2.1; show `END Samahani, jaribu tena.` on any error.

### For Trevor (data)
`/jobs/score` writes `model_version='heuristic-v0-backend'` and **skips any row written by another
model_version** (counted as `kept_existing`). Your trained scores win automatically. The scorer only
reads features with `available_date <= as_of`.

## Open items (confirm before the demo)
1. **Wasiliana field names.** The send call, `apiKey` header and DLR keys (`correlator`,
   `deliveryStatus`, `failure_reason`) come from the `pytek-wasiliana` client, not the docs
   site (docs.wasiliana.com is a JavaScript app that did not render). The adapter
   (`sms/wasiliana.py`) logs every raw callback body; adjust `WASILIANA_*` env vars, not code. Whether
   `deliveryStatus "0"` means delivered is an **assumption**.
2. **Inbound SMS forwarding** is still unconfirmed. `/demo/inbound-sms` and USSD menu 2 cover the demo either way.
3. `check_schema` against the **live** ADB: the APEX export shows `ALERTS` with
   `hazard/severity/lang/body/approved_*/sent_at`. Its exact DDL was not in git, so run the migration and the check.
4. OCI GenAI: set `REPLY_READER=genai`, `OCI_COMPARTMENT_ID`, and test the 10 Swahili replies
   (EXECUTION_PLAN). The keyword fallback handles the demo sentence on its own (confidence 0.8).
5. The heuristic scorer and all synthetic data are labelled as such. Copy in `alerts/catalogue.py`
   needs a native-speaker check (API_CONTRACT §4).

## Testing against a real Oracle database
SQLite does not enforce `VARCHAR2` lengths or time-zone storage, and both have caught real bugs here.
Before merging, run the suite on Oracle 23ai Free:

```bash
docker run -d --name ngao-oracle -p 1521:1521 -e ORACLE_PASSWORD=<pw> gvenzl/oracle-free:23-slim-faststart
DB_ENGINE=oracle DB_DSN=localhost:1521/FREEPDB1 DB_USER=system DB_PASSWORD=<pw> python manage.py test tests
```
