"""Alert engine: one live ALERTS row per subscriber, then approval, then one SMS."""
import datetime as dt

from django.db import connection
from django.test import override_settings
from django.utils import timezone

from alerts.engine import ensure_risk_score
from alerts.queue_view import alert_queue_rows
from alerts.templates import render_alert
from ngao.models import Alert, AlertTemplate, AlertType, Message, RiskScore, Subscriber
from ngao_core.timeutil import today_eat
from tests.base import NgaoTestCase


class DraftTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.score_today()

    def test_drafts_one_alert_per_subscriber_pending_approval(self):
        summary = self.job("send-alerts")["summary"]
        # Moiben A1: 101 sw, 102 en, 103 sw. Kapseret A2: 104 sw, 105 en.
        self.assertEqual(summary["alerts_created"], 5)
        self.assertEqual(summary["pending_approval"], 5)
        self.assertEqual(summary["messages_queued"], 0)
        self.assertFalse(Message.objects.filter(direction="OUT").exists())
        sw = Alert.objects.get(subscriber__phone="+254700000101")
        self.assertEqual(sw.status, "PENDING_APPROVAL")
        self.assertEqual((sw.ward_id, sw.alert_type_id, sw.lang), (12, "A1", "sw"))
        self.assertIsNotNone(sw.score_id)
        self.assertIsNotNone(sw.subscriber_id)
        self.assertLessEqual(len(sw.body), 160)
        self.assertLessEqual(len(sw.body.encode("utf-8")), 160)
        self.assertTrue(sw.body.startswith("MvuaSmart: Siku 3 za jua zinatarajiwa kuanzia "))
        self.assertIn(f"uhakika {sw.score.confidence_pct}%", sw.body)
        self.assertEqual((sw.hazard, sw.severity), ("dry_window", "warning"))
        en = Alert.objects.get(subscriber__phone="+254700000102")
        self.assertIn("3 dry days expected from", en.body)
        self.assertFalse(Alert.objects.filter(score_id__isnull=True).exists())
        self.assertFalse(Alert.objects.filter(subscriber_id__isnull=True).exists())

    def test_rerun_does_not_duplicate(self):
        self.job("send-alerts")
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["alerts_created"], 0)
        self.assertEqual(summary["already_drafted"], 5)
        self.assertEqual(Alert.objects.count(), 5)

    def test_dry_run_previews_without_writing(self):
        summary = self.job("send-alerts", {"dry_run": True})["summary"]
        self.assertEqual(summary["alerts_created"], 5)
        self.assertEqual(len(summary["preview"]), 5)
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
        body = Alert.objects.get(subscriber__phone="+254700000101").body
        self.assertRegex(body, r"^Ngao: jua Moiben \d+%$")

    def test_long_officer_template_is_stored_at_160(self):
        AlertTemplate.objects.filter(hazard="dry_window", lang="sw").update(body="N" * 400)
        self.job("send-alerts")
        body = Alert.objects.get(subscriber__phone="+254700000101").body
        self.assertEqual(len(body), 160)
        self.assertEqual(len(body.encode("utf-8")), 160)


class ApprovalTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.score_today()
        self.job("send-alerts")
        self.alert = Alert.objects.get(subscriber__phone="+254700000101")

    def test_rest_approval_sends_that_subscriber(self):
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {"approved_by": "officer1"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["dispatch"]["messages_queued"], 1)
        self.alert.refresh_from_db()
        self.assertEqual((self.alert.status, self.alert.approved_by), ("SENT", "officer1"))
        self.assertIsNotNone(self.alert.sent_at)
        self.assertIsNotNone(self.alert.message_id)
        message = self.alert.message
        self.assertEqual(message.phone, "+254700000101")
        self.assertEqual((message.status, message.provider_msg_id), ("SENT", "SIMULATED"))
        self.assertEqual(message.body, self.alert.body)
        self.assertNotIn("alert", [field.name for field in Message._meta.concrete_fields])
        # The other Kiswahili subscriber is a separate ALERTS row until an officer approves it.
        other = Alert.objects.get(subscriber__phone="+254700000103")
        self.assertEqual(other.status, "PENDING_APPROVAL")
        self.api.post(f"/ngao/v1/alerts/{other.pk}/approve", {"approved_by": "officer1"}, format="json")
        other.refresh_from_db()
        self.assertEqual(other.message.phone, "+254700000103")
        self.assertEqual(other.status, "SENT")

    @override_settings(NGAO_SMS_SIMULATE_SYNTHETIC=False)
    def test_real_send_records_provider_id(self):
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        self.alert.refresh_from_db()
        self.assertTrue(self.alert.message.provider_msg_id.startswith("console-"))

    def test_apex_style_approval_is_picked_up_by_dispatch(self):
        # APEX page 3 does: UPDATE alerts SET status='APPROVED', approved_by=:APP_USER ...
        Alert.objects.filter(pk=self.alert.pk).update(status="APPROVED", approved_by="CHRIS", approved_at=timezone.now())
        summary = self.job("dispatch")["summary"]
        self.assertEqual(summary["approved_dispatch"]["alerts_dispatched"], 1)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "SENT")
        self.assertEqual(self.alert.message.phone, "+254700000101")
        again = self.job("dispatch")["summary"]
        self.assertEqual(again["approved_dispatch"]["alerts_dispatched"], 0)

    def test_reject(self):
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/reject", {"approved_by": "officer1"}, format="json")
        self.assertEqual(response.json()["status"], "REJECTED")
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.message_id)

    def test_approve_twice_conflicts(self):
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        response = self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "CONFLICT")

    def test_list_alerts(self):
        items = self.api.get("/ngao/v1/alerts?status=PENDING_APPROVAL").json()["items"]
        self.assertEqual(len(items), 5)
        self.assertEqual(
            set(items[0]) >= {"alert_id", "subscriber_id", "phone", "ward_name", "body", "status", "confidence_pct"},
            True,
        )
        self.assertTrue(all(len(item["body"]) <= 160 for item in items))

    def test_stopped_subscriber_is_not_sent(self):
        Subscriber.objects.filter(phone="+254700000101").update(status="STOPPED")
        self.api.post(f"/ngao/v1/alerts/{self.alert.pk}/approve", {}, format="json")
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "APPROVED")
        self.assertIsNone(self.alert.message_id)


class GuardTests(NgaoTestCase):
    def test_replay_alerts_are_never_sent(self):
        score = RiskScore.objects.create(ward_id=41, score_date=dt.date(2006, 11, 3), alert_type_id="B3",
                                         probability=0.9, confidence_pct=90, decision="ACT", is_replay=True)
        subscriber = Subscriber.objects.get(phone="+254700000201")
        Alert.objects.create(score=score, subscriber=subscriber, alert_type_id="B3", ward_id=41, lang="sw",
                             body="x", status="APPROVED")
        summary = self.job("dispatch")["summary"]["approved_dispatch"]
        self.assertEqual(summary["skipped_replay"], 1)
        self.assertFalse(Message.objects.exists())

    def test_stale_approvals_are_not_sent(self):
        old = timezone.now() - dt.timedelta(days=5)
        subscriber = Subscriber.objects.get(phone="+254700000101")
        score = RiskScore.objects.create(ward_id=12, score_date=today_eat(), alert_type_id="A1", probability=0.8,
                                         confidence_pct=80, decision="ACT")
        Alert.objects.create(score=score, subscriber=subscriber, alert_type_id="A1", ward_id=12, lang="sw",
                             body="old news", status="APPROVED", created_at=old)
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
        self.assertEqual(summary["suppressed_cooldown"], 3)  # 101, 102, 103 each already have an A1 row
        self.assertEqual(summary["alerts_created"], 0)

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_weekly_cap_for_routine_alerts(self):
        subscriber = Subscriber.objects.get(phone="+254700000104")  # Kapseret
        a1 = AlertType.objects.get(pk="A1")
        routine_score = RiskScore.objects.create(
            ward_id=13, score_date=today_eat() - dt.timedelta(days=2), alert_type=a1, probability=0.8,
            confidence_pct=80, decision="ACT",
        )
        for _ in range(3):
            Alert.objects.create(score=routine_score, subscriber=subscriber, alert_type=a1, ward_id=13, lang="sw",
                                 body="r", status="SENT", sent_at=timezone.now())
        self.score_today()
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["suppressed_cap"], 1)  # 104 is capped; 105 (en) still gets A2
        self.assertFalse(Alert.objects.filter(subscriber=subscriber, alert_type_id="A2").exists())
        self.assertTrue(Alert.objects.filter(subscriber__phone="+254700000105", alert_type_id="A2",
                                             message__isnull=False).exists())

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_flood_and_disease_override_the_cap(self):
        subscriber = Subscriber.objects.get(phone="+254700000104")
        a1 = AlertType.objects.get(pk="A1")
        routine_score = RiskScore.objects.create(
            ward_id=13, score_date=today_eat() - dt.timedelta(days=2), alert_type=a1, probability=0.8,
            confidence_pct=80, decision="ACT",
        )
        for _ in range(3):
            Alert.objects.create(score=routine_score, subscriber=subscriber, alert_type=a1, ward_id=13, lang="sw",
                                 body="r", status="SENT", sent_at=timezone.now())
        RiskScore.objects.create(ward_id=13, score_date=today_eat(), alert_type_id="A4", probability=0.8,
                                 confidence_pct=80, decision="ACT")
        self.job("send-alerts")
        self.assertTrue(Alert.objects.filter(subscriber=subscriber, alert_type_id="A4", message__isnull=False).exists())

    @override_settings(NGAO_ALERTS_REQUIRE_APPROVAL=False)
    def test_without_approval_alerts_go_straight_out(self):
        self.score_today()
        summary = self.job("send-alerts")["summary"]
        self.assertEqual(summary["messages_queued"], 5)  # Moiben 3 + Kapseret 2, one SMS each
        self.assertEqual(set(Alert.objects.values_list("status", flat=True)), {"SENT"})
        self.assertTrue(all(approved_by == "auto" for approved_by in Alert.objects.values_list("approved_by", flat=True)))
        self.assertEqual(Alert.objects.filter(message__isnull=False).count(), 5)


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


