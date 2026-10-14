"""Swagger UI / OpenAPI: the docs are served, the schema is valid, and real responses match it."""
import io

import yaml
from django.core.management import call_command

from ngao.models import Alert, Booking
from tests.base import NgaoTestCase


def _resolve(schema, root):
    while "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        schema = root["components"]["schemas"][name]
    return schema


def check(value, schema, root, path="$"):
    """Small structural check: documented keys only, required keys present, types roughly right."""
    schema = _resolve(schema, root)
    if value is None:
        assert schema.get("nullable") or "type" not in schema, f"{path} is null but not nullable"
        return
    if "allOf" in schema:
        for part in schema["allOf"]:
            check(value, part, root, path)
        return
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        assert isinstance(value, dict), f"{path} should be an object"
        props = schema.get("properties")
        if props:
            extra = set(value) - set(props)
            assert not extra, f"{path} has undocumented keys {extra}"
            missing = set(schema.get("required", [])) - set(value)
            assert not missing, f"{path} lacks documented keys {missing}"
            for key, sub in value.items():
                check(sub, props[key], root, f"{path}.{key}")
    elif kind == "array":
        assert isinstance(value, list), f"{path} should be an array"
        for i, item in enumerate(value):
            check(item, schema["items"], root, f"{path}[{i}]")
    elif kind == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), f"{path} should be an integer, got {value!r}"
    elif kind == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{path} should be a number"
    elif kind == "boolean":
        assert isinstance(value, bool), f"{path} should be a boolean"
    elif kind == "string":
        assert isinstance(value, str), f"{path} should be a string, got {value!r}"
    if "enum" in schema and value is not None:
        assert value in schema["enum"], f"{path}={value!r} not in {schema['enum']}"


class DocsServedTests(NgaoTestCase):
    def test_swagger_ui_redoc_and_schema_are_public(self):
        ui = self.anon.get("/ngao/v1/docs/")
        self.assertEqual(ui.status_code, 200)
        self.assertIn(b"swagger-ui", ui.content)
        self.assertEqual(self.anon.get("/ngao/v1/redoc/").status_code, 200)
        schema = yaml.safe_load(self.anon.get("/ngao/v1/schema").content)
        self.assertEqual(schema["openapi"][:2], "3.")
        self.assertEqual({s["url"] for s in schema["servers"]}, {"/ngao/v1", "/api/v1"})
        self.assertEqual(self.anon.get("/").status_code, 302)

    def test_assets_are_served_locally(self):
        page = self.anon.get("/ngao/v1/docs/").content.decode()
        self.assertIn("/static/drf_spectacular_sidecar/swagger-ui-dist/", page)
        asset = self.anon.get("/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js")
        self.assertEqual(asset.status_code, 200)

    def test_schema_has_no_warnings_and_is_valid(self):
        out = io.StringIO()
        call_command("spectacular", "--validate", "--fail-on-warn", stdout=out, stderr=io.StringIO())
        schema = yaml.safe_load(out.getvalue())
        ops = [o for p in schema["paths"].values() for o in p.values()]
        self.assertGreaterEqual(len(ops), 29)
        for op in ops:
            self.assertTrue(op.get("summary"), op["operationId"])
            self.assertTrue(op.get("tags"), op["operationId"])
        self.assertFalse(any(p.startswith("/api/v1") for p in schema["paths"]))  # documented once


