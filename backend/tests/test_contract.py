"""API_CONTRACT §2.0 auth, §2.1 error format, both URL prefixes, §2.2 job envelope, §2.3 reads."""
import datetime as dt

from ngao.models import JobRun, RiskScore
from ngao_core.timeutil import today_eat
from tests.base import NgaoTestCase


class ErrorFormatTests(NgaoTestCase):
    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()["error"]
        self.assertEqual(body["code"], code)
        self.assertTrue(body["message"])
        self.assertIn("details", body)
        self.assertEqual(body["request_id"], response["X-Request-ID"])
        return body

    def test_missing_token_is_unauthorized(self):
        self.assertError(self.anon.get("/ngao/v1/wards"), 401, "UNAUTHORIZED")

    def test_bad_token_is_unauthorized(self):
        self.anon.credentials(HTTP_AUTHORIZATION="Bearer nope")
        self.assertError(self.anon.get("/ngao/v1/wards"), 401, "UNAUTHORIZED")

    def test_ussd_token_cannot_run_jobs(self):
        self.assertError(self.ussd.post("/ngao/v1/jobs/score", {}, format="json"), 403, "FORBIDDEN")

    def test_validation_error_names_the_field(self):
        response = self.ussd.post("/ngao/v1/ussd/register", {"phone": "+254712345678", "role": "farmer",
                                                             "consent": True}, format="json")
        body = self.assertError(response, 400, "VALIDATION_ERROR")
        self.assertEqual(body["message"], "ward_id is required")
        self.assertEqual(body["details"]["field"], "ward_id")

    def test_not_found(self):
        self.assertError(self.api.get("/ngao/v1/wards/999/risk"), 404, "NOT_FOUND")
        self.assertError(self.api.get("/ngao/v1/nothing-here"), 404, "NOT_FOUND")

    def test_unknown_job(self):
        self.assertError(self.api.post("/ngao/v1/jobs/launch-rockets", {}, format="json"), 404, "NOT_FOUND")

    def test_bad_job_body(self):
        body = self.assertError(self.api.post("/ngao/v1/jobs/score", {"as_of": "15/10/2026"}, format="json"),
                                400, "VALIDATION_ERROR")
        self.assertEqual(body["details"]["field"], "as_of")

    def test_request_id_is_echoed(self):
        response = self.api.get("/ngao/v1/wards", HTTP_X_REQUEST_ID="abc-123")
        self.assertEqual(response["X-Request-ID"], "abc-123")


class RoutingPrefixTests(NgaoTestCase):
    def test_both_prefixes_serve_the_same_api(self):
        a = self.api.get("/ngao/v1/wards?county=Uasin%20Gishu").json()
        b = self.api.get("/api/v1/wards?county=Uasin%20Gishu").json()
        self.assertEqual(a, b)
        self.assertEqual(len(a["items"]), 5)

    def test_health_is_public(self):
        response = self.anon.get("/ngao/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_scorecard_is_public(self):
        response = self.anon.get("/ngao/v1/public/scorecard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": []})


class JobEnvelopeTests(NgaoTestCase):
    def test_job_response_matches_contract(self):
        body = self.job("send-alerts", {"as_of": today_eat().isoformat(), "dry_run": False})
        self.assertEqual(set(body), {"job_id", "job", "status", "summary"})
        self.assertEqual(body["job"], "send-alerts")
        self.assertEqual(body["status"], "DONE")
        for key in ("alerts_created", "messages_queued", "suppressed_cooldown"):
            self.assertIn(key, body["summary"])
        run = JobRun.objects.get(pk=body["job_id"])
        self.assertEqual(run.status, "DONE")
        status = self.api.get(f"/ngao/v1/jobs/{run.job_id}/status").json()
        self.assertEqual(status["summary"], body["summary"])

    def test_failed_job_is_recorded(self):
        response = self.api.post("/ngao/v1/jobs/score", {"county": "Atlantis"}, format="json")
        self.assertEqual(response.status_code, 400)
        run = JobRun.objects.latest("job_id")
        self.assertEqual(run.status, "FAILED")
        self.assertIn("Atlantis", run.error_text)


