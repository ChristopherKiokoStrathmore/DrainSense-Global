"""OpenAPI 3 / Swagger UI configuration (drf-spectacular). Served at /ngao/v1/docs.

Kept free of Django/DRF imports: settings.py imports SPECTACULAR_SETTINGS from here.
"""

DESCRIPTION = """
Backend for **Ngao · MvuaSmart**: early warnings for floods, Rift Valley fever and aflatoxin,
sent to farmers and herders by SMS/USSD in Kiswahili or English (the subscriber's choice), with
officers approving alerts in the Oracle APEX dashboard. This page is generated from the code and
follows [`docs/API_CONTRACT.md`](https://github.com/ChristopherKiokoStrathmore/MVUA-SMART/blob/main/docs/API_CONTRACT.md).

## Base URLs
The same API is served under two prefixes (pick one in **Servers** above):

| Prefix | Who uses it |
|---|---|
| `/ngao/v1` | the contract's ORDS-style path; Louisa's `ussd/` service, APEX demo controls |
| `/api/v1` | the contract's Django base URL; identical routes |

## Authentication (§2.0)
Click **Authorize** and paste a token (without the word `Bearer`).

| Scheme | Header | Caller | May call |
|---|---|---|---|
| `ApiToken` | `Authorization: Bearer <NGAO_API_TOKENS>` | APEX demo controls, scheduler, tests | everything except the public endpoints |
| `UssdToken` | `Authorization: Bearer <USSD_SERVICE_TOKEN>` | Louisa's USSD service | `/ussd/*` only |
| `WebhookSecret` | `?k=<WEBHOOK_SECRET>` | Wasiliana callbacks | `/sms/delivery`, `/sms/inbound` |
| none | | anyone | `/health`, `/public/*`, these docs |

## Errors (§2.1)
Every error has the same body. `request_id` equals the `X-Request-ID` response header (send your
own `X-Request-ID` to correlate logs).
```json
{"error": {"code": "VALIDATION_ERROR", "message": "ward_id is required",
           "details": {"field": "ward_id"}, "request_id": "8f2c..."}}
```
| code | HTTP |
|---|---|
| `VALIDATION_ERROR` | 400 |
| `UNAUTHORIZED` | 401 |
| `FORBIDDEN` | 403 |
| `NOT_FOUND` | 404 |
| `CONFLICT` | 409 |
| `UPSTREAM_ERROR` | 502 |
| `INTERNAL` | 500 |

Wasiliana webhooks are the exception: they **always** answer `200` with an empty body.

## Conventions
- Phones are E.164 (`+2547XXXXXXXX`); inputs may also be `07…` or `2547…`.
- Times are ISO-8601 with the Nairobi offset (`2026-10-16T08:00:00+03:00`); dates are `YYYY-MM-DD` (EAT).
- `probability` is 0–1 and `confidence_pct` is 0–100. Decisions are `ACT` / `WATCH` / `ABSTAIN`.
- Languages: `sw` (Kiswahili, the default) or `en`.

## The demo in seven calls
1. `POST /ussd/language` + `POST /ussd/register`: the farmer registers by USSD
2. `POST /jobs/score`: score wards → `RISK_SCORES`
3. `POST /jobs/send-alerts` → `GET /alerts?status=PENDING_APPROVAL` → `POST /alerts/{id}/approve`: the SMS goes out
4. `POST /demo/inbound-sms` (or the Wasiliana `POST /sms/inbound`): the farmer replies, *"Nimevuna magunia 20…"*
5. `POST /jobs/plan-routes` → `GET /routes`: a dryer is routed to her collection point
6. `POST /bookings/{id}/confirm` (or an SMS reply of `1`): booking confirmed
7. `POST /jobs/replay`: the Garissa 2006 Rift Valley fever replay

The Postman collection in `backend/postman/` runs exactly this sequence.

## Safety rules the API enforces
- Only `ACT` scores produce SMS, and only after approval (`ALERTS_REQUIRE_APPROVAL=true`).
- Replay alerts and approvals older than 48 h are never sent.
- Synthetic subscribers (`is_demo='Y'`) never reach Wasiliana.
- Generative AI only *reads* replies. Unsure parses go to the officers' review queue.
"""

