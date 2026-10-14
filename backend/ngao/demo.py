"""POST /jobs/reset-demo: put the live demo back to its starting point between rehearsals.

It never touches the synthetic dataset as a whole. It removes:
  - everything belonging to the presenter phones (DEMO_PHONES, or "phones" in the body):
    subscriber row (so the USSD language prompt shows again), messages, reports, bookings,
    farms, herds and USSD sessions;
  - alerts drafted today (EAT) and their messages/outcomes, so "Send alerts" can run again
    ("alerts_today": false to keep them);
  - dryer routes from today on, with stops and bookings ("routes": false to keep them).
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Q

from ngao.models import (Alert, Booking, Farm, Herd, Message, Outcome, Report, Route, RouteStop, Subscriber,
                         UssdSession)
from ngao_core.errors import InvalidInput
from ngao_core.phones import InvalidPhone, normalize_phone
from ngao_core.timeutil import start_of_day_eat, today_eat


def reset_demo(phones=None, alerts_today=True, routes=True):
    try:
        phones = [normalize_phone(p) for p in (phones if phones is not None else settings.NGAO_DEMO_PHONES)]
    except InvalidPhone as exc:
        raise InvalidInput(str(exc), field="phones") from exc
    today = today_eat()
    summary = {"phones": len(phones)}
    with transaction.atomic():
        subscribers = Subscriber.objects.filter(phone__in=phones)
        alerts = Alert.objects.filter(subscriber__in=subscribers)  # legacy per-subscriber rows
        if alerts_today:
            alerts = Alert.objects.filter(Q(pk__in=alerts) | Q(created_at__gte=start_of_day_eat(today)))
        alert_ids = list(alerts.values_list("pk", flat=True))
        messages = Message.objects.filter(Q(phone__in=phones) | Q(subscriber__in=subscribers) | Q(alert_id__in=alert_ids))
        message_ids = list(messages.values_list("pk", flat=True))
        route_ids = list(Route.objects.filter(route_date__gte=today).values_list("pk", flat=True)) if routes else []

        summary["reports"] = Report.objects.filter(message_id__in=message_ids).delete()[0]
        summary["outcomes"] = Outcome.objects.filter(alert_id__in=alert_ids).delete()[0]
        summary["bookings"] = Booking.objects.filter(Q(subscriber__in=subscribers) | Q(stop__route_id__in=route_ids)).delete()[0]
        RouteStop.objects.filter(route_id__in=route_ids).delete()
        summary["routes"] = Route.objects.filter(pk__in=route_ids).delete()[0]
        Alert.objects.filter(message_id__in=message_ids).exclude(pk__in=alert_ids).update(message=None)
        summary["messages"] = Message.objects.filter(pk__in=message_ids).delete()[0]
        summary["alerts"] = Alert.objects.filter(pk__in=alert_ids).delete()[0]
        Farm.objects.filter(subscriber__in=subscribers).delete()
        Herd.objects.filter(subscriber__in=subscribers).delete()
        UssdSession.objects.filter(Q(subscriber__in=subscribers) | Q(phone__in=phones)).delete()
        summary["subscribers"] = Subscriber.objects.filter(phone__in=phones).delete()[0]
    return summary
