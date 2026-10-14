"""Wasiliana adapter, dispatcher, webhooks, keywords and the reply reader."""
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from ngao.models import Message, Report, Subscriber
from reply_reader import rules
from reply_reader.schema import SchemaError, validate
from sms import wasiliana
from sms.dispatcher import queue_sms, send_queued
from tests.base import NgaoTestCase

REAL = "+254711000001"  # in NGAO_DEMO_PHONES: always really sent


def fake_response(status=200, body=None):
    response = mock.Mock(status_code=status)
    response.json.return_value = body if body is not None else {}
    response.text = str(body)
    return response


@override_settings(NGAO_SMS_BACKEND="wasiliana", NGAO_WASILIANA_API_KEY="k-123", NGAO_WASILIANA_SENDER_ID="NGAO",
                   NGAO_SMS_DISPATCH="worker")
class WasilianaSendTests(NgaoTestCase):
    @mock.patch("sms.wasiliana.requests.post")
    def test_send_shape_and_provider_id(self, post):
        post.return_value = fake_response(200, {"status": "success", "data": [{"correlator": "msg_15883801465"}]})
        message = queue_sms(REAL, "Habari")
        summary = send_queued()
        self.assertEqual(summary["sent"], 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.wasiliana.com/api/v1/developer/send/sms")
        self.assertEqual(kwargs["headers"]["apiKey"], "k-123")
        self.assertEqual(kwargs["json"], {"recipients": ["254711000001"], "from": "NGAO", "message": "Habari"})
        message.refresh_from_db()
        self.assertEqual((message.status, message.provider_msg_id), ("SENT", "msg_15883801465"))

    @mock.patch("sms.wasiliana.requests.post", side_effect=requests.ConnectionError("down"))
    def test_network_error_retries_later(self, post):
        message = queue_sms(REAL, "Habari")
        self.assertEqual(send_queued()["retry_later"], 1)
        message.refresh_from_db()
        self.assertEqual(message.status, "QUEUED")

    @mock.patch("sms.wasiliana.requests.post")
    def test_client_error_fails(self, post):
        post.return_value = fake_response(401, {"message": "invalid api key"})
        message = queue_sms(REAL, "Habari")
        self.assertEqual(send_queued()["failed"], 1)
        message.refresh_from_db()
        self.assertEqual(message.status, "FAILED")

    @mock.patch("sms.wasiliana.requests.post")
    def test_synthetic_numbers_never_reach_wasiliana(self, post):
        subscriber = Subscriber.objects.get(phone="+254700000101")
        queue_sms(subscriber.phone, "x", subscriber=subscriber)
        self.assertEqual(send_queued()["simulated"], 1)
        post.assert_not_called()


class DeliveryReportTests(NgaoTestCase):
    def setUp(self):
        super().setUp()
        self.message = Message.objects.create(direction="OUT", phone=REAL, body="x", status="SENT",
                                              provider_msg_id="message_1588")

    def test_delivered(self):
        response = self.anon.post("/ngao/v1/sms/delivery", {"phone": "254711000001", "correlator": "message_1588",
                                                            "deliveryStatus": "0", "failure_reason": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "DELIVERED")

    def test_failed_with_reason(self):
        self.anon.post("/ngao/v1/sms/delivery", {"correlator": "message_1588", "deliveryStatus": "1",
                                                 "failure_reason": "AbsentSubscriber"}, format="json")
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "FAILED")

    def test_garbage_still_gets_200(self):
        response = self.anon.post("/ngao/v1/sms/delivery", "not json", content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    @override_settings(NGAO_WEBHOOK_SECRET="s3cret")
    def test_secret_is_enforced_silently(self):
        body = {"correlator": "message_1588", "deliveryStatus": "0"}
        self.assertEqual(self.anon.post("/ngao/v1/sms/delivery", body, format="json").status_code, 200)
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "SENT")
        self.anon.post("/ngao/v1/sms/delivery?k=s3cret", body, format="json")
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "DELIVERED")

    @override_settings(NGAO_WASILIANA_DLR_FIELDS={"provider_msg_id": ["ref"], "status": ["state"]})
    def test_field_names_come_from_settings(self):
        self.anon.post("/ngao/v1/sms/delivery", {"ref": "message_1588", "state": "DELIVERED"}, format="json")
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "DELIVERED")


