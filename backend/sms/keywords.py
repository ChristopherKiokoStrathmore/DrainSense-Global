"""Exact SMS keywords, handled before any GenAI call (API_CONTRACT §2.4, §3.4, §5).

  STOP                         unsubscribe (status STOPPED) + confirmation
  LUGHA SW|EN, LANG SW|EN      set preferred_lang + confirmation in the new language
                               (no second token: usage hint, preferred_lang unchanged)
  1, NDIYO, YES                confirm the farmer's open dryer booking; NDIYO/YES with no booking
                               is the A1 reply "Jibu NDIYO ukivuna" -> a 'harvested' report
  RVF [count] [species]        herder reports RVF signs
"""
import re

from django.conf import settings

from alerts.templates import render_system
from ngao.models import Report, Subscriber
from reply_reader.service import create_report
from sms.dispatcher import kick, queue_sms

LANG_WORDS = {"LUGHA", "LANG", "LANGUAGE"}
LANG_VALUES = {"SW": "sw", "EN": "en", "KISWAHILI": "sw", "SWAHILI": "sw", "ENGLISH": "en", "KIINGEREZA": "en"}
CONFIRM_WORDS = {"1", "NDIYO", "YES", "NDIO"}


def tokens(text):
    return re.sub(r"[^\w\s']", " ", (text or "").upper()).split()


def _reply(message, key, lang, **slots):
    queue_sms(message.phone, render_system(key, lang, **slots), subscriber=message.subscriber)


def _lang(message):
    return message.subscriber.spoken_lang() if message.subscriber else "sw"


def handle(message):
    """Return True when the message was a keyword and has been fully handled."""
    words = tokens(message.body)
    if not words:
        return False
    first = words[0]
    handled = (
        _stop(message, words) if first == "STOP" else
        _language(message, words) if first in LANG_WORDS else
        _confirm(message, words) if first in CONFIRM_WORDS and len(words) <= 2 else
        _rvf(message, words) if first == "RVF" else
        False
    )
    if handled:
        kick()
    return handled


def _ward(message):
    return message.subscriber.ward if message.subscriber else None


def _stop(message, words):
    subscriber = message.subscriber
    if subscriber is not None:
        subscriber.status = Subscriber.STOPPED
        subscriber.save(update_fields=["status"])
    create_report(report_type="stop", message=message, ward=_ward(message), source=Report.SMS_KEYWORD,
                  review_status=Report.AUTO_ACCEPTED, payload={"keyword": "STOP"}, ai_confidence=1.0)
    _reply(message, "stopped", _lang(message))
    return True


def _language(message, words):
    value = LANG_VALUES.get(words[1]) if len(words) > 1 else None
    if value is None:
        _reply(message, "lang_usage", _lang(message))
        return True
    subscriber = message.subscriber
    if subscriber is None:
        subscriber = Subscriber.objects.create(
            phone=message.phone, preferred_lang=value, language=value, status=Subscriber.ACTIVE,
            is_demo=message.phone in settings.NGAO_DEMO_PHONES,
        )
        message.subscriber = subscriber
        message.save(update_fields=["subscriber"])
    else:
        subscriber.set_language(value)
        subscriber.save(update_fields=["preferred_lang", "language"])
    _reply(message, "lang_set", value)
    return True


def _confirm(message, words):
    from routing.service import confirm_booking, open_booking_for

    subscriber = message.subscriber
    booking = open_booking_for(subscriber) if subscriber else None
    if booking is not None:
        confirm_booking(booking=booking)
        create_report(report_type="booking_confirm", message=message, ward=_ward(message),
                      source=Report.SMS_KEYWORD, review_status=Report.AUTO_ACCEPTED,
                      payload={"keyword": words[0], "booking_id": booking.pk}, ai_confidence=1.0)
        return True
    if words[0] == "1":
        _reply(message, "no_booking", _lang(message))
        return True
    create_report(report_type="harvested", message=message, ward=_ward(message), source=Report.SMS_KEYWORD,
                  review_status=Report.AUTO_ACCEPTED, payload={"keyword": words[0]}, ai_confidence=1.0)
    if settings.NGAO_SEND_REPORT_ACK:
        _reply(message, "report_ack", _lang(message))
    return True


SPECIES = {"NG'OMBE": "cattle", "NGOMBE": "cattle", "CATTLE": "cattle", "COWS": "cattle", "MBUZI": "goats",
           "GOATS": "goats", "KONDOO": "sheep", "SHEEP": "sheep", "NGAMIA": "camels", "CAMELS": "camels"}


def _rvf(message, words):
    count = next((int(w) for w in words[1:] if w.isdigit()), None)
    species = next((SPECIES[w] for w in words[1:] if w in SPECIES), None)
    create_report(report_type="rvf_signs", count=count, unit="animals" if count else None,
                  message=message, ward=_ward(message), source=Report.SMS_KEYWORD,
                  review_status=Report.AUTO_ACCEPTED, ai_confidence=1.0,
                  payload={"keyword": "RVF", "count": count, "species": species})
    if settings.NGAO_SEND_REPORT_ACK:
        _reply(message, "report_ack", _lang(message))
    return True