class ReadEndpointTests(NgaoTestCase):
    def test_wards_shape(self):
        item = next(i for i in self.api.get("/ngao/v1/wards").json()["items"] if i["ward_id"] == 12)
        self.assertEqual(item, {"ward_id": 12, "ward_name": "Moiben", "county": "Uasin Gishu",
                                "centroid": {"lat": 0.82, "lon": 35.38}})

    def test_ward_risk_shape_and_service_filter(self):
        self.score_today()
        body = self.api.get(f"/ngao/v1/wards/12/risk?date={today_eat()}&service=FARM").json()
        self.assertEqual(body["ward_id"], 12)
        self.assertEqual(body["score_date"], today_eat().isoformat())
        a1 = next(r for r in body["risks"] if r["alert_type"] == "A1")
        self.assertEqual(a1["decision"], "ACT")
        self.assertEqual(a1["confidence_pct"], round(a1["probability"] * 100))
        self.assertTrue(a1["model_version"])
        self.assertTrue(all(r["service"] == "FARM" for r in body["risks"]))
        bad = self.api.get("/ngao/v1/wards/12/risk?service=SPACE")
        self.assertEqual(bad.status_code, 400)

    def test_ward_risk_excludes_replay_rows(self):
        RiskScore.objects.create(ward_id=12, score_date=today_eat(), alert_type_id="B3", probability=0.9,
                                 confidence_pct=90, decision="ACT", is_replay=True)
        body = self.api.get("/ngao/v1/wards/12/risk").json()
        self.assertFalse(any(r["alert_type"] == "B3" for r in body["risks"]))

    def test_routes_default_to_tomorrow(self):
        body = self.api.get("/ngao/v1/routes").json()
        self.assertEqual(body["route_date"], (today_eat() + dt.timedelta(days=1)).isoformat())
        self.assertEqual(body["routes"], [])


class ColumnWidthTests(NgaoTestCase):
    """SQLite ignores VARCHAR2 lengths; Oracle does not (ORA-12899). Every value we write must fit."""

    def test_enum_values_fit_their_columns(self):
        from ngao.models import Alert, Booking, Message, Report, RiskScore, Route, Subscriber
        from reply_reader.schema import REPORT_TYPES

        values = {
            (Alert, "status"): ["PENDING_APPROVAL", "APPROVED", "REJECTED", "SENT", "SUPPRESSED"],
            (Message, "status"): ["QUEUED", "SENT", "DELIVERED", "FAILED", "RECEIVED"],
            (Message, "direction"): ["IN", "OUT"],
            (Message, "channel"): ["SMS", "USSD"],
            (Report, "source"): ["SMS_AI", "SMS_KEYWORD", "USSD"],
            (Report, "review_status"): ["AUTO_ACCEPTED", "NEEDS_REVIEW", "ACCEPTED", "CORRECTED", "REJECTED"],
            (Report, "report_type"): REPORT_TYPES,
            (Report, "unit"): ["bags", "animals"],
            (RiskScore, "decision"): ["ACT", "WATCH", "ABSTAIN"],
            (Subscriber, "role"): Subscriber.ROLES,
            (Route, "status"): ["PLANNED", "NOTIFIED", "DONE"],
            (Booking, "status"): ["OFFERED", "CONFIRMED", "DRIED", "NO_SHOW"],
        }
        for (model, field), options in values.items():
            width = model._meta.get_field(field).max_length
            for value in options:
                self.assertLessEqual(len(value), width, f"{model.__name__}.{field} '{value}'")

    def test_model_widths_match_schema_sql(self):
        import re
        from pathlib import Path

        from django.apps import apps

        ddl = (Path(__file__).resolve().parents[2] / "db" / "schema.sql").read_text()
        tables = dict(re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S))
        for model in apps.get_app_config("ngao").get_models():
            body = tables.get(model._meta.db_table)
            if body is None:
                continue
            for field in model._meta.concrete_fields:
                match = re.search(rf"^\s*{field.column}\s+(?:VARCHAR2|CHAR)\((\d+)\)", body, re.M)
                if match and getattr(field, "max_length", None):
                    self.assertLessEqual(field.max_length, int(match.group(1)),
                                         f"{model._meta.db_table}.{field.column} wider in Django than in schema.sql")