class InboundHelpers:
    FARMER = "+254700000101"  # Moiben, sw

    def inbound(self, text, phone="254700000101", **extra):
        response = self.anon.post("/ngao/v1/sms/inbound", {"phone": phone, "message": text, **extra}, format="json")
        self.assertEqual(response.status_code, 200)
        return Message.objects.filter(direction="IN").latest("message_id")


class InboundTests(InboundHelpers, NgaoTestCase):
    def test_reply_becomes_auto_accepted_report_and_ack(self):
        message = self.inbound("Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu")
        self.assertEqual((message.phone, message.status), (self.FARMER, "RECEIVED"))
        report = Report.objects.get(message=message)
        self.assertEqual((report.report_type, report.report_count, report.unit, report.ward_id),
                         ("wet_bags", 20, "bags", 12))
        self.assertEqual((report.review_status, report.source), ("AUTO_ACCEPTED", "SMS_KEYWORD"))
        self.assertTrue(report.payload["rain_reported"])
        ack = Message.objects.filter(direction="OUT", phone=self.FARMER).latest("message_id")
        self.assertEqual(ack.body, "Ngao: Asante, tumepokea ripoti yako.")

    def test_unclear_reply_needs_review(self):
        report = Report.objects.get(message=self.inbound("habari yako mzee"))
        self.assertEqual((report.report_type, report.review_status), ("other", "NEEDS_REVIEW"))
        self.assertFalse(Message.objects.filter(direction="OUT").exists())

    def test_form_encoded_body(self):
        self.anon.post("/ngao/v1/sms/inbound", {"msisdn": "254700000102", "text": "STOP"})
        self.assertEqual(Subscriber.objects.get(phone="+254700000102").status, "STOPPED")

    def test_simulated_inbound_endpoint(self):
        response = self.api.post("/ngao/v1/demo/inbound-sms", {"phone": "0700000101", "text": "magunia 20 mabichi"},
                                 format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["report"]["report_type"], "wet_bags")


class KeywordTests(InboundHelpers, NgaoTestCase):
    def last_out(self, phone):
        return Message.objects.filter(direction="OUT", phone=phone).latest("message_id").body

    def test_stop(self):
        self.inbound("stop")
        self.assertEqual(Subscriber.objects.get(phone=self.FARMER).status, "STOPPED")
        self.assertIn("Umejiondoa", self.last_out(self.FARMER))
        self.assertEqual(Report.objects.get(report_type="stop").source, "SMS_KEYWORD")

    def test_language_switch_confirms_in_new_language(self):
        self.inbound("Lugha EN")
        self.assertEqual(Subscriber.objects.get(phone=self.FARMER).preferred_lang, "en")
        self.assertEqual(self.last_out(self.FARMER), "Ngao: Your language is now English.")
        self.inbound("LANG sw")
        self.assertEqual(Subscriber.objects.get(phone=self.FARMER).preferred_lang, "sw")
        self.assertEqual(self.last_out(self.FARMER), "Ngao: Lugha yako sasa ni Kiswahili.")

    def test_language_without_value_changes_nothing(self):
        self.inbound("LUGHA")
        self.assertEqual(Subscriber.objects.get(phone=self.FARMER).preferred_lang, "sw")
        self.assertIn("LUGHA SW", self.last_out(self.FARMER))
        self.assertFalse(Report.objects.exists())

    def test_ndiyo_without_booking_is_harvested(self):
        self.inbound("NDIYO")
        self.assertEqual(Report.objects.get().report_type, "harvested")

    def test_one_without_booking(self):
        self.inbound("1")
        self.assertIn("Hakuna nafasi", self.last_out(self.FARMER))

    def test_rvf(self):
        self.inbound("RVF 3 mbuzi", phone="254700000201")
        report = Report.objects.get()
        self.assertEqual((report.report_type, report.report_count, report.payload["species"]),
                         ("rvf_signs", 3, "goats"))


