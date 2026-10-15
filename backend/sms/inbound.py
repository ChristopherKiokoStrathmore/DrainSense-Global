"""Inbound SMS: store the MESSAGES row now, read it after the HTTP 200 (API_CONTRACT §3.4)."""
import logging

from django.conf import settings

from ngao.models import Message, Report, Subscriber
from ngao_core.phones import InvalidPhone, normalize_phone
from ngao_core.tasks import run_after_commit
from reply_reader.service import read_and_store
from sms import keywords
from sms.dispatcher import kick, queue_sms

logger = logging.getLogger(__name__)


def receive(phone, text, provider_msg_id=None, process_now=False):
    """Insert MESSAGES(direction=IN) and schedule processing (or run it now). Returns the message."""
    try:
        phone = normalize_phone(phone, kenya_only=False)
    except InvalidPhone:
        phone = str(phone or "unknown")[:16]
    subscriber = Subscriber.objects.filter(phone=phone).first()
    message = Message.objects.create(
        direction=Message.IN, channel=Message.SMS, phone=phone, subscriber=subscriber,
        body=(text or "")[:1000], provider_msg_id=(provider_msg_id or "")[:100] or None,
        status=Message.RECEIVED,
    )
    if process_now:
        process(message.pk)
    else:
        run_after_commit(process, message.pk)
    return message


def process(message_id):
    message = Message.objects.select_related("subscriber__ward").get(pk=message_id)
    if keywords.handle(message):
        return None
    report = read_and_store(message)
    if report.report_type == "bags_dried" and report.review_status == Report.AUTO_ACCEPTED:
        from routing.service import record_drying

        record_drying(report)
    if settings.NGAO_SEND_REPORT_ACK and message.subscriber and report.review_status == Report.AUTO_ACCEPTED:
        from alerts.templates import render_system

        queue_sms(message.phone, render_system("report_ack", message.subscriber.spoken_lang()),
                  subscriber=message.subscriber)
        kick()
    return report
