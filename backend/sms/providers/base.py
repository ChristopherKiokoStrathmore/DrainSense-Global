"""Provider-neutral pieces: result types, the console backend, and lenient callback parsing helpers."""
import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger("sms.providers")


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


def pick(data, candidates):
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in candidates:
        value = data.get(key, lowered.get(key.lower()))
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        if value not in (None, ""):
            return str(value).strip()
    return None


def normalize_status(value):
    return (value or "").replace(" ", "").replace("_", "").lower()


class ConsoleBackend:
    """Development/demo backend: logs the SMS instead of sending it."""

    name = "console"

    def send(self, phone, text):
        logger.info("SMS (console) to %s: %s", phone, text)
        return SendResult(ok=True, provider_msg_id=f"console-{uuid.uuid4().hex[:12]}")
