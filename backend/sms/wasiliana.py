"""The single Wasiliana adapter (API_CONTRACT §3.2–3.4). No other module knows provider field names.

Send call (reference shape from the pytek-wasiliana client; confirm at docs.wasiliana.com):
    POST https://api.wasiliana.com/api/v1/developer/send/sms
    headers: apiKey: <WASILIANA_API_KEY>
    body:    {"recipients": ["2547XXXXXXXX"], "from": "<WASILIANA_SENDER_ID>", "message": "..."}
Delivery report (same reference): {"phone", "correlator", "deliveryStatus", "failure_reason"}.
Inbound SMS forwarding is still an open item, so its field names are candidates only.

URL, header name and callback field names are all overridable from the environment, so
confirming the docs changes .env, not code. Raw callback bodies are always logged.
"""
import logging
import uuid
from dataclasses import dataclass

import requests
from django.conf import settings

from ngao_core.phones import mask_phone, to_provider_msisdn

logger = logging.getLogger("sms.wasiliana")

DEFAULT_DLR_FIELDS = {
    "provider_msg_id": ["correlator", "messageId", "message_id", "msgId", "id"],
    "status": ["deliveryStatus", "delivery_status", "status", "dlrStatus"],
    "phone": ["phone", "msisdn", "recipient", "mobile"],
    "reason": ["failure_reason", "failureReason", "reason", "description"],
}
DEFAULT_INBOUND_FIELDS = {
    "phone": ["phone", "msisdn", "from", "sender", "mobile", "phoneNumber"],
    "text": ["message", "text", "body", "sms", "content"],
    "provider_msg_id": ["linkid", "linkId", "messageId", "message_id", "id"],
}
SEND_ID_KEYS = ["correlator", "messageId", "message_id", "msgId", "id", "uniqueId"]


@dataclass
class SendResult:
    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None
    retryable: bool = False
    raw: object = None


@dataclass
class DeliveryReport:
    provider_msg_id: str | None
    status_raw: str | None
    phone: str | None
    reason: str | None
    outcome: str | None  # "DELIVERED", "FAILED", or None when the status is not final / unknown


@dataclass
class InboundSms:
    phone: str | None
    text: str | None
    provider_msg_id: str | None


def flatten(data):
    """QueryDict / dict / JSON list-of-one -> plain dict of scalars."""
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if hasattr(data, "dict"):
        data = data.dict()
    if not isinstance(data, dict):
        return {}
    for wrapper in ("data", "payload", "message_data"):
        if isinstance(data.get(wrapper), dict):
            merged = dict(data)
            merged.update(data[wrapper])
            data = merged
    return data


def _pick(data, candidates):
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in candidates:
        value = data.get(key, lowered.get(key.lower()))
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if value not in (None, ""):
            return str(value).strip()
    return None


def _find_key(obj, keys, depth=0):
    if depth > 3:
        return None
    if isinstance(obj, dict):
        for key in keys:
            if obj.get(key) not in (None, ""):
                return str(obj[key])
        for value in obj.values():
            found = _find_key(value, keys, depth + 1)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _find_key(item, keys, depth + 1)
            if found:
                return found
    return None


# ----- outbound ----------------------------------------------------------------------------------


class ConsoleBackend:
    """Development/demo backend: logs the SMS instead of sending it."""

    name = "console"

    def send(self, phone, text):
        logger.info("SMS (console) to %s: %s", phone, text)
        return SendResult(ok=True, provider_msg_id=f"console-{uuid.uuid4().hex[:12]}")


class WasilianaBackend:
    name = "wasiliana"

    def __init__(self):
        self.url = settings.NGAO_WASILIANA_SMS_URL
        self.api_key = settings.NGAO_WASILIANA_API_KEY
        self.sender_id = settings.NGAO_WASILIANA_SENDER_ID
        self.header = settings.NGAO_WASILIANA_API_KEY_HEADER
        self.timeout = settings.NGAO_WASILIANA_TIMEOUT
        if not (self.api_key and self.sender_id):
            raise RuntimeError("SMS_BACKEND=wasiliana needs WASILIANA_API_KEY and WASILIANA_SENDER_ID")

    def send(self, phone, text):
        payload = {"recipients": [to_provider_msisdn(phone)], "from": self.sender_id, "message": text}
        headers = {"Content-Type": "application/json", "Accept": "application/json", self.header: self.api_key}
        try:
            response = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            return SendResult(ok=False, error=f"network: {exc}", retryable=True)
        try:
            body = response.json()
        except ValueError:
            body = response.text[:500]
        logger.info("Wasiliana send %s -> HTTP %s %s", mask_phone(phone), response.status_code, body)
        if response.status_code >= 500:
            return SendResult(ok=False, error=f"HTTP {response.status_code}", retryable=True, raw=body)
        if response.status_code >= 400:
            return SendResult(ok=False, error=f"HTTP {response.status_code}: {body}", raw=body)
        if isinstance(body, dict):
            status_text = str(body.get("status", "")).lower()
            if body.get("success") is False or status_text in ("error", "failed", "fail"):
                return SendResult(ok=False, error=str(body)[:300], raw=body)
        return SendResult(ok=True, provider_msg_id=_find_key(body, SEND_ID_KEYS), raw=body)


def get_backend():
    if settings.NGAO_SMS_BACKEND == "wasiliana":
        return WasilianaBackend()
    return ConsoleBackend()


# ----- callbacks ---------------------------------------------------------------------------------


def parse_delivery_report(data):
    fields = dict(DEFAULT_DLR_FIELDS)
    fields.update(settings.NGAO_WASILIANA_DLR_FIELDS or {})
    data = flatten(data)
    status_raw = _pick(data, fields["status"])
    reason = _pick(data, fields["reason"])
    normalized = (status_raw or "").replace(" ", "").replace("_", "").lower()
    delivered = {v.replace("_", "").lower() for v in settings.NGAO_WASILIANA_DLR_DELIVERED}
    failed = {v.replace("_", "").lower() for v in settings.NGAO_WASILIANA_DLR_FAILED}
    if normalized in delivered and not reason:
        outcome = "DELIVERED"
    elif normalized in failed or (reason and normalized not in delivered):
        outcome = "FAILED"
    elif normalized in delivered:
        outcome = "DELIVERED"
    else:
        outcome = None
    return DeliveryReport(
        provider_msg_id=_pick(data, fields["provider_msg_id"]),
        status_raw=status_raw,
        phone=_pick(data, fields["phone"]),
        reason=reason,
        outcome=outcome,
    )


def parse_inbound(data):
    fields = dict(DEFAULT_INBOUND_FIELDS)
    fields.update(settings.NGAO_WASILIANA_INBOUND_FIELDS or {})
    data = flatten(data)
    return InboundSms(
        phone=_pick(data, fields["phone"]),
        text=_pick(data, fields["text"]),
        provider_msg_id=_pick(data, fields["provider_msg_id"]),
    )