TAGS = [
    {"name": "Health & docs", "description": "Liveness and configuration summary."},
    {"name": "Jobs", "description": "§2.2 action endpoints for APEX demo controls and the scheduler. "
                                    "Each call runs synchronously, is recorded in `JOB_RUNS`, and answers `202` with a summary."},
    {"name": "Alerts", "description": "Officer approval of drafted alerts. This is the same as the APEX Alert Approvals page, "
                                      "which may also set `ALERTS.status='APPROVED'` directly."},
    {"name": "Wards & risk", "description": "§2.3 reads: wards and today's decisions per ward."},
    {"name": "Reports", "description": "§2.3 reads and officer review of farmer/herder reports (SMS replies and USSD menu 2)."},
    {"name": "Dryer routes", "description": "§2.3 tomorrow's dryer plan and booking confirmation."},
    {"name": "USSD contract", "description": "§5 endpoints called by Louisa's `ussd/` service. Database only, no AI; well under 2 s."},
    {"name": "Wasiliana webhooks", "description": "§3.3–3.4 callbacks from Wasiliana. They always answer `200` with an empty body. "
                                                  "Field names are read by one adapter and can be remapped in `.env`."},
    {"name": "Demo", "description": "Helpers for the Friday demo."},
    {"name": "Public scorecard", "description": "Unauthenticated: every alert graded in public."},
]

SPECTACULAR_SETTINGS = {
    "TITLE": "Ngao · MvuaSmart backend API",
    "DESCRIPTION": DESCRIPTION,
    "VERSION": "1.0.0 (contract v1-draft.4)",
    "CONTACT": {"name": "Dennis (backend-dennis)"},
    "LICENSE": {"name": "Hackathon prototype, GreenTech Hackathon 2026"},
    "SERVERS": [
        {"url": "/ngao/v1", "description": "Contract path (ORDS-style); what ussd/ calls"},
        {"url": "/api/v1", "description": "Django base path (same routes)"},
    ],
    "SCHEMA_PATH_PREFIX": r"/ngao/v1",
    "SCHEMA_PATH_PREFIX_TRIM": True,
    "PREPROCESSING_HOOKS": ["api.openapi.only_ngao_prefix"],
    "TAGS": TAGS,
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "LangEnum": ["sw", "en"],
        "ReviewDecisionEnum": ["ACCEPTED", "CORRECTED", "REJECTED"],
        "ReviewStatusEnum": ["AUTO_ACCEPTED", "NEEDS_REVIEW", "ACCEPTED", "CORRECTED", "REJECTED"],
        "AlertStatusEnum": ["PENDING_APPROVAL", "APPROVED", "REJECTED", "SENT", "SUPPRESSED"],
        "ApprovedAlertStatusEnum": ["APPROVED", "SENT", "SUPPRESSED"],
        "BookingStatusEnum": ["OFFERED", "CONFIRMED", "DRIED", "NO_SHOW"],
        "RouteStatusEnum": ["PLANNED", "NOTIFIED", "DONE"],
        "JobRunStatusEnum": ["RUNNING", "DONE", "FAILED"],
        "HealthStatusEnum": ["ok", "degraded"],
        "DecisionEnum": ["ACT", "WATCH", "ABSTAIN"],
        "ServiceEnum": ["FARM", "HERD", "HEALTH"],
    },
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiToken": {
                "type": "http", "scheme": "bearer",
                "description": "NGAO_API_TOKENS: APEX demo controls, scheduler, router, tests.",
            },
            "UssdToken": {
                "type": "http", "scheme": "bearer",
                "description": "USSD_SERVICE_TOKEN: Louisa's ussd/ service. Only /ussd/* endpoints.",
            },
            "WebhookSecret": {
                "type": "apiKey", "in": "query", "name": "k",
                "description": "WEBHOOK_SECRET appended to the Wasiliana callback URL. "
                               "A wrong secret is ignored silently (still 200).",
            },
        }
    },
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "list",
        "filter": True,
        "tryItOutEnabled": True,
        "deepLinking": True,
        "defaultModelsExpandDepth": 0,
    },
}
