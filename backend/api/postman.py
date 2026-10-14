"""Build the Postman collection (v2.1) from the OpenAPI schema, so it cannot drift from the API.

    python manage.py export_postman                       # writes postman/*.json
    python manage.py export_postman --capture-from http://127.0.0.1:8000/ngao/v1 \\
        --api-token ... --ussd-token ...                  # also saves live responses as examples

Folders:
  0. Demo walkthrough   the Friday demo in order; test scripts capture alert_id, report_id, booking_id
  1..n  one folder per OpenAPI tag, every operation with its documented examples
  Error examples        401 / 403 / 400 / 404 in the §2.1 envelope
"""
import json
import re
import uuid

SCHEMA_URL = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

VARIABLES = [
    ("base_url", "http://127.0.0.1:8000/ngao/v1", "Base URL incl. /ngao/v1 (or /api/v1)"),
    ("api_token", "change-me-apex", "NGAO_API_TOKENS value (APEX / scheduler)"),
    ("ussd_token", "change-me-ussd", "USSD_SERVICE_TOKEN value (Louisa's service)"),
    ("webhook_secret", "", "WEBHOOK_SECRET, appended to Wasiliana callbacks as ?k="),
    ("demo_phone", "+254711000001", "Presenter phone: wiped by reset-demo, sent for real"),
    ("ward_id", "12", "Ward used in the walkthrough (12 = Moiben in the synthetic seed)"),
    ("alert_id", "", "Set by 'Find the Moiben Kiswahili alert'"),
    ("report_id", "", "Set by 'List auto-accepted reports'"),
    ("booking_id", "", "Set by 'Get tomorrow's routes'"),
    ("job_id", "", "Set by every job call"),
]
PATH_VARIABLES = {"alert_id", "report_id", "booking_id", "job_id", "ward_id"}

AUTH = {
    "ApiToken": {"type": "bearer", "bearer": [{"key": "token", "value": "{{api_token}}", "type": "string"}]},
    "UssdToken": {"type": "bearer", "bearer": [{"key": "token", "value": "{{ussd_token}}", "type": "string"}]},
    None: {"type": "noauth"},
}

# ----- the demo walkthrough ---------------------------------------------------------------------------

JOB_ID = "const b = pm.response.json(); if (b.job_id) pm.collectionVariables.set('job_id', b.job_id);"

