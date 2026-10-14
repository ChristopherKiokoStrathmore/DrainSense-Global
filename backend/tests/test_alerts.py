"""Alert engine: draft -> officer approval (REST or APEX) -> fan-out -> SMS, with every guard."""
import datetime as dt

from django.test import override_settings
from django.utils import timezone

from alerts.templates import render_alert
from ngao.models import Alert, AlertTemplate, AlertType, Message, RiskScore, Subscriber
from ngao_core.timeutil import today_eat
from tests.base import NgaoTestCase


class DraftTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.score_today()

    def test_drafts_one_alert_per_language_pending_approval(self):
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["alerts_created"], 4)    # Moiben A1 sw+en, Kapseret A2 sw+en
        self.assertEqual(summary["pending_approval"], 4)
        self.assertEqual(summary["messages_queued"], 0)
        self.assertFalse(Message.objects.filter(direction="OUT").exists())
        sw = Alert.objects.get(ward_id=12, alert_type_id="A1", lang="sw")
        self.assertEqual(sw.status, "PENDING_APPROVAL")
        self.assertTrue(sw.body.startswith("MvuaSmart: Siku 3 za jua zinatarajiwa kuanzia "))
        self.assertIn(f"uhakika {sw.score.confidence_pct}%", sw.body)
        self.assertEqual((sw.hazard, sw.severity), ("dry_window", "warning"))
        en = Alert.objects.get(ward_id=12, alert_type_id="A1", lang="en")
        self.assertIn("3 dry days expected from", en.body)

    def test_rerun_does_not_duplicate(self):
        self.job("send-alerts")
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["alerts_created"], 0)
        self.assertEqual(summary["already_drafted"], 4)
        self.assertEqual(Alert.objects.count(), 4)

    def test_dry_run_previews_without_writing(self):
        summary = self.job("send-alerts", {"dry_run": True})["summary"]
        self.assertEqual(summary["alerts_created"], 4)
        self.assertEqual(len(summary["preview"]), 4)
        self.assertFalse(Alert.objects.exists())

    def test_watch_and_abstain_never_alert(self):
        self.job("send-alerts")
        self.assertFalse(Alert.objects.filter(ward_id__in=[14, 16]).exists())

    def test_ward_without_audience_is_counted(self):
        Subscriber.objects.filter(ward_id=13).update(status="STOPPED")
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["no_subscribers"], 1)

    def test_officer_template_wins(self):
        AlertTemplate.objects.filter(hazard="dry_window", lang="sw").update(body="Ngao: jua {ward} {confidence}%")
        self.job("send-alerts")
        body = Alert.objects.get(ward_id=12, alert_type_id="A1", lang="sw").body
        self.assertRegex(body, r"^Ngao: jua Moiben \d+%$")


class ApprovalTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.score_today()
        self.job("send-alerts")
        self.alert = Alert.objects.get(ward_id=12, alert_type_id="A1", lang="sw")

    def test_rest_approval_fans_out_and_sends(self):
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {"approved_by": "officer1"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["dispatch"]["messages_queued"], 2)  # 101 farmer + 103 both (sw)
        self.alert.refresh_from_db()
        self.assertEqual((self.alert.status, self.alert.approved_by), ("SENT", "officer1"))
        self.assertIsNotNone(self.alert.sent_at)
        messages = Message.objects.filter(alert=self.alert)
        self.assertEqual({m.phone for m in messages}, {"+254700000101", "+254700000103"})
        # synthetic subscribers never reach the provider
        self.assertTrue(all(m.status == "SENT" and m.provider_msg_id == "SIMULATED" for m in messages))
        self.assertTrue(all(m.body == self.alert.body for m in messages))

    @override_settings(NGAO_SMS_SIMULATE_SYNTHETIC=False)
    def test_real_send_records_provider_id(self):
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        ids = set(Message.objects.filter(alert=self.alert).values_list("provider_msg_id", flat=True))
        self.assertTrue(all(i.startswith("console-") for i in ids))

    def test_apex_style_approval_is_picked_up_by_dispatch(self):
        # APEX page 3 does: UPDATE alerts SET status='APPROVED', approved_by=:APP_USER ...
        Alert.objects.filter(pk=self.alert.pk).update(status="APPROVED", approved_by="CHRIS", approved_at=timezone.now())
        summary = self.job("dispatch")["summary"]
        self.assertEqual(summary["approved_dispatch"]["alerts_dispatched"], 1)
        self.assertEqual(Message.objects.filter(alert=self.alert).count(), 2)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "SENT")
        again = self.job("dispatch")["summary"]
        self.assertEqual(again["approved_dispatch"]["alerts_dispatched"], 0)

    def test_reject(self):
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/reject", {"approved_by": "officer1"}, format="json")
        self.assertEqual(response.json()["status"], "REJECTED")
        self.assertFalse(Message.objects.filter(alert=self.alert).exists())

    def test_approve_twice_conflicts(self):
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "CONFLICT")

    def test_list_alerts(self):
        items = self.api.get("/ngao/v1/alerts?status=PENDING_APPROVAL").json()["items"]
        self.assertEqual(len(items), 4)
        self.assertEqual(set(items[0]) >= {"alert_id", "ward_name", "body", "status", "confidence_pct"}, True)

    def test_stopped_and_unconsented_subscribers_are_skipped(self):
        Subscriber.objects.filter(phone="+254700000101").update(status="STOPPED")
        Subscriber.objects.filter(phone="+254700000103").update(consent_at=None)
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "SUPPRESSED")
        self.assertFalse(Message.objects.filter(alert=self.alert).exists())


