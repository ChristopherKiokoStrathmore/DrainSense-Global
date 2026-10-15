# backend/ (Dennis · branch `backend-dennis`)

Django + DRF service for Ngao · MvuaSmart. It owns the **REST API** (`/ngao/v1`, including the
USSD contract Louisa calls), the **alert engine**, **Africa's Talking SMS** (send, delivery reports,
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
├── sms/           providers (Africa's Talking, Wasiliana fallback, console), queue dispatcher, webhooks, keywords
├── reply_reader/  OCI GenAI client, §2.4 JSON validation, keyword + number fallback
├── ussd_api/      §5 endpoints for the ussd/ service
├── routing/       greedy dryer router, bookings, operator stop lists
├── postman/       Postman collection + environments + openapi.yaml (generated: manage.py export_postman)
├── tests/         158 tests (pass on SQLite)
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
venv/bin/python manage.py test tests       # 158 tests
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
| | `POST /demo/inbound-sms` `{"phone","text"}` | additive | the reply path without a shortcode callback |
| Louisa (`USSD_SERVICE_TOKEN`) | `POST /ussd/language`, `/ussd/register`, `/ussd/report`, `GET /ussd/ward-status`, `POST /ussd/unsubscribe` | §5 | DB only, no GenAI; 0.1–0.3 s measured against local Oracle 23ai (not yet timed on ADB) |
| Africa's Talking (public) | `POST /sms/delivery`, `POST /sms/inbound` | §3 | form-encoded; always `200`; `?k=<WEBHOOK_SECRET>` when set |
| Public | `GET /public/scorecard`, `/public/calibration`, `GET /health`, `/docs/`, `/redoc/`, `/schema` | §2.3 | |

Errors always use the §2.1 body, with `request_id` matching the `X-Request-ID` response header.

## How the moving parts fit

```text
RISK_SCORES (Trevor, or /jobs/score) ──ACT──► alert engine ──► ALERTS PENDING_APPROVAL (one row per subscriber)
                                                                    │ officer approves (APEX page 3 or REST)
                                                                    ▼
                         one SMS (consent, ACTIVE, audience role, 48 h cooldown, 3/week cap)
                                                                    ▼
              ALERTS.MESSAGE_ID ──► MESSAGES QUEUED ──dispatcher──► Africa's Talking ──► SENT ──DLR──► DELIVERED/FAILED
AT incoming SMS ──► MESSAGES IN ──► keywords (STOP, LUGHA/LANG, 1/NDIYO, RVF) ──► else reply reader ──► REPORTS
REPORTS wet_bags ──► dryer router (wet bags × P(rain 48 h) × aflatoxin) ──► ROUTES/STOPS/BOOKINGS + A5 SMS
```

Safety rules built in:
- **Watch/Abstain never send SMS.** Replay scores (`is_replay='Y'`) and approvals older than
  `ALERT_MAX_AGE_HOURS` are never sent, even if someone approves them.
- **Synthetic subscribers never reach the SMS provider** (`is_demo='Y'` → `SENT` with
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
2. Do **not** run [`db/migrations/001_backend_alignment.sql`](../db/migrations/001_backend_alignment.sql)
   against this database. The backend already targets the live columns. See
   [Live schema compatibility](#live-schema-compatibility).
3. `python manage.py check_schema`: every table/column the backend uses, the CHECK constraints, and
   timestamps the thin driver cannot read. `JOB_RUNS` missing is a warning, not a failure.
   **Fix anything it reports as an error before the demo.**
4. `python manage.py seed_reference` inserts missing `ALERT_TYPES` rows (never changes existing ones).
   Add `--templates` only if `ALERT_TEMPLATE` has no rows for our hazards (see below).
5. Put HTTPS in front (Caddy: `ngao-api.example.org { reverse_proxy 127.0.0.1:8000 }`), open 443 in the
   VCN security list, set `DJANGO_ALLOWED_HOSTS`.
6. Africa's Talking (see [SMS: Africa's Talking](#sms-africas-talking) below): set `SMS_BACKEND=africastalking`,
   `AT_USERNAME`, `AT_API_KEY`, `DEMO_PHONES=<presenter phones>`, and the two callback URLs in the dashboard.

## SMS: Africa's Talking

Outbound SMS, delivery reports and incoming SMS use **Africa's Talking** (`sms/providers/africastalking.py`).
Wasiliana stays available as a fallback (`SMS_BACKEND=wasiliana`). USSD is unchanged: Louisa's `ussd/`
service still takes the **Wasiliana** USSD callback. Africa's Talking USSD uses the same `CON`/`END` text if the team
moves it later.

| Step | Sandbox (rehearsals, free) | Live (the pitch) |
|---|---|---|
| App | the built-in sandbox app | your app on account.africastalking.com |
| `.env` | `AT_USERNAME=sandbox`, `AT_API_KEY=<sandbox key>` | `AT_USERNAME=<app username>`, `AT_API_KEY=<live key>` |
| Sender | default (`AT_SENDER_ID` empty) | registered sender ID or shortcode in `AT_SENDER_ID`; default sender until approved |
| Where SMS land | the AT **simulator** (simulator.africastalking.com, "launch" with your number) | real phones (needs SMS credit) |
| Delivery reports | SMS → Delivery Reports callback: `https://<host>/ngao/v1/sms/delivery?k=<WEBHOOK_SECRET>` | same |
| Farmer replies | create a **sandbox shortcode**, set its incoming-messages callback to `https://<host>/ngao/v1/sms/inbound?k=<WEBHOOK_SECRET>`, reply from the simulator | a shortcode (dedicated, or shared + keyword) with the same callback |

How the adapter behaves:
- **Send:** a form POST to `/version1/messaging` (`username`, `to`, `message`, `bulkSMSMode=1`, optional `from`). The recipient's `messageId` is stored as `MESSAGES.provider_msg_id`.
- **Send results:** status codes 100/101/102 count as accepted. 500/501/502 are retried until `SMS_RETRY_HOURS` runs out. Anything else (invalid number, blacklist, `InvalidSenderId`, insufficient balance) marks the message `FAILED`.
- **Delivery reports:** `Success` → `DELIVERED`, `Failed`/`Rejected` → `FAILED`. `Sent`/`Submitted`/`Buffered` change nothing.
- **Incoming SMS:** form fields `from`, `to`, `text`, `id`, `linkId`. Keywords run first, then the reply reader.
- **Callback security:** Africa's Talking does not sign callbacks, so set `WEBHOOK_SECRET` and keep `?k=` in both URLs.
- **Network:** the callbacks need a public HTTPS URL (the VM, or `ngrok http 8000` for a laptop demo).

### For Chris (APEX)
- The **Alert Approvals** page works unchanged: it lists `V_ALERT_QUEUE` where `status='PENDING_APPROVAL'`.
  When it sets one row to `APPROVED`, the scheduler (or `POST /jobs/dispatch`) sends that subscriber's
  SMS, sets `ALERTS.MESSAGE_ID`, then `SENT` / `sent_at`. Each row already has `subscriber_id`,
  `score_id`, `ward_id`, `alert_type` and a `body` of at most 160 characters, which is what the view joins.
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
1. **Africa's Talking sender ID and shortcode.** An alphanumeric sender ID needs approval, which can take days. Until
   then SMS go out from AT's default sender. Farmer replies need a shortcode with the incoming-messages callback.
   Until one exists, `/demo/inbound-sms` and USSD menu 2 cover the demo. The send call matches the official
   `africastalking` Python SDK. The response and callback field names follow AT's documented shapes,
   because developers.africastalking.com blocked automated reads. Every raw callback body is logged, so
   check the first real delivery report in the log.
2. **Wasiliana** remains only as a fallback; its field names were never confirmed.
3. `check_schema` against the **live** ADB compares the models to the columns that are already there.
   Do not apply `db/migrations/001_backend_alignment.sql` to make the check pass.
4. OCI GenAI: set `REPLY_READER=genai`, `OCI_COMPARTMENT_ID`, and test the 10 Swahili replies
   (EXECUTION_PLAN). The keyword fallback handles the demo sentence on its own (confidence 0.8).
5. The heuristic scorer and all synthetic data are labelled as such. Copy in `alerts/catalogue.py`
   needs a native-speaker check (API_CONTRACT §4).

## Live schema compatibility

The backend writes the **live** NgaoDB tables. It does not need `db/migrations/001_backend_alignment.sql`,
and that script must not be run on the live database (schema `WKSP_NGAO`). The script would make
`ALERTS.SUBSCRIBER_ID` and `SCORE_ID` nullable, widen `BODY`, add `MESSAGES.ALERT_ID` and create
`JOB_RUNS`. None of that matches the dashboard.

What the live tables require, and what the backend does:

- **One `ALERTS` row per subscriber.** `SUBSCRIBER_ID`, `SCORE_ID`, `WARD_ID` and `ALERT_TYPE` are
  `NOT NULL` foreign keys. `draft_alerts` looks up the `RISK_SCORES` row for
  `(ward_id, score_date, alert_type, is_replay)` and creates it only when that key is missing.
  It does not overwrite a row another model already wrote.
- **`RISK_SCORES.DECISION` is `NOT NULL VARCHAR2(7)`.** The values the views already use are
  `ACT`, `WATCH` and `ABSTAIN` (`V_WARD_RISK_LATEST` colours those three). Every row this backend
  inserts sets one of them. A score below the watch threshold is not stored. An alert that has to
  create the missing score stores `ACT`.
- **`RISK_SCORES.IS_REPLAY` is `CHAR(1)`, nullable, and part of the unique key.** Oracle does not
  treat `NULL` as equal to `'N'`, and `V_WARD_RISK_LATEST` / `V_GARISSA_REPLAY` filter on `'N'` and
  `'Y'`. Writes are always `Y` or `N`. A non-replay lookup also matches a `NULL` flag and stores
  `N`, so a second row is not inserted beside it.
- **Language is `SUBSCRIBERS.PREFERRED_LANG` (`CHAR(2) NOT NULL`).** USSD language, USSD
  registration and SMS `LUGHA`/`LANG` set it (`sw` or `en`). `LANGUAGE` is the nullable legacy
  column and is kept in sync, but SMS and USSD read `PREFERRED_LANG`. Trailing spaces from
  `CHAR` padding are stripped on read.
- **`V_ALERT_QUEUE` inner-joins `SUBSCRIBERS`, `WARDS` and `RISK_SCORES`.** A row missing any of
  those never shows on the Alert Approvals page. Status values the page already reads and writes
  are `PENDING_APPROVAL`, `APPROVED`, `REJECTED` and `SENT`.
- **`ALERTS.BODY` is `VARCHAR2(160)`.** Rendered copy is cut to 160 characters and 160 UTF-8 bytes
  before insert. The SMS that goes out is that same body.
- **The SMS link is `ALERTS.MESSAGE_ID`.** Live `MESSAGES` has no `alert_id` column. Outbound SMS
  and USSD transcripts are still normal `MESSAGES` rows (`DIRECTION`, `CHANNEL`, `PHONE`, `BODY`,
  `STATUS`).
- **There is no `JOB_RUNS` table.** `/jobs/*` still runs and returns `job_id` / `status` / `summary`.
  The row is stored when the table exists (local SQLite). Otherwise it is kept in the process, and
  the scheduler cannot tell that today's job already ran after a restart.
- **SMS and the delivery/inbound callbacks stay on Africa's Talking** (`SMS_BACKEND=africastalking`
  for a real send). Wasiliana remains an optional fallback only. USSD session callbacks are still
  Louisa's service; the backend logs USSD reports as `MESSAGES.CHANNEL='USSD'`.
- **`RISK_SCORES.RISK_LEVEL` (`VARCHAR2(10)`, nullable) and `SUBSCRIBERS.COOP_ID` (nullable) are
  not written.** Inserts omit them. The ward map colours by `RISK_LEVEL` (`HIGH` / `MEDIUM` /
  `UNCERTAIN` / `LOW`); the view's `COLOUR` column is the decision `CASE`. There is no mapping
  from `ACT` / `WATCH` / `ABSTAIN` to `RISK_LEVEL` in this repo.

`tests/test_alerts.py` runs the same joins as `V_ALERT_QUEUE` on SQLite, including a row whose
subscriber id matches nobody, which the join drops.

## Testing against a real Oracle database
SQLite does not enforce `VARCHAR2` lengths or time-zone storage, and both have caught real bugs here.
Before merging, run the suite on Oracle 23ai Free:

```bash
docker run -d --name ngao-oracle -p 1521:1521 -e ORACLE_PASSWORD=<pw> gvenzl/oracle-free:23-slim-faststart
DB_ENGINE=oracle DB_DSN=localhost:1521/FREEPDB1 DB_USER=system DB_PASSWORD=<pw> python manage.py test tests
```
