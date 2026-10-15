"""API_CONTRACT §5: the endpoints Louisa's USSD service calls."""
from ngao.models import Message, Report, Subscriber
from tests.base import NgaoTestCase

PHONE = "+254712345678"


class LanguageTests(NgaoTestCase):
    def test_first_session_creates_subscriber_with_language(self):
        response = self.ussd.post("/ngao/v1/ussd/language", {"phone": PHONE, "preferred_lang": "en"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preferred_lang"], "en")
        sub = Subscriber.objects.get(phone=PHONE)
        self.assertEqual((sub.preferred_lang, sub.language, sub.status), ("en", "en", "ACTIVE"))
        self.assertIsNone(sub.ward_id)
        self.assertIsNone(sub.consent_at)

    def test_change_language_later(self):
        self.ussd.post("/ngao/v1/ussd/language", {"phone": PHONE, "preferred_lang": "en"}, format="json")
        self.ussd.post("/ngao/v1/ussd/language", {"phone": PHONE, "preferred_lang": "sw"}, format="json")
        self.assertEqual(Subscriber.objects.get(phone=PHONE).preferred_lang, "sw")
        self.assertEqual(Subscriber.objects.filter(phone=PHONE).count(), 1)

    def test_rejects_other_languages(self):
        response = self.ussd.post("/ngao/v1/ussd/language", {"phone": PHONE, "preferred_lang": "fr"}, format="json")
        self.assertEqual(response.status_code, 400)


class RegisterTests(NgaoTestCase):
    def register(self, **overrides):
        body = {"phone": PHONE, "ward_id": 12, "role": "farmer", "preferred_lang": "sw", "consent": True}
        body.update(overrides)
        return self.ussd.post("/ngao/v1/ussd/register", body, format="json")

    def test_register_merges_subscriber_and_sends_welcome(self):
        self.ussd.post("/ngao/v1/ussd/language", {"phone": PHONE, "preferred_lang": "en"}, format="json")
        response = self.register(preferred_lang="en")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "ACTIVE")
        sub = Subscriber.objects.get(pk=body["subscriber_id"])
        self.assertEqual((sub.ward_id, sub.role, sub.preferred_lang), (12, "farmer", "en"))
        self.assertIsNotNone(sub.consent_at)
        welcome = Message.objects.get(phone=PHONE, direction="OUT")
        self.assertEqual(welcome.status, "SENT")  # console backend, real (non-demo) number
        self.assertIn("Welcome", welcome.body)
        self.assertIn("Moiben", welcome.body)

    def test_reregistering_does_not_resend_welcome(self):
        self.register()
        self.register()
        self.assertEqual(Message.objects.filter(phone=PHONE, direction="OUT").count(), 1)

    def test_phone_is_normalised_to_e164(self):
        self.register(phone="0712 345 678")
        self.assertTrue(Subscriber.objects.filter(phone=PHONE).exists())

    def test_consent_is_required(self):
        response = self.register(consent=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["field"], "consent")

    def test_unknown_ward(self):
        response = self.register(ward_id=999)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["field"], "ward_id")

    def test_invalid_phone(self):
        response = self.register(phone="12345")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["details"]["field"], "phone")

    def test_optional_herd_detail(self):
        self.register(role="herder", ward_id=41, species="cattle", head_count=10)
        herd = Subscriber.objects.get(phone=PHONE).herd_set.get()
        self.assertEqual((herd.species, herd.tlu), ("cattle", 7.0))

    def test_stopped_subscriber_can_rejoin(self):
        self.register()
        self.ussd.post("/ngao/v1/ussd/unsubscribe", {"phone": PHONE}, format="json")
        self.register()
        self.assertEqual(Subscriber.objects.get(phone=PHONE).status, "ACTIVE")


class ReportTests(NgaoTestCase):
    def test_structured_report_is_auto_accepted(self):
        self.ussd.post("/ngao/v1/ussd/register", {"phone": PHONE, "ward_id": 12, "role": "farmer", "consent": True},
                       format="json")
        response = self.ussd.post("/ngao/v1/ussd/report", {"phone": PHONE, "session_id": "s1", "report_type": "wet_bags",
                                                           "count": 20, "unit": "bags"}, format="json")
        self.assertEqual(response.status_code, 201)
        report = Report.objects.get(pk=response.json()["report_id"])
        self.assertEqual((report.source, report.review_status, report.ward_id, report.report_count),
                         ("USSD", "AUTO_ACCEPTED", 12, 20))
        self.assertEqual(report.message.channel, "USSD")
        self.assertEqual(report.message.phone, PHONE)
        self.assertEqual(report.payload["session_id"], "s1")

    def test_other_is_not_a_ussd_report(self):
        response = self.ussd.post("/ngao/v1/ussd/report", {"phone": PHONE, "report_type": "other"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_report_from_unregistered_phone_has_no_ward(self):
        response = self.ussd.post("/ngao/v1/ussd/report", {"phone": PHONE, "report_type": "flooding"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Report.objects.get(pk=response.json()["report_id"]).ward_id)


class WardStatusTests(NgaoTestCase):
    def status(self, phone):
        response = self.ussd.get(f"/ngao/v1/ussd/ward-status?phone={phone}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_swahili_act_line(self):
        self.score_today()
        body = self.status("+254700000101")  # Moiben farmer, sw
        self.assertRegex(body["text"], r"^Kata ya Moiben: siku za jua, uhakika \d+%\.$")
        self.assertEqual(body["decision"], "ACT")

    def test_english_line_and_unencoded_plus(self):
        self.score_today()
        body = self.status("+254700000102")  # '+' arrives as a space when not URL-encoded
        self.assertRegex(body["text"], r"^Moiben ward: dry days ahead, confidence \d+%\.$")

    def test_no_scores(self):
        body = self.status("%2B254700000108")  # Kimumu, nothing scored
        self.assertEqual(body["text"], "Kimumu ward: no warnings today.")

    def test_unregistered(self):
        body = self.status("%2B254799999999")
        self.assertEqual(body["text"], "Hujajiandikisha. Chagua 1 kujiandikisha.")
        self.assertFalse(body["registered"])

    def test_abstain_line(self):
        self.score_today()
        body = self.status("%2B254700000106")  # Ainabkoi/Olare: GFS/ECMWF disagree
        self.assertEqual(body["text"], "Kata ya Ainabkoi/Olare: hatuna uhakika wa kutosha leo.")


class UnsubscribeTests(NgaoTestCase):
    def test_unsubscribe(self):
        response = self.ussd.post("/ngao/v1/ussd/unsubscribe", {"phone": "+254700000101"}, format="json")
        self.assertEqual(response.json(), {"status": "STOPPED"})
        self.assertEqual(Subscriber.objects.get(phone="+254700000101").status, "STOPPED")

    def test_unknown_phone(self):
        response = self.ussd.post("/ngao/v1/ussd/unsubscribe", {"phone": "+254799999999"}, format="json")
        self.assertEqual(response.status_code, 404)