class GuardTests(NgaoTestCase):
    def test_replay_alerts_are_never_sent(self):
        score = RiskScore.objects.create(ward_id=41, score_date=dt.date(2006, 11, 3), alert_type_id="B3",
                                         probability=0.9, confidence_pct=90, decision="ACT", is_replay=True)
        Alert.objects.create(score=score, alert_type_id="B3", ward_id=41, lang="sw", body="x", status="APPROVED")
        summary = self.job("dispatch")["summary"]["approved_dispatch"]
        self.assertEqual(summary["skipped_replay"], 1)
        self.assertFalse(Message.objects.exists())

    def test_stale_approvals_are_not_sent(self):
        old = timezone.now() - dt.timedelta(days=5)
        Alert.objects.create(alert_type_id="A1", ward_id=12, lang="sw", body="old news", status="APPROVED",
                             created_at=old)
        summary = self.job("dispatch")["summary"]["approved_dispatch"]
        self.assertEqual(summary["skipped_stale"], 1)
        self.assertFalse(Message.objects.exists())

    def test_ward_cooldown_blocks_a_second_draft(self):
        self.score_today()
        self.job("send-alerts")
        Alert.objects.update(status="SENT")
        tomorrow = today_eat() + dt.timedelta(days=1)
        RiskScore.objects.create(ward_id=12, score_date=tomorrow, alert_type_id="A1", probability=0.8,
                                 confidence_pct=80, decision="ACT")
        summary = self.job("send-alerts", {"as_of": tomorrow.isoformat()})["summary"]
        self.assertEqual(summary["suppressed_cooldown"], 2)
        self.assertEqual(summary["alerts_created"], 0)

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_weekly_cap_for_routine_alerts(self):
        subscriber = Subscriber.objects.get(phone="+254700000104")  # Kapseret
        a1 = AlertType.objects.get(pk="A1")
        for _ in range(3):
            routine = Alert.objects.create(alert_type=a1, ward_id=13, lang="sw", body="r", status="SENT")
            Message.objects.create(direction="OUT", phone=subscriber.phone, subscriber=subscriber, body="r",
                                   status="SENT", alert=routine)
        self.score_today()
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["suppressed_cap"], 1)  # 104 is capped; 105 (en) still gets A2
        self.assertFalse(Message.objects.filter(subscriber=subscriber, alert__alert_type_id="A2").exists())
        self.assertTrue(Message.objects.filter(phone="+254700000105", alert__alert_type_id="A2").exists())

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_flood_and_disease_override_the_cap(self):
        subscriber = Subscriber.objects.get(phone="+254700000104")
        a1 = AlertType.objects.get(pk="A1")
        for _ in range(3):
            routine = Alert.objects.create(alert_type=a1, ward_id=13, lang="sw", body="r", status="SENT")
            Message.objects.create(direction="OUT", phone=subscriber.phone, subscriber=subscriber, body="r",
                                   status="SENT", alert=routine)
        RiskScore.objects.create(ward_id=13, score_date=today_eat(), alert_type_id="A4", probability=0.8,
                                 confidence_pct=80, decision="ACT")
        self.job("send-alerts")
        self.assertTrue(Message.objects.filter(subscriber=subscriber, alert__alert_type_id="A4").exists())

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_without_approval_alerts_go_straight_out(self):
        self.score_today()
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["messages_queued"], 5)  # Moiben 3 + Kapseret 2
        self.assertEqual(set(Alert.objects.values_list("status", flat=True)), {"SENT"})
        self.assertEqual(Alert.objects.first().approved_by, "auto")


class TemplateTests(NgaoTestCase):
    def test_default_copy_fits_one_sms_and_has_known_slots(self):
        from alerts.catalogue import DEFAULT_ALERT_COPY, SYSTEM_COPY
        import string

        allowed = {"ward", "day", "confidence", "point", "time", "fee", "bags", "dryer", "stops"}
        for code, copy in list(DEFAULT_ALERT_COPY.items()) + list(SYSTEM_COPY.items()):
            for lang in ("sw", "en"):
                text = copy[lang]
                if code in DEFAULT_ALERT_COPY:
                    self.assertLessEqual(len(text), 160, f"{code}/{lang} is {len(text)} chars")
                slots = {name for _, name, _, _ in string.Formatter().parse(text) if name}
                self.assertLessEqual(slots, allowed, f"{code}/{lang}")

    def test_missing_slot_renders_empty(self):
        body, *_ = render_alert("A1", "en", confidence=80)
        self.assertIn("80% sure", body)
