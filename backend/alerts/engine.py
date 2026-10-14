"""Alert engine (ARCHITECTURE §5, M3): ACT scores -> ALERTS -> MESSAGES.

Flow, matching the APEX app's Alert Approvals page (page 3):
  1. draft_alerts(as_of)   one ALERT per (ACT score, language of the ward's eligible subscribers),
                           body rendered from ALERT_TEMPLATE, status PENDING_APPROVAL
                           (APPROVED straight away when ALERTS_REQUIRE_APPROVAL is off).
  2. officer approves      APEX sets status APPROVED (or REJECTED), or POST /alerts/{id}/approve.
  3. dispatch_approved()   fans each APPROVED alert out to its subscribers as queued MESSAGES,
                           applying the per-subscriber cooldown and the weekly routine cap.
  4. sms dispatcher        sends; the alert becomes SENT with sent_at.
Watch and Abstain never produce SMS: they are for officers on the dashboard.
"""
import datetime as dt
import logging
import math

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from alerts.catalogue import DEFAULT_AUDIENCE
from alerts.templates import render_alert
from ngao.models import Alert, Message, RiskScore, Subscriber
from ngao_core.errors import ConflictError, NotFoundError
from ngao_core.timeutil import today_eat, weekday_name
from sms.dispatcher import kick, queue_sms

logger = logging.getLogger(__name__)

ROUTE_DRIVEN = {"A5"}  # booking SMS come from the dryer router, not from scores


def audience_roles(alert_type):
    if alert_type.audience_roles:
        roles = [r.strip().lower() for r in alert_type.audience_roles.replace(";", ",").split(",")]
        roles = [r for r in roles if r]
        if roles:
            return roles
    return DEFAULT_AUDIENCE.get(alert_type.service, [])


def eligible_subscribers(ward_id, alert_type):
    """ACTIVE subscribers who consented, live in the ward and are in the alert's audience."""
    return Subscriber.objects.filter(
        ward_id=ward_id, status=Subscriber.ACTIVE, consent_at__isnull=False,
        role__in=audience_roles(alert_type),
    )


def _slots(score, lang):
    day = score.score_date + dt.timedelta(days=1)
    if score.alert_type_id == "A1":
        try:
            from scoring.service import slot_hints

            day = slot_hints(score).get("day", day)
        except Exception:  # hints are cosmetic; never block an alert on them
            logger.exception("slot hints failed for score %s", score.pk)
    return {
        "ward": score.ward.ward_name,
        "confidence": score.confidence_pct if score.confidence_pct is not None
        else round((score.probability or 0) * 100),
        "day": weekday_name(day, lang),
    }


def draft_alerts(as_of=None, dry_run=False, county=None):
    as_of = as_of or today_eat()
    require_approval = settings.NGAO_ALERTS_REQUIRE_APPROVAL
    summary = {"as_of": as_of.isoformat(), "dry_run": dry_run, "approval_required": require_approval,
               "act_scores": 0, "alerts_created": 0, "pending_approval": 0, "messages_queued": 0,
               "suppressed_cooldown": 0, "suppressed_cap": 0, "already_drafted": 0,
               "no_subscribers": 0, "preview": []}
    scores = (
        RiskScore.objects.filter(score_date=as_of, decision=RiskScore.ACT, is_replay=False)
        .exclude(alert_type__in=ROUTE_DRIVEN)
        .select_related("ward", "alert_type")
        .order_by("ward__ward_name", "alert_type")
    )
    if county:
        scores = scores.filter(ward__county__iexact=county)

    now = timezone.now()
    with transaction.atomic():
        for score in scores:
            summary["act_scores"] += 1
            alert_type = score.alert_type
            langs = sorted(set(
                eligible_subscribers(score.ward_id, alert_type).values_list("preferred_lang", flat=True)
            ))
            if not langs:
                summary["no_subscribers"] += 1
                continue
            for lang in langs:
                if Alert.objects.filter(score=score, lang=lang).exists():
                    summary["already_drafted"] += 1
                    continue
                since = now - dt.timedelta(hours=alert_type.cooldown_hours or 48)
                recent = (
                    Alert.objects.filter(ward_id=score.ward_id, alert_type=alert_type, lang=lang, created_at__gte=since)
                    .exclude(status__in=[Alert.REJECTED, Alert.SUPPRESSED])
                )
                if recent.exists():
                    summary["suppressed_cooldown"] += 1
                    continue
                body, hazard, severity, source = render_alert(alert_type, lang, **_slots(score, lang))
                if dry_run:
                    summary["alerts_created"] += 1
                    if len(summary["preview"]) < 5:
                        summary["preview"].append({"ward": score.ward.ward_name, "alert_type": alert_type.pk,
                                                   "lang": lang, "body": body, "template_source": source})
                    continue
                alert = Alert.objects.create(
                    score=score, alert_type=alert_type, ward_id=score.ward_id, hazard=hazard,
                    severity=severity, lang=lang, body=body,
                    status=Alert.PENDING_APPROVAL if require_approval else Alert.APPROVED,
                    approved_by=None if require_approval else "auto",
                    approved_at=None if require_approval else now,
                )
                summary["alerts_created"] += 1
                if require_approval:
                    summary["pending_approval"] += 1
                else:
                    result = fan_out(alert)
                    summary["messages_queued"] += result["messages_queued"]
                    summary["suppressed_cooldown"] += result["suppressed_cooldown"]
                    summary["suppressed_cap"] += result["suppressed_cap"]
    if summary["messages_queued"]:
        kick()
    if not dry_run:
        summary.pop("preview")
    return summary


