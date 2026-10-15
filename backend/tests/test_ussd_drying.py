"""USSD menu 7 (API_CONTRACT §5): drying state, slot confirmation, and drying requests at a chosen point."""
import datetime as dt

from ngao.models import Booking, CollectionPoint, Report, RouteStop
from ngao_core.timeutil import today_eat
from tests.base import NgaoTestCase

TOMORROW = today_eat() + dt.timedelta(days=1)
FARMER = "+254700000104"  # Kapseret (ward 13)


class DryingTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.score_today()

    def plan(self):
        return self.job("plan-routes", {"route_date": TOMORROW.isoformat()})["summary"]

    def drying(self, phone):
        response = self.ussd.get("/ngao/v1/ussd/drying", {"phone": phone})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def request_drying(self, phone, bags, point_id=None):
        body = {"phone": phone, "session_id": "s9", "report_type": "wet_bags", "count": bags, "unit": "bags"}
        if point_id is not None:
            body["point_id"] = point_id
        return self.ussd.post("/ngao/v1/ussd/report", body, format="json")

    def test_registered_without_booking_lists_ward_points(self):
        state = self.drying(FARMER)
        self.assertEqual(state["registered"], True)
        self.assertIsNone(state["booking"])
        self.assertEqual([p["point_name"] for p in state["points"]], ["Kapseret coop store"])

    def test_unknown_phone_is_not_registered(self):
        self.assertEqual(self.drying("+254799999999"), {"registered": False, "booking": None, "points": []})

    def test_offered_booking_then_confirm(self):
        self.assertEqual(self.request_drying(FARMER, 40).status_code, 201)
        self.plan()
        booking = self.drying(FARMER)["booking"]
        self.assertEqual((booking["status"], booking["point_name"], booking["bags"]),
                         ("OFFERED", "Kapseret coop store", 40))
        self.assertTrue(booking["eta"].startswith(f"{TOMORROW}T"))

        response = self.ussd.post("/ngao/v1/ussd/drying/confirm",
                                  {"phone": FARMER, "booking_id": booking["booking_id"]}, format="json")
        self.assertEqual(response.json(), {"booking_id": booking["booking_id"], "status": "CONFIRMED"})
        self.assertEqual(self.drying(FARMER)["booking"]["status"], "CONFIRMED")
        again = self.ussd.post("/ngao/v1/ussd/drying/confirm",
                               {"phone": FARMER, "booking_id": booking["booking_id"]}, format="json")
        self.assertEqual(again.status_code, 200)

    def test_confirm_someone_elses_booking_is_404(self):
        self.request_drying(FARMER, 40)
        self.plan()
        booking = Booking.objects.get(subscriber__phone=FARMER)
        response = self.ussd.post("/ngao/v1/ussd/drying/confirm",
                                  {"phone": "+254700000105", "booking_id": booking.pk}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Booking.objects.get(pk=booking.pk).status, "OFFERED")

    def test_dried_booking_conflicts(self):
        self.request_drying(FARMER, 40)
        self.plan()
        booking = Booking.objects.get(subscriber__phone=FARMER)
        Booking.objects.filter(pk=booking.pk).update(status="DRIED")
        response = self.ussd.post("/ngao/v1/ussd/drying/confirm",
                                  {"phone": FARMER, "booking_id": booking.pk}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_point_id_is_stored_and_routes_to_that_point(self):
        depot = CollectionPoint.objects.get(point_name="Tembelio depot")
        response = self.request_drying(FARMER, 30, point_id=depot.pk)
        self.assertEqual(response.status_code, 201, response.content)
        report = Report.objects.get(pk=response.json()["report_id"])
        self.assertEqual(report.payload["point_id"], depot.pk)
        self.assertEqual(report.ward_id, 13)  # still the farmer's ward
        self.plan()
        self.assertEqual(list(RouteStop.objects.values_list("point__point_name", flat=True)), ["Tembelio depot"])

    def test_unknown_point_is_rejected(self):
        response = self.request_drying(FARMER, 30, point_id=99999)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["field"], "point_id")

    def test_requires_ussd_token(self):
        self.assertEqual(self.anon.get("/ngao/v1/ussd/drying", {"phone": FARMER}).status_code, 401)