WALKTHROUGH = [
    # (operationId, name, overrides, extra test JS, python capture)
    ("health", "0. Health check", {}, "", None),
    ("job_reset_demo", "1. Reset the presenter phone (clean start)",
     {"body": {"phones": ["{{demo_phone}}"]}}, JOB_ID, None),
    ("ussd_language", "2a. USSD: first session, choose Kiswahili",
     {"body": {"phone": "{{demo_phone}}", "preferred_lang": "sw"}},
     "pm.test('language saved', () => pm.expect(pm.response.json().preferred_lang).to.eql('sw'));", None),
    ("ussd_register", "2b. USSD: register as a farmer in the ward",
     {"body": {"phone": "{{demo_phone}}", "ward_id": "{{ward_id}}", "role": "farmer", "consent": True}},
     "pm.test('active', () => pm.expect(pm.response.json().status).to.eql('ACTIVE'));", None),
    ("job_score", "3. Run morning scoring", {"body": {}},
     JOB_ID + "\npm.test('some wards ACT', () => pm.expect(b.summary.act).to.be.above(0));", None),
    ("job_send_alerts", "4a. Send alerts (drafts for officer approval)", {"body": {}}, JOB_ID, None),
    ("alerts_list", "4b. Find the ward's Kiswahili alert",
     {"query": {"status": "PENDING_APPROVAL", "ward_id": "{{ward_id}}", "lang": "sw"}},
     "const items = pm.response.json().items;\n"
     "pm.test('a pending alert exists', () => pm.expect(items.length).to.be.above(0));\n"
     "if (items.length) pm.collectionVariables.set('alert_id', items[0].alert_id);",
     ("alert_id", lambda b: b["items"][0]["alert_id"])),
    ("alert_approve", "4c. Officer approves: the SMS goes out", {"body": {"approved_by": "postman.officer"}},
     "pm.test('approved or sent', () => pm.expect(['APPROVED','SENT']).to.include(pm.response.json().status));", None),
    ("ussd_ward_status", "4d. USSD menu 3: ward status line", {"query": {"phone": "{{demo_phone}}"}},
     "pm.test('has a text line', () => pm.expect(pm.response.json().text).to.be.a('string'));", None),
    ("demo_inbound_sms", "5. Farmer replies in Kiswahili",
     {"body": {"phone": "{{demo_phone}}", "text": "Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu"}},
     "pm.test('read as wet bags', () => pm.expect(pm.response.json().report.report_type).to.eql('wet_bags'));", None),
    ("reports_list", "6a. List auto-accepted reports", {"query": {"status": "AUTO_ACCEPTED", "limit": "1"}},
     "const r = pm.response.json().items;\nif (r.length) pm.collectionVariables.set('report_id', r[0].report_id);",
     ("report_id", lambda b: b["items"][0]["report_id"])),
    ("report_review", "6b. Officer corrects the bag count",
     {"body": {"review_status": "CORRECTED", "payload": {"report_type": "wet_bags", "count": 25}}},
     "pm.test('count corrected', () => pm.expect(pm.response.json().report_count).to.eql(25));", None),
    ("job_plan_routes", "7a. Plan tomorrow's dryer routes", {"body": {}},
     JOB_ID + "\npm.test('a route was planned', () => pm.expect(b.summary.routes).to.be.above(0));", None),
    ("routes_list", "7b. Get tomorrow's routes", {"query": {}},
     "const routes = pm.response.json().routes;\n"
     "const bookings = routes.flatMap(r => r.stops.flatMap(s => s.bookings));\n"
     "pm.test('a booking was offered', () => pm.expect(bookings.length).to.be.above(0));\n"
     "if (bookings.length) pm.collectionVariables.set('booking_id', bookings[0].booking_id);",
     ("booking_id", lambda b: [k for r in b["routes"] for s in r["stops"] for k in s["bookings"]][0]["booking_id"])),
    ("booking_confirm", "7c. Farmer confirms the booking", {},
     "pm.test('confirmed', () => pm.expect(pm.response.json().status).to.eql('CONFIRMED'));", None),
    ("sms_inbound", "8. Wasiliana inbound SMS: LANG EN", {"body": {"phone": "{{demo_phone}}", "message": "LANG EN"}},
     "", None),
    ("sms_delivery_report", "9. Wasiliana delivery report", {}, "", None),
    ("job_replay", "10. Garissa 2006 RVF replay",
     {"body": {"scenario": "garissa_rvf_2006", "from": "2006-09-01", "to": "2007-01-15"}}, JOB_ID, None),
    ("job_status", "11. Look up the last job", {}, "", None),
    ("job_grade_outcomes", "12. Grade past alerts", {"body": {}}, JOB_ID, None),
    ("public_scorecard", "13. Public scorecard (no token)", {}, "", None),
    ("ussd_unsubscribe", "14. USSD: unsubscribe", {"body": {"phone": "{{demo_phone}}"}}, "", None),
    ("job_reset_demo", "15. Reset the presenter phone (clean up)", {"body": {"phones": ["{{demo_phone}}"]}}, JOB_ID, None),
]

ERROR_EXAMPLES = [
    ("wards_list", "401: no token", {"auth": None}, 401),
    ("job_score", "403: USSD token on a job", {"auth": "UssdToken", "body": {}}, 403),
    ("ussd_register", "400: ward_id missing", {"body": {"phone": "{{demo_phone}}", "role": "farmer", "consent": True}}, 400),
    ("ward_risk", "404: unknown ward", {"path": {"ward_id": "999999"}}, 404),
    ("alert_approve", "409 or 404: approve twice", {"body": {}}, None),
]


# ----- helpers --------------------------------------------------------------------------------------------


def _operations(schema):
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            yield path, method, op


def _resolve(schema, node):
    while isinstance(node, dict) and "$ref" in node:
        node = schema["components"]["schemas"][node["$ref"].split("/")[-1]]
    return node


def _request_example(op):
    content = (op.get("requestBody") or {}).get("content", {})
    media = content.get("application/json") or next(iter(content.values()), None)
    if not media:
        return None
    examples = media.get("examples") or {}
    if examples:
        return next(iter(examples.values())).get("value")
    return {}


def _scheme(op):
    for requirement in op.get("security", []):
        for name in requirement:
            return name
    return None


def _url(path, query, path_values=None):
    path_values = path_values or {}

    def repl(match):
        name = match.group(1)
        return path_values.get(name, "{{%s}}" % name if name in PATH_VARIABLES else ":" + name)

    concrete = re.sub(r"\{(\w+)\}", repl, path)
    segments = [s for s in concrete.split("/") if s]
    raw = "{{base_url}}/" + "/".join(segments)
    query_list = [{"key": k, "value": str(v), **({"disabled": True} if d else {}), "description": desc}
                  for k, v, d, desc in query]
    if query_list:
        raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in query_list if not q.get("disabled"))
    url = {"raw": raw, "host": ["{{base_url}}"], "path": segments}
    if query_list:
        url["query"] = query_list
    return url


