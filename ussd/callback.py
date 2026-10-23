"""USSD callback (Africa's Talking by default, Wasiliana optional): POST /ussd -> text/plain "CON ..." or "END ...".

Run locally:   flask --app callback run --port 8080
Run for real:  gunicorn -w 1 --threads 8 -b 0.0.0.0:8080 callback:app
(one worker process: the per-session cache in store.SessionCache is in memory)
"""

import hmac
import logging
import os
import re
import time
from urllib.parse import parse_qs

from flask import Flask, Response, redirect, request, send_file

import menus
import gateway
from backend import BackendClient, DevBackend
from store import SessionCache, SessionMeta, store_from_env

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ussd")


def mask(phone: str) -> str:
    return phone[:7] + "***" + phone[-2:] if len(phone) > 9 else "***"


HERE = os.path.dirname(os.path.abspath(__file__))
# Browser phone simulator (demo): only fake +2547000xxxxx numbers, so it can never text a real phone.
SIM_PHONE = re.compile(r"^\+2547000\d{5}$")


def plain(body: str, status: int = 200) -> Response:
    return Response(body, status=status, mimetype="text/plain")


def create_app(backend=None, store=None, sessions=None) -> Flask:
    app = Flask(__name__)
    if backend is None:
        if os.getenv("BACKEND_BASE_URL"):
            backend = BackendClient.from_env()
        else:
            log.warning("BACKEND_BASE_URL not set: using DevBackend (no real writes)")
            backend = DevBackend()
    store = store or store_from_env()
    sessions = sessions or SessionCache()
    secret = os.getenv("WEBHOOK_SECRET", "")

    @app.get("/healthz")
    def healthz():
        return plain(f"ok {gateway.provider()}")

    @app.post("/ussd")
    def ussd():
        if secret and not hmac.compare_digest(request.args.get("k", ""), secret):
            return plain("END Unauthorized", 403)
        return hop()

    if os.getenv("SIMULATOR_ENABLED", "1") == "1":
        @app.get("/simulator")
        def simulator_redirect():
            return redirect("/simulator/", 301)

        @app.get("/simulator/")
        def simulator_page():
            return send_file(os.path.join(HERE, "simulator.html"))

        @app.post("/simulator/ussd")
        def simulator_ussd():
            # No webhook secret here, so the phone is restricted to the synthetic range instead.
            # Read the raw body (not request.form) so gateway.parse still sees it.
            phone = parse_qs(request.get_data(as_text=True)).get("phoneNumber", [""])[0].strip()
            if not SIM_PHONE.match(phone):
                return plain("END Simulator numbers must look like +2547000xxxxx", 400)
            return hop()

    def hop():
        started = time.monotonic()
        try:
            req = gateway.parse(request.content_type, request.get_data(),
                                  request.args.to_dict())
        except gateway.BadRequest as exc:
            log.warning("bad USSD request: %s body=%r", exc, request.get_data()[:500])
            return plain(menus.end(menus.TEXT["sw"]["error"]), 400)

        lang = "sw"
        try:
            text = req.text
            if gateway.text_mode() == "latest":
                # Gateway sends only the newest input; rebuild the full path.
                prev = store.last_text(req.session_id)
                text = f"{prev}*{text}" if prev and text else (text or prev)

            meta = sessions.get(req.session_id)
            if meta is None or meta.phone != req.phone:
                meta = SessionMeta(start_lang=store.preferred_lang(req.phone), created=time.time(),
                                   phone=req.phone)
                sessions.put(req.session_id, meta)
            lang = meta.start_lang or "sw"

            ctx = menus.Ctx(phone=req.phone, session_id=req.session_id, lang=lang,
                            backend=backend, store=store, cache=meta.cache)
            reply = menus.handle(text, ctx, is_new=meta.start_lang is None)

            try:
                store.upsert_session(req.session_id, req.phone, text)
            except Exception:
                log.exception("USSD_SESSIONS upsert failed for %s", req.session_id)
        except Exception:
            log.exception("USSD hop failed for session %s", req.session_id)
            reply = menus.end(menus.TEXT[lang]["error"])

        log.info("session=%s phone=%s text=%r -> %r (%.0f ms)", req.session_id, mask(req.phone),
                 req.text, reply[:40], (time.monotonic() - started) * 1000)
        return plain(reply)

    return app


app = create_app()
