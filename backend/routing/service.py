"""Dryer router (ARCHITECTURE §6.3, M6): wet maize + rain risk -> tomorrow's dryer routes.

  demand    accepted wet_bags reports of the last 3 days, latest report per farmer, attributed to
            the ward's primary collection point (no GPS is stored, so points are ward-level)
  urgency   wet_bags x P(heavy rain 48 h, A2) x aflatoxin boost (A3 ACT 1.5, WATCH 1.2)
  capacity  bags/day = tonnes_per_hour x hours_per_day / 0.09 (90 kg bags)
  plan      greedy: most urgent point first, to the dryer with the most capacity left; a point
            larger than the capacity left is split across dryers
  eta       start 08:00 EAT; travel at 30 km/h (haversine) + drying time at each stop
Each stop books its farmers (BOOKINGS OFFERED) and queues the A5 booking SMS; every operator
gets a stop list. Farmers confirm with "1" (sms/keywords.py) or POST /bookings/{id}/confirm.
"""
import datetime as dt
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from alerts.templates import render_alert, render_system
from ngao.models import (Booking, CollectionPoint, Dryer, Outcome, Report, RiskScore, Route,
                         RouteStop, Subscriber)
from ngao_core.errors import ConflictError, NotFoundError
from ngao_core.timeutil import EAT, clock_text, iso_eat, today_eat, weekday_name
from sms.dispatcher import kick, queue_sms

logger = logging.getLogger(__name__)

POINT_KIND_PRIORITY = {"market": 0, "coop_store": 1, "depot": 2, "school": 3}
A3_BOOST = {"ACT": 1.5, "WATCH": 1.2}


@dataclass
class Demand:
    point: CollectionPoint
    bags: float = 0.0
    farmers: list = field(default_factory=list)  # (subscriber, bags, moisture_pct, reported_at)
    rain_prob: float = 0.0
    boost: float = 1.0

    @property
    def urgency(self):
        return round(self.bags * self.rain_prob * self.boost, 1)


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    if None in (a_lat, a_lon, b_lat, b_lon):
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def round_up_5min(moment):
    """ETAs go into SMS copy: 11:12:40 becomes 11:15."""
    minute = moment.replace(second=0, microsecond=0)
    if minute < moment:
        minute += dt.timedelta(minutes=1)
    return minute + dt.timedelta(minutes=(-minute.minute) % 5)


def bags_per_day(dryer):
    tph = float(dryer.tonnes_per_hour or 0)
    hours = float(dryer.hours_per_day or 0)
    return math.floor(tph * hours / settings.NGAO_ROUTER_BAG_TONNES)


def fee_per_bag(moisture_pct):
    start = moisture_pct if moisture_pct is not None else settings.NGAO_ROUTER_DEFAULT_MOISTURE_PCT
    points = max(0.0, float(start) - settings.NGAO_ROUTER_TARGET_MOISTURE_PCT)
    return round(points * settings.NGAO_ROUTER_KES_PER_POINT)


def _primary_points():
    by_ward = {}
    for point in CollectionPoint.objects.all().order_by("point_id"):
        current = by_ward.get(point.ward_id)
        rank = POINT_KIND_PRIORITY.get(point.kind, 9)
        if current is None or rank < POINT_KIND_PRIORITY.get(current.kind, 9):
            by_ward[point.ward_id] = point
    return by_ward


def _reporter(report):
    if report.message_id and report.message and report.message.subscriber_id:
        return report.message.subscriber
    return None


def collect_demand(plan_day):
    """Wet bags per collection point from the last 3 days of accepted wet_bags reports."""
    since = timezone.now() - dt.timedelta(days=3)
    points = _primary_points()
    reports = (
        Report.objects.filter(report_type="wet_bags", review_status__in=Report.USABLE,
                              created_at__gte=since, ward__isnull=False, report_count__gt=0)
        .select_related("message__subscriber")
        .order_by("created_at")
    )
    latest = {}  # one (latest) report per farmer; anonymous reports count on their own
    for report in reports:
        subscriber = _reporter(report)
        key = ("sub", subscriber.pk) if subscriber else ("rep", report.pk)
        latest[key] = (report, subscriber)

    demand = {}
    for report, subscriber in latest.values():
        point = points.get(report.ward_id)
        if point is None:
            continue
        entry = demand.setdefault(point.point_id, Demand(point=point))
        bags = float(report.report_count)
        entry.bags += bags
        moisture = (report.payload or {}).get("moisture_pct") if isinstance(report.payload, dict) else None
        entry.farmers.append((subscriber, bags, moisture, report.created_at))

    ward_ids = {d.point.ward_id for d in demand.values()}
    rain = {}
    boost = {}
    for score in (
        RiskScore.objects.filter(ward_id__in=ward_ids, is_replay=False, alert_type__in=["A2", "A3"],
                                 score_date__lte=plan_day, score_date__gte=plan_day - dt.timedelta(days=2))
        .order_by("score_date")
    ):  # later dates overwrite earlier ones
        if score.alert_type_id == "A2" and score.probability is not None:
            rain[score.ward_id] = float(score.probability)
        if score.alert_type_id == "A3":
            boost[score.ward_id] = A3_BOOST.get(score.decision, 1.0)
    for entry in demand.values():
        entry.rain_prob = rain.get(entry.point.ward_id, settings.NGAO_ROUTER_DEFAULT_RAIN_PROB)
        entry.boost = boost.get(entry.point.ward_id, 1.0)
    return [d for d in demand.values() if d.bags >= settings.NGAO_ROUTER_MIN_STOP_BAGS]


