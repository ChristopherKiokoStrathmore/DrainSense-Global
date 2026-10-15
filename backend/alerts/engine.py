"""Alert engine (ARCHITECTURE §5): ACT scores -> one ALERTS row per subscriber -> one SMS.

Flow, matching the live APEX Alert Approvals page (page 3, V_ALERT_QUEUE):
  1. draft_alerts(as_of)   one ALERT per eligible subscriber of an ACT score. Each row has
                           score_id, subscriber_id, ward_id, alert_type and a body of at most
                           160 characters. Status is PENDING_APPROVAL (APPROVED immediately
                           when ALERTS_REQUIRE_APPROVAL is off).
  2. officer approves      APEX sets that row to APPROVED (or REJECTED), or POST /alerts/{id}/approve.
  3. dispatch_approved()   queues one SMS for each APPROVED row that has no MESSAGE_ID yet,
                           applying the per-subscriber cooldown and the weekly routine cap.
  4. sms dispatcher        sends; the alert becomes SENT and ALERTS.MESSAGE_ID points at the
                           MESSAGES row. MESSAGES has no alert_id column on the live database.
Watch and Abstain never produce SMS: they are for officers on the dashboard.
Status values written here are the ones the approvals page already uses:
PENDING_APPROVAL, APPROVED, REJECTED, SENT.
"""
import datetime as dt
import logging
import math

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from alerts.catalogue import DEFAULT_AUDIENCE
from alerts.templates import fit_alert_body, render_alert
from ngao.models import Alert, RiskScore, Subscriber
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


def ensure_risk_score(ward_id, alert_type, score_date, is_replay=False, score=None, **defaults):
    """Return the RISK_SCORES row for (ward, date, alert type, replay), creating it if needed.

    Live RISK_SCORES is unique on that key, and ALERTS.SCORE_ID is NOT NULL and a foreign key.
    An existing row (Trevor's model, or an earlier score run) is reused and not overwritten.
    """
    if score is not None:
        return score
    alert_type_id = getattr(alert_type, "pk", alert_type)
    row, _created = RiskScore.objects.get_or_create(
        ward_id=ward_id, score_date=score_date, alert_type_id=alert_type_id, is_replay=is_replay,
        defaults={"decision": RiskScore.ACT, "model_version": "backend-alert", **defaults},
    )
    return row


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


def _still_eligible(subscriber, alert_type):
    return (
        subscriber is not None
        and subscriber.status == Subscriber.ACTIVE
        and subscriber.consent_at is not None
        and subscriber.role in audience_roles(alert_type)
    )


def draft_alerts(as_of=None, dry_run=False, county=None):
    as_of = as_of or today_eat()
    require_approval = settings.NGAO_ALERTS_REQUIRE_APPROVAL
    cap = settings.NGAO_WEEKLY_ROUTINE_CAP
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
            subscribers = list(eligible_subscribers(score.ward_id, alert_type))
            if not subscribers:
                summary["no_subscribers"] += 1
                continue
            for subscriber in subscribers:
                if Alert.objects.filter(score=score, subscriber=subscriber).exists():
                    summary["already_drafted"] += 1
                    continue
                since = now - dt.timedelta(hours=alert_type.cooldown_hours or 48)
                recent = (
                    Alert.objects.filter(subscriber=subscriber, alert_type=alert_type, created_at__gte=since)
                    .exclude(status=Alert.REJECTED)
                )
                if recent.exists():
                    summary["suppressed_cooldown"] += 1
                    continue
                if not alert_type.overrides_cap and _routine_count_7d(subscriber) >= cap:
                    summary["suppressed_cap"] += 1
                    continue
                lang = (subscriber.preferred_lang or "sw")[:2]
                body, hazard, severity, source = render_alert(alert_type, lang, **_slots(score, lang))
                body = fit_alert_body(body)
                if dry_run:
                    summary["alerts_created"] += 1
                    if len(summary["preview"]) < 5:
                        summary["preview"].append({"ward": score.ward.ward_name, "alert_type": alert_type.pk,
                                                   "lang": lang, "body": body, "template_source": source})
                    continue
                # score is already the unique (ward, date, type, replay) row; ensure_risk_score
                # is what a caller without that row uses, and it must not insert a second one.
                score_row = ensure_risk_score(
                    score.ward_id, alert_type, score.score_date, is_replay=score.is_replay, score=score,
                )
                alert = Alert.objects.create(
                    score=score_row, subscriber=subscriber, alert_type=alert_type, ward_id=score.ward_id,
                    hazard=hazard, severity=severity, lang=lang, body=body,
                    status=Alert.PENDING_APPROVAL if require_approval else Alert.APPROVED,
                    approved_by=None if require_approval else "auto",
                    approved_at=None if require_approval else now,
                )
                summary["alerts_created"] += 1
                if require_approval:
                    summary["pending_approval"] += 1
                else:
                    result = deliver_alert(alert)
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
    return Alert.objects.filter(
        subscriber=subscriber, status=Alert.SENT, alert_type__overrides_cap=False, created_at__gte=since,
    ).count()


