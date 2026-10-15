"""Africa's Talking SMS (API_CONTRACT §3): bulk send, delivery reports, incoming messages.

Send (shape from the official africastalking-python SDK, SMS.send):
    POST https://api.africastalking.com/version1/messaging
         https://api.sandbox.africastalking.com/version1/messaging   when AT_USERNAME = "sandbox"
    headers: apiKey: <AT_API_KEY>, Accept: application/json
    form:    username, to (+2547...), message, bulkSMSMode=1, from (optional sender ID / shortcode)
    reply:   {"SMSMessageData": {"Message": "Sent to 1/1 Total Cost: KES 0.8000",
              "Recipients": [{"statusCode": 101, "number": "+2547...", "status": "Success",
                              "cost": "KES 0.8000", "messageId": "ATXid_..."}]}}
Callbacks are form-encoded POSTs (AT does not sign them; use ?k=<WEBHOOK_SECRET>):
    delivery report  id, status (Sent|Submitted|Buffered|Rejected|Success|Failed), phoneNumber,
                     networkCode, failureReason, retryCount
    incoming SMS     from, to, text, date, id, linkId, networkCode
"""
import logging

import requests
from django.conf import settings

from ngao_core.phones import mask_phone
from sms.providers.base import DeliveryReport, InboundSms, SendResult, flatten, normalize_status, pick

logger = logging.getLogger("sms.africastalking")

LIVE_URL = "https://api.africastalking.com/version1/messaging"
SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"

ACCEPTED_CODES = {100, 101, 102}    # Processed, Sent, Queued
RETRYABLE_CODES = {500, 501, 502}   # InternalServerError, GatewayError, RejectedByGateway
# 401 RiskHold, 402 InvalidSenderId, 403 InvalidPhoneNumber, 404 UnsupportedNumberType,
# 405 InsufficientBalance, 406 UserInBlacklist, 407 CouldNotRoute, 409 DoNotDisturbRejection: final

DLR_FIELDS = {
    "provider_msg_id": ["id", "messageId"],
    "status": ["status"],
    "phone": ["phoneNumber", "phone"],
    "reason": ["failureReason", "failure_reason"],
}
INBOUND_FIELDS = {
    "phone": ["from", "phoneNumber", "phone"],
    "text": ["text", "message"],
    "provider_msg_id": ["id", "linkId"],
}
DELIVERED = {"success"}
FAILED = {"failed", "rejected"}  # Sent / Submitted / Buffered are not final


def sms_url():
    if settings.NGAO_AT_SMS_URL:
        return settings.NGAO_AT_SMS_URL
    return SANDBOX_URL if settings.NGAO_AT_USERNAME == "sandbox" else LIVE_URL


class AfricasTalkingBackend:
    name = "africastalking"

    def __init__(self):
        self.username = settings.NGAO_AT_USERNAME
        self.api_key = settings.NGAO_AT_API_KEY
        self.sender_id = settings.NGAO_AT_SENDER_ID
        self.url = sms_url()
        self.timeout = settings.NGAO_AT_TIMEOUT
        if not (self.username and self.api_key):
            raise RuntimeError("SMS_BACKEND=africastalking needs AT_USERNAME and AT_API_KEY")

    def send(self, phone, text):
        data = {"username": self.username, "to": phone, "message": text, "bulkSMSMode": 1}
        if self.sender_id:
            data["from"] = self.sender_id
        headers = {"apiKey": self.api_key, "Accept": "application/json"}
        try:
            response = requests.post(self.url, data=data, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            return SendResult(ok=False, error=f"network: {exc}", retryable=True)
        try:
            body = response.json()
        except ValueError:
            body = response.text[:300]  # e.g. "The supplied authentication is invalid"
        logger.info("Africa's Talking send %s -> HTTP %s %s", mask_phone(phone), response.status_code, body)
        if response.status_code >= 500:
            return SendResult(ok=False, error=f"HTTP {response.status_code}", retryable=True, raw=body)
        if response.status_code >= 400 or not isinstance(body, dict):
            return SendResult(ok=False, error=f"HTTP {response.status_code}: {body}", raw=body)

        data = body.get("SMSMessageData") or {}
        recipients = data.get("Recipients") or []
        if not recipients:
            # e.g. {"SMSMessageData": {"Message": "InvalidSenderId", "Recipients": []}}
            return SendResult(ok=False, error=f"no recipients: {data.get('Message')}", raw=body)
        recipient = recipients[0]
        try:
            code = int(recipient.get("statusCode", 0))
        except (TypeError, ValueError):
            code = 0
        if code in ACCEPTED_CODES:
            return SendResult(ok=True, provider_msg_id=recipient.get("messageId"), raw=body)
        error = f"{code} {recipient.get('status')}"
        return SendResult(ok=False, error=error, retryable=code in RETRYABLE_CODES, raw=body)


def parse_delivery_report(data):
    fields = dict(DLR_FIELDS)
    fields.update(settings.NGAO_SMS_DLR_FIELDS or {})
    data = flatten(data)
    status_raw = pick(data, fields["status"])
    status = normalize_status(status_raw)
    outcome = "DELIVERED" if status in DELIVERED else "FAILED" if status in FAILED else None
    return DeliveryReport(
        provider_msg_id=pick(data, fields["provider_msg_id"]),
        status_raw=status_raw,
        phone=pick(data, fields["phone"]),
        reason=pick(data, fields["reason"]),
        outcome=outcome,
    )


def parse_inbound(data):
    fields = dict(INBOUND_FIELDS)
    fields.update(settings.NGAO_SMS_INBOUND_FIELDS or {})
    data = flatten(data)
    return InboundSms(
        phone=pick(data, fields["phone"]),
        text=pick(data, fields["text"]),
        provider_msg_id=pick(data, fields["provider_msg_id"]),
    )
