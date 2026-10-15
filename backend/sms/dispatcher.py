"""MESSAGES queue -> the SMS provider (Africa's Talking; API_CONTRACT §3). The backend is the only SMS sender.

Producers (alert engine, dryer router, keyword replies) call queue_sms(); kick() sends right away
in a background thread when SMS_DISPATCH=inline, and `manage.py sms_worker` drains the queue
in worker mode. Rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED so the worker and an
inline sender can never send the same row twice.
"""
import datetime as dt
import logging
import threading

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ngao.models import Alert, Message
from ngao_core.tasks import run_after_commit
from sms.providers import get_backend

logger = logging.getLogger(__name__)
_send_lock = threading.Lock()
SIMULATED = "SIMULATED"


def queue_sms(phone, body, subscriber=None, alert=None):
    return Message.objects.create(
        direction=Message.OUT, channel=Message.SMS, phone=phone, subscriber=subscriber,
        body=body[:1000], status=Message.QUEUED, alert=alert,
    )


def kick():
    """Send queued messages soon (after the current transaction commits)."""
    if settings.NGAO_SMS_DISPATCH == "inline":
        run_after_commit(send_queued)


def _is_simulated(message):
    if not settings.NGAO_SMS_SIMULATE_SYNTHETIC or message.phone in settings.NGAO_DEMO_PHONES:
        return False
    return bool(message.subscriber_id and message.subscriber.is_demo)


def _claim(message_id):
    qs = Message.objects.filter(pk=message_id, status=Message.QUEUED)
    if connection.features.has_select_for_update_skip_locked:
        qs = qs.select_for_update(skip_locked=True)
    rows = list(qs)  # no .first(): Oracle cannot combine FOR UPDATE with a row limit
    return rows[0] if rows else None


def send_queued(limit=None):
    """Send up to `limit` queued OUT SMS. Returns counts."""
    summary = {"sent": 0, "simulated": 0, "failed": 0, "retry_later": 0}
    limit = limit or settings.NGAO_SMS_BATCH_SIZE
    with _send_lock:
        backend = get_backend()
        ids = list(
            Message.objects.filter(direction=Message.OUT, channel=Message.SMS, status=Message.QUEUED)
            .order_by("message_id").values_list("message_id", flat=True)[:limit]
        )
        touched_alerts = set()
        give_up_before = timezone.now() - dt.timedelta(hours=settings.NGAO_SMS_RETRY_HOURS)
        for message_id in ids:
            with transaction.atomic():
                message = _claim(message_id)
                if message is None:
                    continue
                if _is_simulated(message):
                    message.status, message.provider_msg_id = Message.SENT, SIMULATED
                    summary["simulated"] += 1
                else:
                    result = backend.send(message.phone, message.body or "")
                    if result.ok:
                        message.status = Message.SENT
                        message.provider_msg_id = (result.provider_msg_id or "")[:100] or None
                        summary["sent"] += 1
                    elif result.retryable and message.created_at > give_up_before:
                        logger.warning("SMS %s retry later: %s", message_id, result.error)
                        summary["retry_later"] += 1
                        continue
                    else:
                        logger.error("SMS %s failed: %s", message_id, result.error)
                        message.status = Message.FAILED
                        summary["failed"] += 1
                message.save(update_fields=["status", "provider_msg_id"])
                if message.alert_id and message.status == Message.SENT:
                    touched_alerts.add(message.alert_id)
        if touched_alerts:
            Alert.objects.filter(pk__in=touched_alerts, status=Alert.APPROVED).update(
                status=Alert.SENT, sent_at=timezone.now()
            )
    if any(summary.values()):
        logger.info("SMS dispatch: %s", summary)
    return summary


def apply_delivery_report(report):
    """Map a parsed delivery report onto MESSAGES.status (SENT -> DELIVERED / FAILED)."""
    if not report.provider_msg_id or report.outcome is None:
        return 0
    status = Message.DELIVERED if report.outcome == "DELIVERED" else Message.FAILED
    return (
        Message.objects.filter(direction=Message.OUT, provider_msg_id=report.provider_msg_id)
        .exclude(status=Message.DELIVERED)
        .update(status=status)
    )
