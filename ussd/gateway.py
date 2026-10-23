"""Which USSD gateway posts to /ussd: Africa's Talking (default) or Wasiliana.

    USSD_PROVIDER = africastalking (default) | wasiliana

Africa's Talking USSD (https://developers.africastalking.com/docs/ussd/overview):
    POST application/x-www-form-urlencoded to the channel's callback URL with
        sessionId, serviceCode, phoneNumber (+2547...), networkCode, text
    `text` holds every input of the session so far joined by `*` ("" on the first hop).
    Reply 200 text/plain starting with "CON " (keep the session open) or "END " (close it).
That is the same shape the Wasiliana adapter assumes, so both share wasiliana.parse; only the
field names and text mode are fixed for Africa's Talking instead of read from WASILIANA_* env vars.
"""
import os

import wasiliana
from wasiliana import BadRequest, UssdRequest, normalize_phone

__all__ = ["BadRequest", "UssdRequest", "parse", "provider", "text_mode"]

AFRICASTALKING = "africastalking"
WASILIANA = "wasiliana"


def provider() -> str:
    value = os.getenv("USSD_PROVIDER", AFRICASTALKING).strip().lower()
    return value if value in (AFRICASTALKING, WASILIANA) else AFRICASTALKING


def text_mode() -> str:
    return "cumulative" if provider() == AFRICASTALKING else wasiliana.text_mode()


def parse(content_type: str, raw: bytes, query: dict | None = None) -> UssdRequest:
    if provider() == WASILIANA:
        return wasiliana.parse(content_type, raw, query)
    body = wasiliana._decode(content_type, raw)
    session_id = str(body.get("sessionId") or "").strip()
    phone = normalize_phone(str(body.get("phoneNumber") or ""))
    if not session_id or not phone:
        raise BadRequest(f"Africa's Talking USSD needs sessionId and phoneNumber; got keys {sorted(body)}")
    return UssdRequest(session_id=session_id, phone=phone, text=str(body.get("text") or "").strip(),
                       service_code=str(body.get("serviceCode") or ""))