def _query_params(op, overrides=None):
    out, overrides = [], dict(overrides or {})
    for param in op.get("parameters", []):
        if param.get("in") != "query":
            continue
        name = param["name"]
        if name in overrides:
            out.append((name, overrides.pop(name), False, param.get("description", "")))
            continue
        examples = param.get("examples") or {}
        value = next(iter(examples.values()))["value"] if examples else ""
        if not value and "enum" in (param.get("schema") or {}):
            value = param["schema"]["enum"][0]
        out.append((name, value, not param.get("required", False), param.get("description", "")))
    out.extend((k, v, False, "") for k, v in overrides.items())
    return out


def _description(op, schema):
    lines = [op.get("description", "").strip()]
    responses = op.get("responses", {})
    if responses:
        lines.append("\n**Responses:** " + ", ".join(
            f"`{code}` {(_resolve(schema, r) or {}).get('description', '')}".rstrip() for code, r in responses.items()))
    return "\n".join(line for line in lines if line)


def _bare(request):
    """A saved example's originalRequest without the long description (keeps the file small)."""
    return {k: v for k, v in request.items() if k != "description"}


def _saved_responses(op, request_name, request):
    request = _bare(request)
    saved = []
    for code, response in op.get("responses", {}).items():
        content = response.get("content", {}).get("application/json", {})
        for name, ex in (content.get("examples") or {}).items():
            saved.append({
                "name": f"{code} {ex.get('summary') or name}", "originalRequest": request,
                "status": response.get("description", ""), "code": int(code), "_postman_previewlanguage": "json",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": json.dumps(ex.get("value"), indent=2, ensure_ascii=False),
            })
    return saved


def _status_test(op, expected=None):
    codes = sorted(int(c) for c in op.get("responses", {}) if int(c) < 300)
    expect = expected if expected is not None else (codes[0] if codes else 200)
    test = [f"pm.test('status {expect}', () => pm.response.to.have.status({expect}));"]
    if expect >= 400:
        test.append("pm.test('§2.1 error envelope', () => { const e = pm.response.json().error;"
                    " pm.expect(e).to.have.keys('code','message','details','request_id'); });")
    return test


def build_item(schema, path, method, op, name=None, overrides=None, extra_test="", expected=None, reference=False):
    overrides = overrides or {}
    scheme = overrides["auth"] if "auth" in overrides else _scheme(op)
    query = _query_params(op, overrides.get("query"))
    if scheme == "WebhookSecret":
        query.append(("k", "{{webhook_secret}}", False, "WEBHOOK_SECRET (ignored silently when wrong)"))
        scheme = None
    request = {
        "method": method.upper(),
        "header": [],
        "url": _url(path, query, overrides.get("path")),
        "auth": AUTH.get(scheme, AUTH[None]),
        "description": _description(op, schema),
    }
    body = overrides.get("body", _request_example(op))
    if body is not None and method in ("post", "patch", "put"):
        request["header"].append({"key": "Content-Type", "value": "application/json"})
        request["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2, ensure_ascii=False),
                           "options": {"raw": {"language": "json"}}}
        # "{{ward_id}}" must be a JSON number in the body
        request["body"]["raw"] = request["body"]["raw"].replace('"{{ward_id}}"', "{{ward_id}}")
    if reference:
        tests = ["pm.test('no server error', () => pm.expect(pm.response.code).to.be.below(500));",
                 "if (pm.response.code >= 400) pm.test('§2.1 error envelope', () => "
                 "pm.expect(pm.response.json().error).to.have.keys('code','message','details','request_id'));"]
    else:
        tests = _status_test(op, expected)
    if extra_test:
        tests.extend(extra_test.splitlines())
    item = {"name": name or op.get("summary") or op["operationId"], "request": request,
            "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}],
            "response": _saved_responses(op, name, request)}
    return item