class ResponsesMatchSchemaTests(NgaoTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        out = io.StringIO()
        call_command("spectacular", stdout=out, stderr=io.StringIO())
        cls.schema = yaml.safe_load(out.getvalue())

    def assertMatches(self, response, path, method, status):
        self.assertEqual(response.status_code, status, response.content)
        op = self.schema["paths"][path][method]
        content = op["responses"][str(status)]["content"]["application/json"]["schema"]
        check(response.json(), content, self.schema)
        return response.json()

    def test_full_flow_matches_documentation(self):
        A, U = self.api, self.ussd
        phone = "+254711000001"
        self.assertMatches(self.anon.get("/ngao/v1/health"), "/health", "get", 200)
        self.assertMatches(U.post("/ngao/v1/ussd/language", {"phone": phone, "preferred_lang": "sw"}, format="json"),
                           "/ussd/language", "post", 200)
        self.assertMatches(U.post("/ngao/v1/ussd/register", {"phone": phone, "ward_id": 12, "role": "farmer",
                                                             "consent": True}, format="json"),
                           "/ussd/register", "post", 201)
        job = self.assertMatches(A.post("/ngao/v1/jobs/score", {}, format="json"), "/jobs/score", "post", 202)
        self.assertMatches(A.get(f"/ngao/v1/jobs/{job['job_id']}/status"), "/jobs/{job_id}/status", "get", 200)
        self.assertMatches(A.post("/ngao/v1/jobs/send-alerts", {"dry_run": True}, format="json"),
                           "/jobs/send-alerts", "post", 202)
        self.assertMatches(A.post("/ngao/v1/jobs/send-alerts", {}, format="json"), "/jobs/send-alerts", "post", 202)
        self.assertMatches(A.get("/ngao/v1/alerts?status=PENDING_APPROVAL"), "/alerts", "get", 200)
        alert = Alert.objects.filter(ward_id=12, lang="sw").first()
        self.assertMatches(A.post(f"/ngao/v1/alerts/{alert.pk}/approve", {"approved_by": "me"}, format="json"),
                           "/alerts/{alert_id}/approve", "post", 200)
        other = Alert.objects.filter(status="PENDING_APPROVAL").first()
        self.assertMatches(A.post(f"/ngao/v1/alerts/{other.pk}/reject", {}, format="json"),
                           "/alerts/{alert_id}/reject", "post", 200)
        self.assertMatches(U.get("/ngao/v1/ussd/ward-status?phone=%2B254711000001"), "/ussd/ward-status", "get", 200)
        self.assertMatches(A.post("/ngao/v1/demo/inbound-sms", {"phone": phone, "text": "magunia 20 mabichi"},
                                  format="json"), "/demo/inbound-sms", "post", 201)
        self.assertMatches(U.post("/ngao/v1/ussd/report", {"phone": "+254700000104", "report_type": "wet_bags",
                                                           "count": 40}, format="json"), "/ussd/report", "post", 201)
        reports = self.assertMatches(A.get("/ngao/v1/reports"), "/reports", "get", 200)
        rid = reports["items"][0]["report_id"]
        self.assertMatches(A.get(f"/ngao/v1/reports/{rid}"), "/reports/{report_id}", "get", 200)
        self.assertMatches(A.patch(f"/ngao/v1/reports/{rid}", {"review_status": "CORRECTED", "payload": {"count": 30}},
                                   format="json"), "/reports/{report_id}", "patch", 200)
        self.assertMatches(A.post("/ngao/v1/jobs/plan-routes", {}, format="json"), "/jobs/plan-routes", "post", 202)
        routes = self.assertMatches(A.get("/ngao/v1/routes"), "/routes", "get", 200)
        self.assertTrue(routes["routes"][0]["stops"][0]["bookings"])
        booking = Booking.objects.first()
        self.assertMatches(A.post(f"/ngao/v1/bookings/{booking.pk}/confirm"), "/bookings/{booking_id}/confirm",
                           "post", 200)
        self.assertMatches(A.get("/ngao/v1/wards?county=Uasin%20Gishu"), "/wards", "get", 200)
        self.assertMatches(A.get("/ngao/v1/wards/12/risk"), "/wards/{ward_id}/risk", "get", 200)
        self.assertMatches(A.post("/ngao/v1/jobs/dispatch", {}, format="json"), "/jobs/dispatch", "post", 202)
        self.assertMatches(A.post("/ngao/v1/jobs/grade-outcomes", {}, format="json"), "/jobs/grade-outcomes", "post", 202)
        self.assertMatches(A.post("/ngao/v1/jobs/replay", {"from": "2006-10-01", "to": "2006-11-30"}, format="json"),
                           "/jobs/replay", "post", 202)
        self.assertMatches(self.anon.get("/ngao/v1/public/scorecard"), "/public/scorecard", "get", 200)
        self.assertMatches(self.anon.get("/ngao/v1/public/calibration"), "/public/calibration", "get", 200)
        self.assertMatches(U.post("/ngao/v1/ussd/unsubscribe", {"phone": phone}, format="json"),
                           "/ussd/unsubscribe", "post", 200)
        self.assertMatches(A.post("/ngao/v1/jobs/reset-demo", {"phones": [phone]}, format="json"),
                           "/jobs/reset-demo", "post", 202)

    def test_errors_match_documentation(self):
        self.assertMatches(self.anon.get("/ngao/v1/wards"), "/wards", "get", 401)
        self.assertMatches(self.api.get("/ngao/v1/wards/999/risk"), "/wards/{ward_id}/risk", "get", 404)
        self.assertMatches(self.ussd.post("/ngao/v1/ussd/register", {"phone": "+254712345678"}, format="json"),
                           "/ussd/register", "post", 400)


class PostmanCollectionTests(NgaoTestCase):
    def test_collection_covers_every_operation(self):
        from api.postman import WALKTHROUGH, build_collection
        from ngao.management.commands.export_postman import openapi_dict

        schema = openapi_dict()
        collection = build_collection(schema)
        self.assertEqual(collection["info"]["schema"],
                         "https://schema.getpostman.com/json/collection/v2.1.0/collection.json")
        ops = [(m.upper(), p) for p, methods in schema["paths"].items() for m in methods]
        folders = {f["name"]: f["item"] for f in collection["item"]}
        reference = [i for name, items in folders.items() if name not in ("0. Demo walkthrough", "Error examples")
                     for i in items]
        self.assertEqual(len(reference), len(ops))  # every documented operation, once
        self.assertEqual(len(folders["0. Demo walkthrough"]), len(WALKTHROUGH))
        for item in reference + folders["0. Demo walkthrough"]:
            request = item["request"]
            self.assertTrue(request["url"]["raw"].startswith("{{base_url}}/"), item["name"])
            self.assertNotIn(":", "/".join(request["url"]["path"]), f"unbound path variable in {item['name']}")
            if "body" in request:
                import json
                json.loads(request["body"]["raw"].replace("{{ward_id}}", "12"))  # valid JSON once filled
        ussd = next(i for i in folders["USSD contract"])
        self.assertEqual(ussd["request"]["auth"]["bearer"][0]["value"], "{{ussd_token}}")
        public = next(i for i in folders["Public scorecard"])
        self.assertEqual(public["request"]["auth"]["type"], "noauth")
        webhook = folders["Wasiliana webhooks"][0]["request"]["url"]
        self.assertIn("k", [q["key"] for q in webhook["query"]])
