"""Render alert and system SMS in the subscriber's preferred_lang (API_CONTRACT §4)."""
import logging
import string

from django.conf import settings

from alerts.catalogue import DEFAULT_ALERT_COPY, DEFAULT_HAZARDS, SYSTEM_COPY
from ngao.models import AlertTemplate, AlertType

logger = logging.getLogger(__name__)

SMS_SEGMENT = 160
MAX_SEGMENTS = 2


class _Slots(dict):
    """Unknown slots render as empty text instead of raising."""

    def __missing__(self, key):
        logger.warning("template slot {%s} has no value", key)
        return ""


def fill(template, **slots):
    clean = {k: ("" if v is None else v) for k, v in slots.items()}
    return string.Formatter().vformat(template, (), _Slots(clean)).strip()


def hazard_for(alert_type_code):
    mapping = dict(DEFAULT_HAZARDS)
    mapping.update(settings.NGAO_TEMPLATE_HAZARDS or {})
    return mapping.get(alert_type_code, alert_type_code)


def alert_template(alert_type, lang, severity=None):
    """Return (template_text, hazard, severity, source) for an alert type and language."""
    code = alert_type.alert_type if isinstance(alert_type, AlertType) else alert_type
    severity = severity or settings.NGAO_TEMPLATE_SEVERITY_ACT
    hazard = hazard_for(code)
    for hazard_key in dict.fromkeys([hazard, code]):
        row = (
            AlertTemplate.objects.filter(hazard__iexact=hazard_key, severity__iexact=severity, lang=lang)
            .only("body")
            .first()
        )
        if row and row.body:
            return row.body, hazard, severity, "ALERT_TEMPLATE"
    if not isinstance(alert_type, AlertType):
        alert_type = AlertType.objects.filter(pk=code).first()
    if alert_type is not None:
        column = alert_type.template_en if lang == "en" else alert_type.template_sw
        if column:
            return column, hazard, severity, "ALERT_TYPES"
    copy = DEFAULT_ALERT_COPY.get(code, {})
    text = copy.get(lang) or copy.get("sw")
    if not text:
        raise LookupError(f"no template for alert type {code} ({lang})")
    return text, hazard, severity, "DEFAULT"


def render_alert(alert_type, lang, severity=None, **slots):
    template, hazard, severity, source = alert_template(alert_type, lang, severity)
    body = fill(template, **slots)
    if len(body) > SMS_SEGMENT * MAX_SEGMENTS:
        logger.warning("alert %s (%s) renders to %d chars (> 2 SMS)", alert_type, lang, len(body))
    return body, hazard, severity, source


def render_system(key, lang, **slots):
    copy = SYSTEM_COPY[key]
    return fill(copy.get(lang) or copy["sw"], **slots)
