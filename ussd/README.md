# ussd/ (Louisa · branch `ussd-louisa`)

Owns the **Wasiliana USSD callback**, the menu state machine, and `USSD_SESSIONS`.

Wasiliana is callback-only: they POST each session to our callback URL. This service replies with `text/plain` prefixed `CON ` (continue) or `END ` (end) within about 10 seconds. No OCI GenAI call on this path.

It does **not** send SMS and it does **not** write `SUBSCRIBERS`, `MESSAGES`, or `REPORTS`. Those go through the backend contract in [docs/API_CONTRACT.md §5](../docs/API_CONTRACT.md#5-ussd-to-backend-contract):

| Call | When |
|---|---|
| `POST /ngao/v1/ussd/register` | Menu 1 finishes with consent |
| `POST /ngao/v1/ussd/report` | Menu 2 collects a structured report |
| `GET /ngao/v1/ussd/ward-status` | Menu 3, "Hali ya kata yangu" |
| `POST /ngao/v1/ussd/unsubscribe` | The farmer stops |

Auth: `Authorization: Bearer <USSD_SERVICE_TOKEN>`. Each hop also upserts `USSD_SESSIONS` (`session_id`, `phone`, `last_text`, `updated_at`).

**Confirm exact USSD request field names against Wasiliana docs ([docs.wasiliana.com](https://docs.wasiliana.com)).** Parse them in one adapter. The handler needs a session id, the phone number, and the text entered so far.

```text
ussd/
├── README.md
├── callback.py       POST /ussd → CON / END
├── menus.py          Jiandikishe, Ripoti, Hali, Lugha
├── wasiliana.py      request adapter (field names confirmed against the docs)
└── .env.example      USSD_PUBLIC_URL, USSD_SERVICE_TOKEN, BACKEND_BASE_URL (no real values)
```

The callback must be a public HTTPS URL (ngrok for the demo, or the host from decision D2). Point that URL at Wasiliana's USSD service settings.
