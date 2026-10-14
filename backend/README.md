# backend/ (role C · Dennis · branch `backend-dennis`)

Owns: Africa's Talking **USSD + SMS webhooks**, outbound SMS sender (dequeues `MESSAGES`), **reply reader** (OCI GenAI → JSON), **alert engine** wiring, **dryer router**, action endpoints `/ngao/v1/jobs/*`.

Writes to the shared ADB schema `NGAO` ([db/schema.sql](../db/schema.sql)); payloads and paths are fixed in [docs/API_CONTRACT.md](../docs/API_CONTRACT.md).

**Open decision D1:** Django/DRF service (`python-oracledb` → ADB, hosted on an OCI Always Free VM) **or** ORDS handlers + PL/SQL inside ADB. Suggested layout if Django:
```text
backend/
├── manage.py
├── ngao_core/        settings, urls (/api/v1)
├── ussd/             POST /ussd: menu state machine
├── sms/              /sms/inbound, /sms/delivery, outbound sender
├── reply_reader/     OCI GenAI prompt + JSON schema validation
├── alerts/           calls PKG_ALERT_ENGINE or Python engine
├── routing/          greedy dryer router
├── requirements.txt
└── .env.example      AT_USERNAME, AT_API_KEY, AT_SENDER_ID, WEBHOOK_SECRET, DB_DSN, OCI_* (no real values)
```
If ORDS: put handler SQL in `backend/ords/` and PL/SQL packages in `db/`.
Run locally: `python -m venv .venv && pip install -r requirements.txt && python manage.py runserver`, expose with ngrok, set AT sandbox callback URLs.