def deliver_alert(alert):
    """Queue the single SMS for this subscriber alert and set ALERTS.MESSAGE_ID."""
    result = {"messages_queued": 0, "suppressed_cooldown": 0, "suppressed_cap": 0}
    locked = (
        Alert.objects.select_for_update()
        .select_related("subscriber", "alert_type")
        .get(pk=alert.pk)
    )
    if locked.message_id:
        return result
    subscriber = locked.subscriber
    alert_type = locked.alert_type
    if not _still_eligible(subscriber, alert_type):
        return result
    since = timezone.now() - dt.timedelta(hours=alert_type.cooldown_hours or 48)
    recent = (
        Alert.objects.filter(subscriber=subscriber, alert_type=alert_type, message__isnull=False,
                             created_at__gte=since)
        .exclude(pk=locked.pk)
        .exclude(status=Alert.REJECTED)
    )
    if recent.exists():
        result["suppressed_cooldown"] += 1
        return result
    if not alert_type.overrides_cap and _routine_count_7d(subscriber) >= settings.NGAO_WEEKLY_ROUTINE_CAP:
        result["suppressed_cap"] += 1
        return result
    queue_sms(subscriber.phone, locked.body, subscriber=subscriber, alert=locked)
    result["messages_queued"] = 1
    return result


def _dispatchable(alert):
    """Never send a replay alert or an approval that arrived too late to be useful."""
    if alert.score_id and alert.score.is_replay:
        return False, "replay"
    max_age_days = math.ceil(settings.NGAO_ALERT_MAX_AGE_HOURS / 24)
    if alert.score_id and alert.score.score_date < today_eat() - dt.timedelta(days=max_age_days):
        return False, "stale"
    if alert.created_at < timezone.now() - dt.timedelta(hours=settings.NGAO_ALERT_MAX_AGE_HOURS):
        return False, "stale"
    return True, None


def dispatch_approved():
    """Send every APPROVED alert that has no MESSAGE_ID yet (approved in APEX or via REST)."""
    summary = {"alerts_dispatched": 0, "messages_queued": 0, "suppressed_cooldown": 0,
               "suppressed_cap": 0, "skipped_replay": 0, "skipped_stale": 0}
    pending = (
        Alert.objects.filter(status=Alert.APPROVED, message__isnull=True)
        .select_related("score", "alert_type", "subscriber")
    )
    with transaction.atomic():
        for alert in pending:
            ok, why = _dispatchable(alert)
            if not ok:
                summary[f"skipped_{why}"] += 1
                continue
            result = deliver_alert(alert)
            summary["alerts_dispatched"] += 1
            for key in ("messages_queued", "suppressed_cooldown", "suppressed_cap"):
                summary[key] += result[key]
    if summary["messages_queued"]:
        kick()
    return summary


def approve(alert_id, approved_by):
    with transaction.atomic():
        alert = (
            Alert.objects.select_related("alert_type", "score", "subscriber")
            .filter(pk=alert_id).first()
        )
        if alert is None:
            raise NotFoundError(f"alert {alert_id} not found")
        if alert.status != Alert.PENDING_APPROVAL:
            raise ConflictError(f"alert {alert_id} is {alert.status}, not PENDING_APPROVAL")
        alert.status, alert.approved_by, alert.approved_at = Alert.APPROVED, approved_by, timezone.now()
        alert.save(update_fields=["status", "approved_by", "approved_at"])
        ok, why = _dispatchable(alert)
        result = deliver_alert(alert) if ok else {"messages_queued": 0, "skipped": why}
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
