"""Live RISK_SCORES.DECISION, IS_REPLAY and SUBSCRIBERS.PREFERRED_LANG (no Oracle required)."""
from django.db import connection

from alerts.engine import draft_alerts, ensure_risk_score
from ngao.models import Alert, RiskScore, Subscriber
from ngao_core.timeutil import today_eat
from tests.base import NgaoTestCase
from tests.test_sms import form

DECISIONS = {"ACT", "WATCH", "ABSTAIN"}


def _cell(sql, params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


class DecisionAndReplayTests(NgaoTestCase):
    def test_created_score_has_a_decision_and_yn_replay_flag(self):
        day = today_eat()
        omitted = ensure_risk_score(15, "C3", day)
        explicit_null = ensure_risk_score(15, "A4", day, decision=None)
        watch = ensure_risk_score(15, "B4", day, decision="watch")
        for row in (omitted, explicit_null, watch):
            self.assertIn(row.decision, DECISIONS)
            self.assertLessEqual(len(row.decision), 7)
            self.assertIn(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [row.pk]), ("Y", "N"))
        self.assertEqual(omitted.decision, "ACT")
        self.assertEqual(explicit_null.decision, "ACT")
        self.assertEqual(watch.decision, "WATCH")
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [omitted.pk]), "N")
        forced = RiskScore.objects.create(
            ward_id=15, score_date=day, alert_type_id="A3", decision="ACT", is_replay=None,
        )
        self.assertIs(forced.is_replay, False)
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [forced.pk]), "N")

    def test_invalid_decision_is_not_inserted(self):
        day = today_eat()
        with self.assertRaises(ValueError):
            ensure_risk_score(16, "C3", day, decision="MAYBE")
        with self.assertRaises(ValueError):
            RiskScore.objects.create(
                ward_id=16, score_date=day, alert_type_id="C3", decision=None,
            )
        self.assertFalse(RiskScore.objects.filter(ward_id=16, alert_type_id="C3", score_date=day).exists())

    def test_replay_flag_is_part_of_the_unique_key(self):
        day = today_eat()
        live = ensure_risk_score(16, "B1", day, is_replay=False, decision="ACT")
        replay = ensure_risk_score(16, "B1", day, is_replay=True, decision="WATCH")
        self.assertNotEqual(live.pk, replay.pk)
        self.assertEqual(ensure_risk_score(16, "B1", day, is_replay=True, probability=0.1).pk, replay.pk)
        self.assertEqual(ensure_risk_score(16, "B1", day, is_replay=False).pk, live.pk)
        replay.refresh_from_db()
        self.assertEqual(replay.probability, None)  # the second call must not overwrite the row
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [live.pk]), "N")
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [replay.pk]), "Y")
        self.assertEqual(
            RiskScore.objects.filter(ward_id=16, alert_type_id="B1", score_date=day).count(), 2,
        )

    def test_null_is_replay_is_reused_as_non_replay(self):
        day = today_eat()
        row = RiskScore.objects.create(
            ward_id=16, score_date=day, alert_type_id="A1", decision="WATCH", probability=0.4,
        )
        with connection.cursor() as cursor:
            cursor.execute("UPDATE risk_scores SET is_replay = NULL WHERE score_id = %s", [row.pk])
        again = ensure_risk_score(16, "A1", day, probability=0.1)
        self.assertEqual(again.pk, row.pk)
        self.assertEqual(again.decision, "WATCH")
        self.assertIs(again.is_replay, False)
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [row.pk]), "N")
        self.assertEqual(
            RiskScore.objects.filter(ward_id=16, alert_type_id="A1", score_date=day).count(), 1,
        )

    def test_scoring_keeps_another_models_row_and_stores_n_for_a_null_flag(self):
        day = today_eat()
        row = RiskScore.objects.create(
            ward_id=12, score_date=day, alert_type_id="A1", decision="WATCH", probability=0.2,
            model_version="a1-xgb-2026-10-14-abc",
        )
        with connection.cursor() as cursor:
            cursor.execute("UPDATE risk_scores SET is_replay = NULL WHERE score_id = %s", [row.pk])
        summary = self.score_today()
        self.assertGreaterEqual(summary["kept_existing"], 1)
        row.refresh_from_db()
        self.assertEqual((row.decision, row.model_version), ("WATCH", "a1-xgb-2026-10-14-abc"))
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [row.pk]), "N")
        self.assertEqual(
            RiskScore.objects.filter(ward_id=12, alert_type_id="A1", score_date=day).count(), 1,
        )

    def test_scoring_fills_decision_and_does_not_duplicate_a_null_replay_flag(self):
        day = today_eat()
        row = RiskScore.objects.create(
            ward_id=12, score_date=day, alert_type_id="A1", decision="WATCH", probability=0.2,
            confidence_pct=20, model_version="heuristic-v0-backend",
        )
        with connection.cursor() as cursor:
            cursor.execute("UPDATE risk_scores SET is_replay = NULL WHERE score_id = %s", [row.pk])
        self.score_today()
        self.assertEqual(
            RiskScore.objects.filter(ward_id=12, alert_type_id="A1", score_date=day).count(), 1,
        )
        row.refresh_from_db()
        self.assertIn(row.decision, DECISIONS)
        self.assertIs(row.is_replay, False)
        self.assertEqual(_cell("SELECT is_replay FROM risk_scores WHERE score_id = %s", [row.pk]), "N")
        with connection.cursor() as cursor:
            cursor.execute("SELECT decision, is_replay FROM risk_scores")
            stored = cursor.fetchall()
        self.assertGreaterEqual(len(stored), 1)
        for decision, replay in stored:
            self.assertIn(decision, DECISIONS)
            self.assertLessEqual(len(decision), 7)
            self.assertIn(replay, ("Y", "N"))