def build_collection(schema):
    ops = {op["operationId"]: (path, method, op) for path, method, op in _operations(schema)}

    walkthrough = []
    for op_id, name, overrides, extra, _capture in WALKTHROUGH:
        path, method, op = ops[op_id]
        walkthrough.append(build_item(schema, path, method, op, name, overrides, extra))

    tag_order = [t["name"] for t in schema.get("tags", [])]
    by_tag = {}
    for path, method, op in _operations(schema):
        by_tag.setdefault(op.get("tags", ["Other"])[0], []).append(build_item(schema, path, method, op, reference=True))
    reference = [{"name": tag, "description": next((t.get("description", "") for t in schema.get("tags", [])
                                                    if t["name"] == tag), ""), "item": by_tag[tag]}
                 for tag in tag_order + sorted(set(by_tag) - set(tag_order)) if tag in by_tag]

    errors = []
    for op_id, name, overrides, expected in ERROR_EXAMPLES:
        path, method, op = ops[op_id]
        item = build_item(schema, path, method, op, name, overrides, expected=expected or 409)
        if expected is None:
            item["event"][0]["script"]["exec"] = [
                "pm.test('conflict or not found', () => pm.expect([404, 409]).to.include(pm.response.code));",
                "pm.test('§2.1 error envelope', () => pm.expect(pm.response.json().error)"
                ".to.have.keys('code','message','details','request_id'));"]
        errors.append(item)

    info = schema["info"]
    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "ngao-mvuasmart-backend")),
            "name": "Ngao · MvuaSmart backend",
            "description": (
                f"{info['title']}, {info['version']}. Generated from the OpenAPI schema by "
                "`python manage.py export_postman`; do not edit by hand.\n\n"
                "1. Import this collection and `ngao-local.postman_environment.json`.\n"
                "2. Set `api_token`, `ussd_token` (and `webhook_secret` if used) to your `.env` values.\n"
                "3. Locally: `manage.py seed_demo` then run **0. Demo walkthrough** top to bottom "
                "(Collection Runner, or `npx newman run`).\n\n"
                "The walkthrough captures `alert_id`, `report_id`, `booking_id` and `job_id` into collection "
                "variables, so the reference folders can be called right after it. Swagger UI: `{{base_url}}/docs/`."
            ),
            "schema": SCHEMA_URL,
        },
        "auth": AUTH["ApiToken"],
        "variable": [{"key": k, "value": v, "description": d} for k, v, d in VARIABLES],
        "item": [
            {"name": "0. Demo walkthrough", "description": "The Friday demo in order (needs the synthetic seed or a "
                                                          "ward with ACT scores in `ward_id`).", "item": walkthrough},
            *reference,
            {"name": "Error examples", "description": "The §2.1 error envelope for each status.", "item": errors},
        ],
    }


def build_environment(name, base_url):
    def var(key, value, secret=False):
        return {"key": key, "value": value, "type": "secret" if secret else "default", "enabled": True}

    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ngao-env-{name}")),
        "name": f"Ngao {name}",
        "values": [var("base_url", base_url), var("api_token", "change-me-apex", True),
                   var("ussd_token", "change-me-ussd", True), var("webhook_secret", "", True),
                   var("demo_phone", "+254711000001"), var("ward_id", "12")],
        "_postman_variable_scope": "environment",
    }


# ----- optional: save real responses from a running server as examples --------------------------------


def capture_live(collection, base_url, variables):
    """Execute the walkthrough against a live server; attach each response as a saved example."""
    import requests

    values = {**{v["key"]: v["value"] for v in collection["variable"]}, **variables, "base_url": base_url.rstrip("/")}

    def fill(text):
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)

    walkthrough = collection["item"][0]["item"]
    results = []
    for item, (_op, _name, _overrides, _extra, capture) in zip(walkthrough, WALKTHROUGH, strict=True):
        request = item["request"]
        url = fill(request["url"]["raw"])
        headers = {h["key"]: h["value"] for h in request["header"]}
        auth = request["auth"]
        if auth["type"] == "bearer":
            headers["Authorization"] = "Bearer " + fill(auth["bearer"][0]["value"])
        body = fill(request["body"]["raw"]) if "body" in request else None
        response = requests.request(request["method"], url, data=body, headers=headers, timeout=60)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        results.append((item["name"], response.status_code))
        if capture and payload:
            key, getter = capture
            try:
                values[key] = getter(payload)
            except (KeyError, IndexError, TypeError):
                pass
        if "job_id" in (payload or {}) and isinstance(payload, dict):
            values["job_id"] = payload["job_id"]
        item["response"].insert(0, {
            "name": f"{response.status_code} live response", "originalRequest": _bare(request),
            "status": response.reason, "code": response.status_code, "_postman_previewlanguage": "json",
            "header": [{"key": "Content-Type", "value": response.headers.get("Content-Type", "")}],
            "body": json.dumps(payload, indent=2, ensure_ascii=False) if payload is not None else response.text,
        })
    return results