def _clear_existing(route_date, replace):
    routes = Route.objects.filter(route_date=route_date)
    if not routes.exists():
        return 0
    if Booking.objects.filter(stop__route__in=routes).exclude(status=Booking.OFFERED).exists() or \
            routes.filter(status=Route.DONE).exists():
        raise ConflictError(f"routes for {route_date} already have confirmed bookings",
                            {"route_date": route_date.isoformat()})
    if not replace:
        raise ConflictError(f"routes for {route_date} already exist; pass replace=true to re-plan",
                            {"route_date": route_date.isoformat()})
    stops = RouteStop.objects.filter(route__in=routes)
    Booking.objects.filter(stop__in=stops).delete()
    stops.delete()
    count = routes.count()
    routes.delete()
    return count


def plan_routes(route_date, replace=True, notify=True):
    plan_day = route_date - dt.timedelta(days=1)
    dryers = [d for d in Dryer.objects.all().order_by("dryer_id") if bags_per_day(d) > 0]
    summary = {"route_date": route_date.isoformat(), "dryers": len(dryers), "points_with_demand": 0,
               "routes": 0, "stops": 0, "planned_bags": 0, "unserved_bags": 0, "bookings": 0,
               "messages_queued": 0, "replaced_routes": 0, "placeholder_dryers": sum(d.is_placeholder for d in dryers)}
    with transaction.atomic():
        summary["replaced_routes"] = _clear_existing(route_date, replace)
        demand = sorted(collect_demand(plan_day), key=lambda d: (-d.urgency, -d.bags))
        summary["points_with_demand"] = len(demand)
        remaining = {d.dryer_id: bags_per_day(d) for d in dryers}
        assignments = defaultdict(list)  # dryer_id -> [(Demand, bags)]
        for entry in demand:
            left = entry.bags
            while left > 0:
                dryer_id = max(remaining, key=remaining.get, default=None)
                if dryer_id is None or remaining[dryer_id] <= 0:
                    break
                take = min(left, remaining[dryer_id])
                assignments[dryer_id].append((entry, take))
                remaining[dryer_id] -= take
                left -= take
            summary["unserved_bags"] += left

        start = dt.datetime.combine(route_date, dt.time(settings.NGAO_ROUTER_START_HOUR), tzinfo=EAT)
        booked = set()
        for dryer in dryers:
            stops = assignments.get(dryer.dryer_id)
            if not stops:
                continue
            route = Route.objects.create(dryer=dryer, route_date=route_date, status=Route.PLANNED)
            summary["routes"] += 1
            clock, previous = start, None
            stop_lines = []
            for order, (entry, bags) in enumerate(stops, start=1):
                point = entry.point
                if previous is not None:
                    km = haversine_km(previous.lat, previous.lon, point.lat, point.lon)
                    clock += dt.timedelta(hours=km / settings.NGAO_ROUTER_SPEED_KMH)
                clock = round_up_5min(clock)
                stop = RouteStop.objects.create(route=route, point=point, stop_order=order, eta=clock,
                                                urgency=entry.urgency, planned_bags=bags)
                summary["stops"] += 1
                summary["planned_bags"] += bags
                stop_lines.append((point.point_name, clock, bags))
                summary["bookings"] += _book_farmers(stop, entry, bags, booked, summary, notify)
                drying_hours = bags * settings.NGAO_ROUTER_BAG_TONNES / float(dryer.tonnes_per_hour)
                clock += dt.timedelta(hours=drying_hours)
                previous = point
            if notify and dryer.operator_phone:
                _notify_operator(dryer, route_date, stop_lines)
                summary["messages_queued"] += 1
            if notify:
                route.status = Route.NOTIFIED
                route.save(update_fields=["status"])
    if summary["messages_queued"]:
        kick()
    return summary


