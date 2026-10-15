"""Dryer router: urgency, greedy fill, ETAs, bookings, SMS, confirmations, drying outcome."""
import datetime as dt

from ngao.models import Booking, Dryer, Message, Outcome, Report, Route, RouteStop
from ngao_core.timeutil import EAT, today_eat
from routing.service import bags_per_day, fee_per_bag
from tests.base import NgaoTestCase

TOMORROW = today_eat() + dt.timedelta(days=1)


class RouterTests(NgaoTestCase):
    def report(self, phone, bags):
        response = self.api.post("/ngao/v1/demo/inbound-sms", {"phone": phone, "text": f"magunia {bags} mabichi"},
                                 format="json")
        self.assertEqual(response.status_code, 201)

    def plan(self, **body):
        return self.job("plan-routes", {"route_date": TOMORROW.isoformat(), **body})["summary"]

    def setUp(self):
        super().setUp()
        self.score_today()  # Kapseret has A2 ACT (rain due), Moiben has no A2 row (default 0.5)
        self.report("+254700000101", 60)   # Moiben
        self.report("+254700000104", 60)   # Kapseret

    def test_rain_risk_puts_kapseret_first(self):
        summary = self.plan()
        self.assertEqual((summary["routes"], summary["stops"], summary["bookings"]), (2, 2, 2))
        routes = self.api.get(f"/ngao/v1/routes?date={TOMORROW}").json()["routes"]
        first = next(r for r in routes if r["dryer_id"] == Dryer.objects.order_by("dryer_id").first().dryer_id)
        stop = first["stops"][0]
        self.assertEqual(stop["point_name"], "Kapseret coop store")  # most urgent -> biggest dryer
        self.assertEqual(stop["stop_order"], 1)
        self.assertTrue(stop["eta"].endswith("+03:00"))
        self.assertTrue(stop["eta"].startswith(f"{TOMORROW}T08:00:00"))
        self.assertGreater(stop["urgency"], 50)

    def test_booking_sms_in_swahili_with_swahili_time(self):
        self.plan()
        body = Message.objects.filter(phone="+254700000104", body__startswith="MvuaSmart: Mashine").get().body
        self.assertIn("Kapseret coop store", body)
        self.assertIn("saa 2 asubuhi", body)  # 08:00 EAT
        self.assertIn("KES 210 kwa gunia", body)  # (20% - 13%) x KES 30
        self.assertIn("Jibu 1", body)

    def test_operator_gets_stop_list(self):
        self.plan()
        operator_sms = Message.objects.filter(phone="+254700000901").get().body
        self.assertIn("Kapseret coop store 08:00 (60 magunia)", operator_sms)
        self.assertEqual(set(Route.objects.values_list("status", flat=True)), {"NOTIFIED"})

    def test_capacity_split_and_unserved(self):
        Dryer.objects.update(tonnes_per_hour=0.5, hours_per_day=8.0)  # 44 bags/day each
        summary = self.plan()
        self.assertEqual(summary["planned_bags"], 88)
        self.assertEqual(summary["unserved_bags"], 120 - 88)

    def test_replan_replaces_unconfirmed_routes(self):
        self.plan()
        summary = self.plan()
        self.assertEqual(summary["replaced_routes"], 2)
        self.assertEqual(Route.objects.count(), 2)

    def test_replan_after_confirmation_conflicts(self):
        self.plan()
        booking = Booking.objects.first()
        self.api.post(f"/ngao/v1/bookings/{booking.pk}/confirm")
        response = self.api.post("/ngao/v1/jobs/plan-routes", {"route_date": TOMORROW.isoformat()}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_confirm_booking_endpoint(self):
        self.plan()
        booking = Booking.objects.get(subscriber__phone="+254700000104")
        response = self.api.post(f"/ngao/v1/bookings/{booking.pk}/confirm")
        self.assertEqual(response.json(), {"booking_id": booking.pk, "status": "CONFIRMED"})
        confirmation = Message.objects.filter(phone="+254700000104").latest("message_id").body
        self.assertIn("imethibitishwa", confirmation)
        self.assertEqual(self.api.post("/ngao/v1/bookings/99999/confirm").status_code, 404)

    def test_reply_1_confirms(self):
        self.plan()
        self.anon.post("/ngao/v1/sms/inbound", {"from": "+254700000104", "text": "1"}, format="json")
        self.assertEqual(Booking.objects.get(subscriber__phone="+254700000104").status, "CONFIRMED")
        self.assertTrue(Report.objects.filter(report_type="booking_confirm").exists())

    def test_rejected_reports_are_not_demand(self):
        Report.objects.update(review_status="REJECTED")
        self.assertEqual(self.plan()["routes"], 0)

    def test_latest_report_per_farmer(self):
        self.report("+254700000101", 30)  # correction: 30, not 60 + 30
        self.plan()
        moiben = RouteStop.objects.get(point__point_name="Moiben market")
        self.assertEqual(moiben.planned_bags, 30)


class DryingOutcomeTests(NgaoTestCase):
    def test_operator_report_closes_route(self):
        dryer = Dryer.objects.get(operator_phone="+254700000901")
        route = Route.objects.create(dryer=dryer, route_date=today_eat(), status="NOTIFIED")
        self.anon.post("/ngao/v1/sms/inbound", {"from": "+254700000901", "text": "Dried 120 bags, 13%"}, format="json")
        outcome = Outcome.objects.get()
        self.assertEqual((outcome.outcome_type, outcome.tonnes_dried), ("bags_dried", 10.8))
        route.refresh_from_db()
        self.assertEqual(route.status, "DONE")


class MathTests(NgaoTestCase):
    def test_eta_rounding(self):
        from routing.service import round_up_5min

        base = dt.datetime(2026, 10, 16, 11, 12, 40, tzinfo=EAT)
        self.assertEqual(round_up_5min(base).strftime("%H:%M:%S"), "11:15:00")
        self.assertEqual(round_up_5min(base.replace(minute=10, second=0)).strftime("%H:%M"), "11:10")
        self.assertEqual(round_up_5min(base.replace(minute=58, second=1)).strftime("%H:%M"), "12:00")

    def test_capacity_formula(self):
        self.assertEqual(bags_per_day(Dryer(tonnes_per_hour=2.0, hours_per_day=8.0)), 177)

    def test_fee(self):
        self.assertEqual(fee_per_bag(20), 210)
        self.assertEqual(fee_per_bag(12), 0)
        self.assertEqual(fee_per_bag(None), 210)
