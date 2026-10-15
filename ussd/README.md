# ussd/ (Louisa · branch `ussd-louisa`)

Owns the **USSD callback** (Africa's Talking by default, Wasiliana with `USSD_PROVIDER=wasiliana`), the menu state machine, and `USSD_SESSIONS`.

The gateway is callback-only: Africa's Talking POSTs each hop (form fields `sessionId`, `serviceCode`, `phoneNumber`, `networkCode`, and `text` = every input so far joined by `*`) to the channel's callback URL, `https://<host>/ussd?k=<WEBHOOK_SECRET>`. This service replies with `text/plain` prefixed `CON ` (continue) or `END ` (end) within about 10 seconds. No OCI GenAI call on this path. `gateway.py` picks the provider; `wasiliana.py` parses the shared payload shape.

It does **not** send SMS and it does **not** write `SUBSCRIBERS`, `MESSAGES`, or `REPORTS`. Those go through the backend contract in [docs/API_CONTRACT.md §5](../docs/API_CONTRACT.md#5-ussd-to-backend-contract):

| Call | When |
|---|---|
| `POST /ngao/v1/ussd/register` | Menu 1 finishes with consent |
| `POST /ngao/v1/ussd/report` | Menu 2 collects a structured report |
| `GET /ngao/v1/ussd/ward-status` | Menu 3, "Hali ya kata yangu" |
| `POST /ngao/v1/ussd/unsubscribe` | The farmer stops |
| `GET /ngao/v1/ussd/drying` | Menu 7: registered? offered/confirmed slot? collection points |
| `POST /ngao/v1/ussd/drying/confirm` | Menu 7: farmer confirms an offered slot |

Auth: `Authorization: Bearer <USSD_SERVICE_TOKEN>`. Each hop also upserts `USSD_SESSIONS` (`session_id`, `phone`, `last_text`, `updated_at`).

**Confirm exact USSD request field names against Wasiliana docs ([docs.wasiliana.com](https://docs.wasiliana.com)).** Parse them in one adapter. The handler needs a session id, the phone number, and the text entered so far.

```text
ussd/
├── README.md
├── callback.py       POST /ussd → CON / END (Flask), GET /healthz
├── menus.py          Jiandikishe, Ripoti, Hali, Lugha, Acha, El Niño: state machine + menu text (sw/en)
├── advice.py         El Niño tips (plant, harvest, water, floods, livestock, health) in sw/en
├── wasiliana.py      request adapter (field names: assumption until confirmed, overridable by env)
├── backend.py        §5 client (BackendClient) + DevBackend stub for local runs
├── store.py          USSD_SESSIONS upsert; read-only SUBSCRIBERS.preferred_lang and WARDS
├── simulate.py       walk the menus in a terminal, or --web for a phone UI
├── simulator.html    phone UI served by simulate.py --web (dev only, not on the live callback)
├── tests/            pytest: adapter + every menu path
├── requirements.txt
└── .env.example      USSD_PUBLIC_URL, USSD_SERVICE_TOKEN, BACKEND_BASE_URL, DB_* (no real values)
```

The callback must be a public HTTPS URL (ngrok for the demo, or the host from decision D2). Point that URL at Wasiliana's USSD service settings.

## Menu tree

```text
(new number) Chagua lugha / Choose language → 1 Kiswahili · 2 English      → POST /ussd/language
MvuaSmart
├── 1 Jiandikishe / Register → role → county → ward → consent              → POST /ussd/register
├── 2 Ripoti / Report → 1 wet bags (count) · 2 sick/dead animals (count)
│                      · 3 flooding · 4 water point dry                    → POST /ussd/report
│                      · 5 other → "send us an SMS" (no call)
├── 3 Hali ya kata yangu / My ward status                                  → GET  /ussd/ward-status
├── 4 Lugha / Language → choose again, continue in the new language        → POST /ussd/language
├── 5 Acha ujumbe / Stop alerts → confirm                                  → POST /ussd/unsubscribe
├── 6 Ushauri wa El Niño / El Niño advice (static tips, no call)
    ├── 1 Kupanda / Planting              ├── 4 Mafuriko na kuhama / Floods and moving
    ├── 2 Kuvuna na kuhifadhi / Harvest   ├── 5 Mifugo / Livestock
    ├── 3 Kuvuna maji / Saving rainwater  └── 6 Afya / Family health
│   each topic: one tip per screen · 1 More · 0 Back · 0 again = main menu
└── 7 Kausha mahindi / Dry wet maize                                       → GET  /ussd/drying
    ├── not registered → "register first"
    ├── slot OFFERED → place, time, bags, fee → 1 Confirm                  → POST /ussd/drying/confirm
    ├── slot CONFIRMED → show it
    └── no slot → bags → collection point → confirm                        → POST /ussd/report (wet_bags + point_id)
```

**Drying flow.** A request is a `wet_bags` report with the chosen collection point. The 18:00 dryer router (backend) turns it into an `OFFERED` booking and sends the A5 SMS. The farmer confirms in menu 7, so booking does not depend on inbound SMS forwarding. Times are shown the Swahili way (08:00 = *saa 2 asubuhi*). The drying endpoints are proposed in API_CONTRACT §5 and need the backend to implement them.

El Niño advice text lives in `advice.py` so it can be edited without touching the menu logic. It needs an agronomist's check, and the Swahili a native speaker's.

Each screen is rebuilt from the whole input path (`1*2*20`) on every hop. An invalid entry shows the same screen again with "Chaguo si sahihi / Invalid choice". Long county and ward lists page with `98`. A backend error ends the session with `Samahani, jaribu tena.` / `Sorry, please try again.`

## Run

```bash
cd ussd
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests          # all menu paths
.venv/bin/python simulate.py +254712345678   # try it in the terminal
.venv/bin/python simulate.py --web           # phone UI at http://localhost:8080/simulator
```

In VS Code, open the phone UI with **Cmd+Shift+P → Simple Browser: Show** and paste the URL. Dial `*384*55#`, press Call, and reply with the keypad or keyboard. **New number** gives a fresh phone so you see the language prompt again.

Without `BACKEND_BASE_URL` the service uses `DevBackend` (logs calls, writes nothing). Without `DB_DSN` it uses an in-memory store with a **dev ward list whose ids do not match `WARDS`**.

For the live callback, set the env vars from `.env.example`, then:

```bash
.venv/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:8080 callback:app
ngrok http 8080        # register https://<ngrok-host>/ussd (add ?k=<WEBHOOK_SECRET> if set) with Wasiliana
```

Run **one worker process**. Whether a session started with the language prompt is kept in process memory, since `USSD_SESSIONS` has no column for it.

## Open items

- **Wasiliana field names.** `wasiliana.py` assumes `sessionId`, `phoneNumber`, `serviceCode`, and a cumulative `text` joined by `*`. Confirm them against docs.wasiliana.com or a logged live request, then override with `WASILIANA_FIELD_*` and `WASILIANA_TEXT_MODE`.
- **Is this number new?** §5 has no lookup endpoint, so this service reads `SUBSCRIBERS.preferred_lang` read-only. If the team prefers, add `GET /ussd/subscriber?phone=` to §5 and swap it in `store.py`.
- **Swahili copy** in `menus.py` needs a native speaker's check.
