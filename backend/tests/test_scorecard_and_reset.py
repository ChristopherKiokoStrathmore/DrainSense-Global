import datetime as dt

from django.utils import timezone

from ngao.models import Alert, Message, Outcome, Report, RiskScore, Route, Subscriber, WardFeatureDaily
from ngao_core.phones import InvalidPhone, normalize_phone
from ngao_core.timeutil import EAT, clock_text, today_eat
from tests.base import NgaoTestCase


class GradingTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.day = today_eat() - dt.timedelta(days=45)  # before the synthetic feature rows
        self.score = RiskScore.objects.create(ward_id=13, score_date=self.day, alert_type_id="A2", probability=0.8,
                                              confidence_pct=80, decision="ACT")
        subscriber = Subscriber.objects.get(phone="+254700000104")
        Alert.objects.create(score=self.score, subscriber=subscriber, alert_type_id="A2", ward_id=13, lang="sw",
                             body="x", status="SENT", sent_at=timezone.now())

    def observe(self, mm_by_offset):
        for offset, mm in mm_by_offset.items():
            WardFeatureDaily.objects.update_or_create(
                ward_id=13, feature_date=self.day + dt.timedelta(days=offset),
                defaults={"available_date": self.day + dt.timedelta(days=offset), "rain_1d_mm": mm})

    def test_heavy_rain_hit(self):
        self.observe({1: 4.0, 2: 31.0})
        summary = self.job("grade-outcomes")["summary"]
        self.assertEqual((summary["graded"], summary["hits"]), (1, 1))
        outcome = Outcome.objects.get()
        self.assertEqual((outcome.hit, outcome.lead_days), (True, 2))
        row = self.anon.get("/ngao/v1/public/scorecard").json()["items"][0]
        self.assertEqual((row["alert_type"], row["alerts_sent"], row["hits"], row["hit_rate"]), ("A2", 1, 1, 1.0))
        self.assertEqual(row["avg_lead_days"], 2.0)
        self.assertEqual(set(row), {"alert_type", "period", "alerts_sent", "hits", "false_alarms", "hit_rate",
                                    "false_alarm_rate", "avg_lead_days", "brier_skill"})

    def test_false_alarm(self):
        self.observe({1: 3.0, 2: 1.0})
        self.job("grade-outcomes")
        row = self.anon.get("/ngao/v1/public/scorecard").json()["items"][0]
        self.assertEqual((row["false_alarms"], row["false_alarm_rate"]), (1, 1.0))

    def test_waits_for_observations(self):
        self.observe({1: 3.0})
        summary = self.job("grade-outcomes")["summary"]
        self.assertEqual(summary["waiting_for_data"], 1)
        self.assertFalse(Outcome.objects.exists())

    def test_calibration(self):
        self.observe({1: 3.0, 2: 40.0})
        self.job("grade-outcomes")
        rows = self.anon.get("/ngao/v1/public/calibration").json()["items"]
        self.assertEqual(rows, [{"alert_type": "A2", "prob_bin": 0.8, "predicted": 0.8, "observed": 1.0, "n": 1}])


class ResetDemoTests(NgaoTestCase):
    DEMO = "+254711000001"

    def test_reset_removes_presenter_state_only(self):
        self.ussd.post("/ngao/v1/ussd/language", {"phone": self.DEMO, "preferred_lang": "sw"}, format="json")
        self.ussd.post("/ngao/v1/ussd/register", {"phone": self.DEMO, "ward_id": 12, "role": "farmer", "consent": True},
                       format="json")
        self.api.post("/ngao/v1/demo/inbound-sms", {"phone": self.DEMO, "text": "magunia 20 mabichi"}, format="json")
        self.score_today()
        self.job("send-alerts")
        self.job("plan-routes")
        self.assertTrue(Subscriber.objects.get(phone=self.DEMO).is_demo)
        summary = self.job("reset-demo")["summary"]
        self.assertEqual(summary["subscribers"], 1)
        self.assertFalse(Subscriber.objects.filter(phone=self.DEMO).exists())
        self.assertFalse(Message.objects.filter(phone=self.DEMO).exists())
        self.assertFalse(Alert.objects.exists())
        self.assertFalse(Route.objects.exists())
        self.assertFalse(Report.objects.exists())
        self.assertEqual(Subscriber.objects.filter(is_demo=True).count(), 11)  # synthetic set untouched
        self.assertTrue(RiskScore.objects.exists())


class UtilTests(NgaoTestCase):
    def test_phone_forms(self):
        for raw in ("+254712345678", "254712345678", "0712345678", "712345678", "0712 345 678", "+254 712-345-678"):
            self.assertEqual(normalize_phone(raw), "+254712345678")
        self.assertEqual(normalize_phone("0110123456"), "+254110123456")
        for bad in ("12345", "+1 202 555 0100", "", None):
            with self.assertRaises(InvalidPhone):
                normalize_phone(bad)
        self.assertEqual(normalize_phone("+1 202 555 0100", kenya_only=False), "+12025550100")

    def test_swahili_time(self):
        def at(h, m=0):
            return dt.datetime(2026, 10, 16, h, m, tzinfo=EAT)
        self.assertEqual(clock_text(at(8), "sw"), "saa 2 asubuhi")
        self.assertEqual(clock_text(at(14, 30), "sw"), "saa 8:30 mchana")
        self.assertEqual(clock_text(at(18), "sw"), "saa 12 jioni")
        self.assertEqual(clock_text(at(21), "sw"), "saa 3 usiku")
        self.assertEqual(clock_text(at(8), "en"), "8:00 AM")