class ScoreLookupTests(NgaoTestCase):
    def test_matching_risk_score_is_reused(self):
        day = today_eat()
        first = ensure_risk_score(12, "A1", day, probability=0.8, confidence_pct=80)
        second = ensure_risk_score(12, "A1", day, probability=0.1)
        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.probability, 0.8)
        self.assertEqual(
            RiskScore.objects.filter(ward_id=12, alert_type_id="A1", score_date=day, is_replay=False).count(), 1,
        )


class LiveQueueTests(NgaoTestCase):
    """V_ALERT_QUEUE inner-joins subscribers, wards and risk_scores. No live database required."""

    def test_drafted_alerts_appear_in_the_queue_join(self):
        self.score_today()
        self.job("send-alerts")
        alerts = {alert.pk: alert for alert in Alert.objects.select_related("subscriber", "ward", "score")}
        rows = alert_queue_rows()
        self.assertEqual({row["alert_id"] for row in rows}, set(alerts))
        self.assertGreaterEqual(len(rows), 1)
        for row in rows:
            alert = alerts[row["alert_id"]]
            self.assertEqual(row["phone"], alert.subscriber.phone)
            self.assertEqual(row["subscriber_id"], alert.subscriber_id)
            self.assertEqual(row["ward_id"], alert.ward_id)
            self.assertEqual(row["ward_name"], alert.ward.ward_name)
            self.assertEqual(row["alert_type"], alert.alert_type_id)
            self.assertEqual(row["status"], "PENDING_APPROVAL")
            self.assertLessEqual(row["body_len"], 160)
            self.assertEqual(row["risk_score"], alert.score.probability)
            self.assertIsNone(row["message_id"])

    def test_inner_join_hides_an_alert_whose_subscriber_does_not_exist(self):
        self.score_today()
        self.job("send-alerts")
        score_id = RiskScore.objects.get(ward_id=12, alert_type_id="A1", is_replay=False).pk
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE alerts_orphan (
                    alert_id integer, score_id integer, subscriber_id integer,
                    alert_type varchar(3), ward_id integer, lang varchar(2),
                    body varchar(160), status varchar(16), hazard varchar(30),
                    severity varchar(16), message_id integer, created_at datetime,
                    approved_by varchar(100), approved_at datetime, sent_at datetime
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO alerts_orphan (
                    alert_id, score_id, subscriber_id, alert_type, ward_id, lang, body, status,
                    hazard, severity, message_id, created_at, approved_by, approved_at, sent_at
                )
                SELECT alert_id, score_id, subscriber_id, alert_type, ward_id, lang, body, status,
                       hazard, severity, message_id, created_at, approved_by, approved_at, sent_at
                FROM alerts
                """
            )
            cursor.execute(
                """
                INSERT INTO alerts_orphan (alert_id, score_id, subscriber_id, alert_type, ward_id, body, status)
                VALUES (999001, %s, 999999, 'A1', 12, 'orphan', 'PENDING_APPROVAL')
                """,
                [score_id],
            )
        visible = {row["alert_id"] for row in alert_queue_rows("alerts_orphan")}
        self.assertNotIn(999001, visible)
        self.assertEqual(visible, set(Alert.objects.values_list("pk", flat=True)))
