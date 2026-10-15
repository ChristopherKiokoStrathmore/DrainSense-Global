"""Client for the USSD-to-backend contract (docs/API_CONTRACT.md §5).

Every call is bounded by BACKEND_TIMEOUT_S so the reply to Wasiliana still
goes out inside its ~10 second window. Any failure raises BackendError, which
the menu turns into an END screen in the subscriber's language.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)


class BackendError(RuntimeError):
    pass


class BackendClient:
    def __init__(self, base_url: str, token: str, timeout: float = 4.0, session=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}"})

    @classmethod
    def from_env(cls) -> "BackendClient":
        return cls(
            base_url=os.environ["BACKEND_BASE_URL"],
            token=os.environ["USSD_SERVICE_TOKEN"],
            timeout=float(os.getenv("BACKEND_TIMEOUT_S", "4")),
        )

    def _call(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.http.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            log.error("%s %s failed: %s", method, path, exc)
            raise BackendError(str(exc)) from exc
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            log.error("%s %s -> %s %s (request_id=%s)", method, path, resp.status_code,
                      err.get("code"), err.get("request_id"))
            raise BackendError(f"{resp.status_code} {err.get('code', '')}".strip())
        try:
            return resp.json() if resp.content else {}
        except ValueError as exc:
            raise BackendError("backend returned non-JSON") from exc

    def set_language(self, phone: str, lang: str) -> dict:
        return self._call("POST", "/ussd/language", json={"phone": phone, "preferred_lang": lang})

    def register(self, phone: str, coop_id: int | None, membership_no: str | None,
                 ward_id: int | None, role: str, lang: str, consent: bool) -> dict:
        # coop_id and membership_no are null for "Other / not listed". ward_id is the
        # cooperative's ward; it is left out when unknown (API_CONTRACT §5 asks the
        # backend to accept that).
        body = {"phone": phone, "coop_id": coop_id, "membership_no": membership_no,
                "role": role, "preferred_lang": lang, "consent": consent}
        if ward_id is not None:
            body["ward_id"] = ward_id
        return self._call("POST", "/ussd/register", json=body)

    def report(self, phone: str, session_id: str, report_type: str,
               count: int | None, unit: str | None, point_id: int | None = None) -> dict:
        body = {"phone": phone, "session_id": session_id, "report_type": report_type,
                "count": count, "unit": unit}
        if point_id is not None:
            body["point_id"] = point_id  # farmer asked for drying at this collection point
        return self._call("POST", "/ussd/report", json=body)

    def ward_status(self, phone: str) -> str:
        return self._call("GET", "/ussd/ward-status", params={"phone": phone}).get("text", "")

    def unsubscribe(self, phone: str) -> dict:
        return self._call("POST", "/ussd/unsubscribe", json={"phone": phone})

    def drying(self, phone: str) -> dict:
        """{"registered": bool, "booking": {...} | None, "points": [{"point_id", "point_name"}]}"""
        return self._call("GET", "/ussd/drying", params={"phone": phone})

    def confirm_booking(self, phone: str, booking_id: int) -> dict:
        return self._call("POST", "/ussd/drying/confirm",
                          json={"phone": phone, "booking_id": booking_id})

    def seasonal_outlook(self, phone: str) -> dict:
        """{"text", "registered", "season", "probability", "category"} for menu 6."""
        return self._call("GET", "/ussd/seasonal-outlook", params={"phone": phone})


class DevBackend:
    """Stand-in for local runs before the real §5 endpoints exist. Logs every call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status_text = {"sw": "Kata yako: hakuna alama ya hatari leo.",
                            "en": "Your ward: no risk score today."}
        self.langs: dict[str, str] = {}
        self.registered: set[str] = set()
        self.bookings: dict[str, dict] = {}
        self.outlooks: dict[str, dict] = {}
        # DEV FIXTURE: real points come from COLLECTION_POINTS in the subscriber's ward.
        self.points = [{"point_id": 3, "point_name": "Moiben market"},
                       {"point_id": 4, "point_name": "Kapseret coop store"},
                       {"point_id": 5, "point_name": "Kimumu primary school"}]

    def _log(self, name: str, **kw):
        self.calls.append((name, kw))
        log.info("DevBackend %s %s", name, kw)

    def set_language(self, phone, lang):
        self.langs[phone] = lang
        self._log("language", phone=phone, preferred_lang=lang)
        return {"preferred_lang": lang}

    def register(self, phone, coop_id, membership_no, ward_id, role, lang, consent):
        self.registered.add(phone)
        self._log("register", phone=phone, coop_id=coop_id, membership_no=membership_no,
                  ward_id=ward_id, role=role, preferred_lang=lang, consent=consent)
        return {"subscriber_id": 1, "status": "ACTIVE"}

    def report(self, phone, session_id, report_type, count, unit, point_id=None):
        extra = {"point_id": point_id} if point_id is not None else {}
        self._log("report", phone=phone, session_id=session_id,
                  report_type=report_type, count=count, unit=unit, **extra)
        if report_type == "wet_bags" and point_id is not None:
            # Pretend the 18:00 router ran: offer a slot tomorrow at 08:00 EAT.
            eat = timezone(timedelta(hours=3))
            eta = (datetime.now(eat) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            name = next(p["point_name"] for p in self.points if p["point_id"] == point_id)
            self.bookings[phone] = {"booking_id": len(self.bookings) + 1, "status": "OFFERED",
                                    "point_name": name, "eta": eta.isoformat(),
                                    "bags": count, "fee_kes": count * 60}
        return {"report_id": 1}

    def drying(self, phone):
        self._log("drying", phone=phone)
        return {"registered": phone in self.registered,
                "booking": self.bookings.get(phone), "points": self.points}

    def confirm_booking(self, phone, booking_id):
        self._log("drying-confirm", phone=phone, booking_id=booking_id)
        self.bookings[phone]["status"] = "CONFIRMED"
        return {"booking_id": booking_id, "status": "CONFIRMED"}

    def seasonal_outlook(self, phone):
        self._log("seasonal-outlook", phone=phone)
        return self.outlooks.get(phone, {"registered": phone in self.registered, "season": None,
                                         "probability": None, "category": None, "text": ""})

    def ward_status(self, phone):
        self._log("ward-status", phone=phone)
        return self.status_text[self.langs.get(phone, "sw")]

    def unsubscribe(self, phone):
        self._log("unsubscribe", phone=phone)
        return {"status": "STOPPED"}