class PreferredLangTests(NgaoTestCase):
    def test_register_without_preferred_lang_still_sets_the_not_null_column(self):
        phone = "+254799111222"
        response = self.ussd.post(
            "/ngao/v1/ussd/register",
            {"phone": phone, "ward_id": 12, "role": "farmer", "consent": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        sub = Subscriber.objects.get(phone=phone)
        self.assertEqual((sub.preferred_lang, sub.language), ("sw", "sw"))
        self.assertEqual(len(sub.preferred_lang), 2)
        self.assertEqual(
            _cell("SELECT preferred_lang FROM subscribers WHERE subscriber_id = %s", [sub.pk]), "sw",
        )

    def test_register_keeps_the_language_chosen_earlier(self):
        phone = "+254799111223"
        self.ussd.post("/ngao/v1/ussd/language", {"phone": phone, "preferred_lang": "en"}, format="json")
        response = self.ussd.post(
            "/ngao/v1/ussd/register",
            {"phone": phone, "ward_id": 12, "role": "farmer", "consent": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        sub = Subscriber.objects.get(phone=phone)
        self.assertEqual((sub.preferred_lang, sub.language), ("en", "en"))

    def test_sms_lang_sets_preferred_lang_for_a_new_number(self):
        phone = "+254733000099"
        form(self.anon, "/ngao/v1/sms/inbound", {
            "from": phone, "to": "12345", "text": "LANG EN", "date": "2026-10-15 09:12:01", "id": "at-lang",
            "networkCode": "63902",
        })
        sub = Subscriber.objects.get(phone=phone)
        self.assertEqual((sub.preferred_lang, sub.language), ("en", "en"))
        self.assertEqual(len(sub.preferred_lang), 2)

    def test_sms_and_ward_status_read_preferred_lang_not_language(self):
        phone = "+254700000101"  # Moiben farmer
        sub = Subscriber.objects.get(phone=phone)
        sub.preferred_lang = "en"
        sub.language = "sw"
        sub.save(update_fields=["preferred_lang", "language"])
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE subscribers SET preferred_lang = %s WHERE subscriber_id = %s", ["en ", sub.pk],
            )
        sub.refresh_from_db()
        self.assertEqual(sub.preferred_lang, "en")
        self.assertEqual(sub.language, "sw")
        self.assertEqual(sub.spoken_lang(), "en")

        self.score_today()
        status = self.ussd.get(f"/ngao/v1/ussd/ward-status?phone={phone}")
        self.assertEqual(status.status_code, 200)
        text = status.json()["text"]
        self.assertIn("ward:", text)
        self.assertNotIn("Kata ya", text)

        draft_alerts()
        alert = Alert.objects.get(subscriber=sub, alert_type_id="A1")
        self.assertEqual(alert.lang, "en")
        self.assertIn("Harvest your maize", alert.body)
        self.assertNotIn("Vuna", alert.body)

        form(self.anon, "/ngao/v1/sms/inbound", {
            "from": phone, "to": "12345", "text": "magunia 20 mabichi", "date": "2026-10-15 09:12:01",
            "id": "at-ack", "networkCode": "63902",
        })
        from ngao.models import Message
        ack = Message.objects.filter(direction="OUT", phone=phone).latest("message_id")
        self.assertIn("Thank you", ack.body)
        self.assertNotIn("Asante", ack.body)
