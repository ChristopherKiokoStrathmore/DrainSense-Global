"""Reply reader (ARCHITECTURE §6.2): inbound text -> validated JSON -> REPORTS.

confidence < 0.7, report_type 'other', a GenAI failure, or output that breaks the §2.4 schema
-> review_status NEEDS_REVIEW (officers see it in V_REVIEW_QUEUE). 'stop' and 'booking_confirm'
from the AI are also reviewed: actions on a subscriber only follow exact keywords (sms/keywords.py).
"""
import logging
from dataclasses import dataclass

from django.conf import settings

from ngao.models import Report
from reply_reader import genai, rules
from reply_reader.schema import SchemaError, validate

logger = logging.getLogger(__name__)

ACTION_TYPES = {"stop", "booking_confirm"}


@dataclass
class ReplyParse:
    payload: dict
    source: str          # SMS_AI | SMS_KEYWORD
    review_status: str   # AUTO_ACCEPTED | NEEDS_REVIEW
    reason: str | None = None


def _review_status(payload):
    if payload["report_type"] == "other":
        return Report.NEEDS_REVIEW, "UNKNOWN_TYPE"
    if payload["report_type"] in ACTION_TYPES:
        return Report.NEEDS_REVIEW, "ACTION_NEEDS_KEYWORD"
    if payload["confidence"] < settings.NGAO_AI_CONFIDENCE_THRESHOLD:
        return Report.NEEDS_REVIEW, "LOW_CONFIDENCE"
    return Report.AUTO_ACCEPTED, None


def parse_reply(text):
    if settings.NGAO_REPLY_READER == "genai":
        try:
            payload = validate(genai.extract(text))
            status, reason = _review_status(payload)
            return ReplyParse(payload, Report.SMS_AI, status, reason)
        except (genai.GenAIError, SchemaError) as exc:
            logger.warning("GenAI reply parse failed (%s); keyword parse kept for the reviewer", exc)
            payload = rules.extract(text)
            return ReplyParse(payload, Report.SMS_KEYWORD, Report.NEEDS_REVIEW, f"GENAI_FAILED: {exc}"[:200])
    payload = rules.extract(text)
    status, reason = _review_status(payload)
    return ReplyParse(payload, Report.SMS_KEYWORD, status, reason)


def create_report(*, report_type, count=None, unit=None, ward=None, message=None, payload=None,
                  ai_confidence=None, source, review_status):
    return Report.objects.create(
        message=message, ward=ward, report_type=report_type, report_count=count, unit=unit,
        payload=payload, ai_confidence=ai_confidence, source=source, review_status=review_status,
    )


def read_and_store(message):
    """Parse one inbound MESSAGES row into a REPORTS row. Returns the report."""
    parsed = parse_reply(message.body or "")
    payload = dict(parsed.payload)
    if parsed.reason:
        payload["review_reason"] = parsed.reason
    subscriber = message.subscriber
    report = create_report(
        report_type=parsed.payload["report_type"],
        count=parsed.payload.get("count"),
        unit=parsed.payload.get("unit"),
        ward=subscriber.ward if subscriber else None,
        message=message,
        payload=payload,
        ai_confidence=round(parsed.payload["confidence"], 3),
        source=parsed.source,
        review_status=parsed.review_status,
    )
    logger.info("report %s from message %s: %s %s (%s)", report.pk, message.pk, report.report_type,
                report.report_count, report.review_status)
    return report