@override_settings(NGAO_REPLY_READER="genai")
class GenAIReaderTests(InboundHelpers, NgaoTestCase):
    VALID = {"report_type": "wet_bags", "count": 20, "unit": "bags", "crop": "maize", "species": None,
             "rain_reported": True, "moisture_pct": None, "language": "sw", "confidence": 0.92}

    @mock.patch("reply_reader.genai.extract")
    def test_valid_output_is_auto_accepted(self, extract):
        extract.return_value = dict(self.VALID)
        report = Report.objects.get(message=self.inbound("magunia ishirini yana unyevu"))
        self.assertEqual((report.source, report.review_status, report.ai_confidence), ("SMS_AI", "AUTO_ACCEPTED", 0.92))

    @mock.patch("reply_reader.genai.extract")
    def test_low_confidence_needs_review(self, extract):
        extract.return_value = {**self.VALID, "confidence": 0.5}
        report = Report.objects.get(message=self.inbound("magunia ishirini"))
        self.assertEqual(report.review_status, "NEEDS_REVIEW")

    @mock.patch("reply_reader.genai.extract")
    def test_schema_violation_needs_review(self, extract):
        extract.return_value = {**self.VALID, "advice": "harvest now"}
        report = Report.objects.get(message=self.inbound("magunia ishirini"))
        self.assertEqual((report.review_status, report.source), ("NEEDS_REVIEW", "SMS_KEYWORD"))
        self.assertIn("GENAI_FAILED", report.payload["review_reason"])

    @mock.patch("reply_reader.genai.extract")
    def test_ai_stop_is_not_acted_on(self, extract):
        extract.return_value = {**self.VALID, "report_type": "stop", "count": None, "unit": None}
        self.inbound("sitaki tena ujumbe huu")
        self.assertEqual(Subscriber.objects.get(phone=self.FARMER).status, "ACTIVE")
        self.assertEqual(Report.objects.get().review_status, "NEEDS_REVIEW")

    @mock.patch("reply_reader.genai.extract")
    def test_keywords_skip_the_ai(self, extract):
        self.inbound("STOP")
        extract.assert_not_called()


class SchemaTests(SimpleTestCase):
    VALID = GenAIReaderTests.VALID

    def test_valid(self):
        self.assertEqual(validate(dict(self.VALID))["count"], 20)

    def test_rejects(self):
        for bad in ({**self.VALID, "report_type": "party"}, {**self.VALID, "confidence": 1.5},
                    {**self.VALID, "count": "20"}, {**self.VALID, "language": "fr"},
                    {k: v for k, v in self.VALID.items() if k != "confidence"}, ["not", "a", "dict"]):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                validate(bad)

    def test_parse_json_with_code_fence(self):
        from reply_reader.genai import parse_json_object

        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('Here: {"a": 2} done'), {"a": 2})


class RulesTests(SimpleTestCase):
    CASES = [
        ("Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu", "wet_bags", 20, "sw"),
        ("magunia 20 mabichi", "wet_bags", 20, "sw"),
        ("magunia kumi na tano mabichi", "wet_bags", 15, "sw"),
        ("I have 30 wet bags of maize", "wet_bags", 30, "en"),
        ("Dried 120 bags, 13%", "bags_dried", 120, "en"),
        ("Ng'ombe 5 wagonjwa", "sick_animals", 5, "sw"),
        ("mbuzi watatu wamekufa", "dead_animals", 3, "sw"),
        ("Kisima kimekauka", "water_point_dry", None, "sw"),
        ("mafuriko kijijini", "flooding", None, "sw"),
        ("habari yako", "other", None, "sw"),
    ]

    def test_cases(self):
        for text, report_type, count, lang in self.CASES:
            with self.subTest(text=text):
                result = rules.extract(text)
                self.assertEqual((result["report_type"], result["count"], result["language"]), (report_type, count, lang))
                validate(result)  # the fallback always produces contract-valid JSON

    def test_moisture(self):
        self.assertEqual(rules.extract("Dried 120 bags, 13%")["moisture_pct"], 13.0)


class AdapterParsingTests(SimpleTestCase):
    def test_inbound_candidates(self):
        sms = wasiliana.parse_inbound({"msisdn": "254700000101", "text": "hi", "linkId": "L1"})
        self.assertEqual((sms.phone, sms.text, sms.provider_msg_id), ("254700000101", "hi", "L1"))

    def test_nested_payload(self):
        report = wasiliana.parse_delivery_report({"data": {"correlator": "c1", "deliveryStatus": "DeliveredToTerminal"}})
        self.assertEqual((report.provider_msg_id, report.outcome), ("c1", "DELIVERED"))

    def test_unknown_status_changes_nothing(self):
        self.assertIsNone(wasiliana.parse_delivery_report({"correlator": "c1", "deliveryStatus": "Pending"}).outcome)
