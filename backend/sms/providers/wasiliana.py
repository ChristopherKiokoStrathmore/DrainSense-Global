"""Wasiliana SMS (kept as a fallback provider; SMS_BACKEND=wasiliana). Africa's Talking is the default.

Send call (reference shape from the pytek-wasiliana client; not confirmed against docs.wasiliana.com):
    POST https://api.wasiliana.com/api/v1/developer/send/sms
    headers: apiKey: <WASILIANA_API_KEY>
    body:    {"recipients": ["2547XXXXXXXX"], "from": "<WASILIANA_SENDER_ID>", "message": "..."}
Delivery report (same reference): {"phone", "correlator", "deliveryStatus", "failure_reason"}.
"""
import logging

import requests
from django.conf import settings

from ngao_core.phones import mask_phone, to_provider_msisdn
from sms.providers.base import DeliveryReport, InboundSms, SendResult, flatten, normalize_status, pick

logger = logging.getLogger("sms.wasiliana")

DLR_FIELDS = {
    "provider_msg_id": ["correlator", "messageId", "message_id", "msgId", "id"],
    "status": ["deliveryStatus", "delivery_status", "status", "dlrStatus"],
    "phone": ["phone", "msisdn", "recipient", "mobile"],
    "reason": ["failure_reason", "failureReason", "reason", "description"],
}
INBOUND_FIELDS = {
    "phone": ["phone", "msisdn", "from", "sender", "mobile", "phoneNumber"],
    "text": ["message", "text", "body", "sms", "content"],
    "provider_msg_id": ["linkid", "linkId", "messageId", "message_id", "id"],
}
SEND_ID_KEYS = ["correlator", "messageId", "message_id", "msgId", "id", "uniqueId"]
DELIVERED = {"0", "delivered", "deliveredtoterminal", "success"}  # "0" = delivered is an assumption
FAILED = {"failed", "rejected", "undelivered", "expired", "deliveryimpossible", "deliveredtonetworkfailed"}


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


class WasilianaBackend:
    name = "wasiliana"

    def __init__(self):
        self.url = settings.NGAO_WASILIANA_SMS_URL
        self.api_key = settings.NGAO_WASILIANA_API_KEY
        self.sender_id = settings.NGAO_WASILIANA_SENDER_ID
        self.timeout = settings.NGAO_WASILIANA_TIMEOUT
        if not (self.api_key and self.sender_id):
            raise RuntimeError("SMS_BACKEND=wasiliana needs WASILIANA_API_KEY and WASILIANA_SENDER_ID")

    def send(self, phone, text):
        payload = {"recipients": [to_provider_msisdn(phone)], "from": self.sender_id, "message": text}
        headers = {"Content-Type": "application/json", "Accept": "application/json", "apiKey": self.api_key}
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


def parse_delivery_report(data):
    fields = dict(DLR_FIELDS)
    fields.update(settings.NGAO_SMS_DLR_FIELDS or {})
    data = flatten(data)
    status_raw = pick(data, fields["status"])
    reason = pick(data, fields["reason"])
    status = normalize_status(status_raw)
    if status in DELIVERED and not reason:
        outcome = "DELIVERED"
    elif status in FAILED or (reason and status not in DELIVERED):
        outcome = "FAILED"
    elif status in DELIVERED:
        outcome = "DELIVERED"
    else:
        outcome = None
    return DeliveryReport(provider_msg_id=pick(data, fields["provider_msg_id"]), status_raw=status_raw,
                          phone=pick(data, fields["phone"]), reason=reason, outcome=outcome)


def parse_inbound(data):
    fields = dict(INBOUND_FIELDS)
    fields.update(settings.NGAO_SMS_INBOUND_FIELDS or {})
    data = flatten(data)
    return InboundSms(phone=pick(data, fields["phone"]), text=pick(data, fields["text"]),
                      provider_msg_id=pick(data, fields["provider_msg_id"]))