def _routine_count_7d(subscriber):
    since = timezone.now() - dt.timedelta(days=7)
    return (
        Message.objects.filter(subscriber=subscriber, direction=Message.OUT, alert__isnull=False,
                               alert__alert_type__overrides_cap=False, created_at__gte=since)
        .exclude(status=Message.FAILED)
        .count()
    )


def fan_out(alert):
    """Queue one SMS per eligible subscriber for an APPROVED alert."""
    result = {"messages_queued": 0, "suppressed_cooldown": 0, "suppressed_cap": 0}
    alert_type = alert.alert_type
    cap = settings.NGAO_WEEKLY_ROUTINE_CAP
    since = timezone.now() - dt.timedelta(hours=alert_type.cooldown_hours or 48)
    subscribers = eligible_subscribers(alert.ward_id, alert_type).filter(preferred_lang=alert.lang)
    for subscriber in subscribers:
        if Message.objects.filter(alert=alert, subscriber=subscriber).exists():
            continue
        recent = (
            Message.objects.filter(subscriber=subscriber, alert__alert_type=alert_type, created_at__gte=since)
            .exclude(status=Message.FAILED)
        )
        if recent.exists():
            result["suppressed_cooldown"] += 1
            continue
        if not alert_type.overrides_cap and _routine_count_7d(subscriber) >= cap:
            result["suppressed_cap"] += 1
            continue
        queue_sms(subscriber.phone, alert.body, subscriber=subscriber, alert=alert)
        result["messages_queued"] += 1
    if result["messages_queued"] == 0 and not alert.messages.exists():
        alert.status = Alert.SUPPRESSED
        alert.save(update_fields=["status"])
    return result


def _dispatchable(alert):
    """Never send a replay alert or an approval that arrived too late to be useful."""
    if alert.score_id:
        if alert.score.is_replay:
            return False, "replay"
        max_age_days = math.ceil(settings.NGAO_ALERT_MAX_AGE_HOURS / 24)
        if alert.score.score_date < today_eat() - dt.timedelta(days=max_age_days):
            return False, "stale"
    if alert.created_at < timezone.now() - dt.timedelta(hours=settings.NGAO_ALERT_MAX_AGE_HOURS):
        return False, "stale"
    return True, None


def dispatch_approved():
    """Fan out every APPROVED alert that has no messages yet (approved in APEX or via REST)."""
    summary = {"alerts_dispatched": 0, "messages_queued": 0, "suppressed_cooldown": 0,
               "suppressed_cap": 0, "skipped_replay": 0, "skipped_stale": 0}
    pending = (
        Alert.objects.filter(status=Alert.APPROVED, messages__isnull=True)
        .select_related("score", "alert_type")
    )
    with transaction.atomic():
        for alert in pending:
            ok, why = _dispatchable(alert)
            if not ok:
                summary[f"skipped_{why}"] += 1
                continue
            result = fan_out(alert)
            summary["alerts_dispatched"] += 1
            for key in ("messages_queued", "suppressed_cooldown", "suppressed_cap"):
                summary[key] += result[key]
    if summary["messages_queued"]:
        kick()
    return summary


def approve(alert_id, approved_by):
    with transaction.atomic():
        alert = Alert.objects.select_related("alert_type", "score").filter(pk=alert_id).first()
        if alert is None:
            raise NotFoundError(f"alert {alert_id} not found")
        if alert.status != Alert.PENDING_APPROVAL:
            raise ConflictError(f"alert {alert_id} is {alert.status}, not PENDING_APPROVAL")
        alert.status, alert.approved_by, alert.approved_at = Alert.APPROVED, approved_by, timezone.now()
        alert.save(update_fields=["status", "approved_by", "approved_at"])
        ok, why = _dispatchable(alert)
        result = fan_out(alert) if ok else {"messages_queued": 0, "skipped": why}
    if result.get("messages_queued"):
        kick()
    alert.refresh_from_db()
    return alert, result


def reject(alert_id, rejected_by):
    updated = Alert.objects.filter(pk=alert_id, status=Alert.PENDING_APPROVAL).update(
        status=Alert.REJECTED, approved_by=rejected_by, approved_at=timezone.now()
    )
    alert = Alert.objects.filter(pk=alert_id).first()
    if alert is None:
        raise NotFoundError(f"alert {alert_id} not found")
    if not updated:
        raise ConflictError(f"alert {alert_id} is {alert.status}, not PENDING_APPROVAL")
    return alert