def _book_farmers(stop, entry, planned_bags, booked, summary, notify):
    """Offer the stop to its farmers, oldest report first, until the planned bags are used."""
    left = planned_bags
    count = 0
    for subscriber, bags, moisture, _ in sorted(entry.farmers, key=lambda f: f[3]):
        if subscriber is None or subscriber.pk in booked or subscriber.status != Subscriber.ACTIVE:
            continue
        if left <= 0:
            break
        take = min(bags, left)
        per_bag = fee_per_bag(moisture)
        Booking.objects.create(stop=stop, subscriber=subscriber, bags=take, fee_kes=per_bag * take,
                               status=Booking.OFFERED)
        booked.add(subscriber.pk)
        left -= take
        count += 1
        if notify:
            lang = subscriber.preferred_lang
            body, *_ = render_alert("A5", lang, point=stop.point.point_name,
                                    day=weekday_name(stop.route.route_date, lang),
                                    time=clock_text(stop.eta, lang), fee=per_bag)
            queue_sms(subscriber.phone, body, subscriber=subscriber)
            summary["messages_queued"] += 1
    return count


def _notify_operator(dryer, route_date, stop_lines):
    operator = Subscriber.objects.filter(phone=dryer.operator_phone).first()
    lang = operator.preferred_lang if operator else "sw"
    unit = "bags" if lang == "en" else "magunia"
    stops = "; ".join(
        f"{i}. {name} {eta.astimezone(EAT):%H:%M} ({round(bags)} {unit})"
        for i, (name, eta, bags) in enumerate(stop_lines, start=1)
    )
    body = render_system("operator_route", lang, dryer=dryer.name or dryer.dryer_id,
                         day=weekday_name(route_date, lang), stops=stops)
    queue_sms(dryer.operator_phone, body, subscriber=operator)


def routes_for(route_date):
    routes = (
        Route.objects.filter(route_date=route_date).select_related("dryer").order_by("dryer_id")
        .prefetch_related("stops__point", "stops__bookings")
    )
    out = []
    for route in routes:
        stops = []
        for stop in sorted(route.stops.all(), key=lambda s: s.stop_order):
            bookings = list(stop.bookings.all())
            stops.append({
                "stop_order": stop.stop_order, "stop_id": stop.stop_id, "point_id": stop.point_id,
                "point_name": stop.point.point_name, "ward_id": stop.point.ward_id, "eta": iso_eat(stop.eta),
                "planned_bags": stop.planned_bags, "urgency": stop.urgency,
                "bookings_offered": len(bookings),
                "bookings_confirmed": sum(b.status in (Booking.CONFIRMED, Booking.DRIED) for b in bookings),
                "bookings": [{"booking_id": b.booking_id, "subscriber_id": b.subscriber_id, "bags": b.bags,
                              "fee_kes": b.fee_kes, "status": b.status} for b in bookings],
            })
        out.append({"route_id": route.route_id, "dryer_id": route.dryer_id, "dryer_name": route.dryer.name,
                    "is_placeholder": route.dryer.is_placeholder, "status": route.status, "stops": stops})
    return out


def confirm_booking(booking_id=None, booking=None, notify=True):
    with transaction.atomic():
        booking = booking or Booking.objects.select_related("stop__point", "stop__route", "subscriber") \
            .filter(pk=booking_id).first()
        if booking is None:
            raise NotFoundError(f"booking {booking_id} not found")
        if booking.status == Booking.CONFIRMED:
            return booking
        if booking.status != Booking.OFFERED:
            raise ConflictError(f"booking {booking.pk} is {booking.status}")
        booking.status = Booking.CONFIRMED
        booking.save(update_fields=["status"])
        if notify:
            subscriber = booking.subscriber
            lang = subscriber.preferred_lang
            stop = booking.stop
            body = render_system("booking_confirmed", lang, bags=round(booking.bags or 0),
                                 point=stop.point.point_name, day=weekday_name(stop.route.route_date, lang),
                                 time=clock_text(stop.eta, lang) if stop.eta else "")
            queue_sms(subscriber.phone, body, subscriber=subscriber)
    if notify:
        kick()
    return booking


def open_booking_for(subscriber):
    """The farmer's soonest OFFERED booking that is still ahead (for an SMS reply of '1')."""
    return (
        Booking.objects.filter(subscriber=subscriber, status=Booking.OFFERED,
                               stop__route__route_date__gte=today_eat())
        .select_related("stop__point", "stop__route", "subscriber")
        .order_by("stop__route__route_date", "stop__eta")
        .first()
    )


def record_drying(report):
    """An operator's 'Dried 120 bags, 13%' closes today's route and logs tonnes dried (OUTCOMES)."""
    message = report.message
    if message is None or not report.report_count:
        return None
    dryer = Dryer.objects.filter(operator_phone=message.phone).first()
    outcome = Outcome.objects.create(
        outcome_type="bags_dried", hit=None,
        tonnes_dried=round(float(report.report_count) * settings.NGAO_ROUTER_BAG_TONNES, 2),
        observed_at=today_eat(),
    )
    if dryer:
        route = Route.objects.filter(dryer=dryer, route_date=today_eat()).first()
        if route:
            Booking.objects.filter(stop__route=route, status=Booking.CONFIRMED).update(status=Booking.DRIED)
            route.status = Route.DONE
            route.save(update_fields=["status"])
    return outcome
