"""OpenAPI documentation helpers: the auth scheme, the §2.1 error envelope, and response shapes.

The serializers here only describe responses for Swagger UI. The views build the same dicts, and
tests/test_openapi.py checks real responses against these documented fields.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
from rest_framework import serializers

API_AUTH = [{"ApiToken": []}]
USSD_AUTH = [{"UssdToken": []}, {"ApiToken": []}]
WEBHOOK_AUTH = [{"WebhookSecret": []}, {}]
NO_AUTH = [{}]


def only_ngao_prefix(endpoints, **kwargs):
    """/api/v1 serves the same views; document them once (both prefixes are listed as servers)."""
    return [e for e in endpoints if e[0].startswith("/ngao/v1/") and "/schema" not in e[0]
            and "/docs" not in e[0] and "/redoc" not in e[0]]


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "ngao_core.auth.BearerTokenAuthentication"
    name = "ApiToken"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer",
                "description": "NGAO_API_TOKENS: APEX demo controls, scheduler, router, tests."}


# ----- errors (§2.1) -------------------------------------------------------------------------------


class ErrorBody(serializers.Serializer):
    code = serializers.ChoiceField(choices=["VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
                                            "CONFLICT", "UPSTREAM_ERROR", "INTERNAL", "METHOD_NOT_ALLOWED"])
    message = serializers.CharField()
    details = serializers.DictField(help_text="e.g. {\"field\": \"ward_id\"}; validation errors add `errors`.")
    request_id = serializers.CharField(help_text="Same value as the X-Request-ID response header.")


class ErrorResponse(serializers.Serializer):
    error = ErrorBody()


_ERROR_EXAMPLES = {
    400: ("VALIDATION_ERROR", "ward_id is required", {"field": "ward_id"}),
    401: ("UNAUTHORIZED", "Authentication credentials were not provided.", {}),
    403: ("FORBIDDEN", "This token may not call this endpoint", {}),
    404: ("NOT_FOUND", "ward 999 not found", {}),
    409: ("CONFLICT", "alert 42 is SENT, not PENDING_APPROVAL", {}),
}
_ERROR_TEXT = {400: "Validation error", 401: "Missing or invalid bearer token", 403: "Token not allowed here",
               404: "Not found", 409: "Conflict with the current state"}


def errors(*codes):
    out = {}
    for code in codes:
        name, message, details = _ERROR_EXAMPLES[code]
        out[code] = OpenApiResponse(
            ErrorResponse, description=_ERROR_TEXT[code],
            examples=[OpenApiExample(name, value={"error": {"code": name, "message": message, "details": details,
                                                            "request_id": "8f2c4e0b9a7d4c1e"}})],
        )
    return out


def example(name, value, summary=None, request=False, response=False, status="200"):
    return OpenApiExample(name, value=value, summary=summary, request_only=request, response_only=response,
                          status_codes=[status] if response else None)


DATE_PARAM = OpenApiParameter("date", str, description="Day in EAT, `YYYY-MM-DD`. Default: today.", examples=[
    OpenApiExample("today", value="2026-10-15")])
LIMIT_PARAM = OpenApiParameter("limit", int, description="Max rows (default 200, at most 1000).")


# ----- shared response shapes ----------------------------------------------------------------------


class Centroid(serializers.Serializer):
    lat = serializers.FloatField(allow_null=True)
    lon = serializers.FloatField(allow_null=True)


class WardItem(serializers.Serializer):
    ward_id = serializers.IntegerField()
    ward_name = serializers.CharField()
    county = serializers.CharField()
    centroid = Centroid()


class WardList(serializers.Serializer):
    items = WardItem(many=True)


class RiskItem(serializers.Serializer):
    alert_type = serializers.CharField(help_text="A1..A5, B1..B4, C1..C3")
    service = serializers.ChoiceField(choices=["FARM", "HERD", "HEALTH"])
    probability = serializers.FloatField(allow_null=True, help_text="Calibrated 0..1")
    confidence_pct = serializers.IntegerField(allow_null=True, help_text="0..100, shown in the SMS")
    decision = serializers.ChoiceField(choices=["ACT", "WATCH", "ABSTAIN"])
    abstain_reason = serializers.CharField(allow_null=True)
    model_version = serializers.CharField(allow_null=True)


class WardRisk(serializers.Serializer):
    ward_id = serializers.IntegerField()
    ward_name = serializers.CharField()
    score_date = serializers.DateField()
    risks = RiskItem(many=True)


class BookingItem(serializers.Serializer):
    booking_id = serializers.IntegerField()
    subscriber_id = serializers.IntegerField()
    bags = serializers.FloatField(allow_null=True)
    fee_kes = serializers.FloatField(allow_null=True, help_text="Total fee: bags x (moisture - 13) x KES 30")
    status = serializers.ChoiceField(choices=["OFFERED", "CONFIRMED", "DRIED", "NO_SHOW"])


class RouteStopItem(serializers.Serializer):
    stop_order = serializers.IntegerField()
    stop_id = serializers.IntegerField()
    point_id = serializers.IntegerField()
    point_name = serializers.CharField()
    ward_id = serializers.IntegerField()
    eta = serializers.DateTimeField(help_text="+03:00, rounded up to 5 minutes")
    planned_bags = serializers.FloatField()
    urgency = serializers.FloatField(help_text="wet_bags x P(heavy rain 48 h) x aflatoxin boost")
    bookings_offered = serializers.IntegerField()
    bookings_confirmed = serializers.IntegerField()
    bookings = BookingItem(many=True)


class RouteItem(serializers.Serializer):
    route_id = serializers.IntegerField()
    dryer_id = serializers.IntegerField()
    dryer_name = serializers.CharField(allow_null=True)
    is_placeholder = serializers.BooleanField(help_text="Demo dryers are labelled honestly")
    status = serializers.ChoiceField(choices=["PLANNED", "NOTIFIED", "DONE"])
    stops = RouteStopItem(many=True)


class RouteList(serializers.Serializer):
    route_date = serializers.DateField()
    routes = RouteItem(many=True)


class BookingConfirmed(serializers.Serializer):
    booking_id = serializers.IntegerField()
    status = serializers.CharField(help_text="Always CONFIRMED")


class ScorecardRow(serializers.Serializer):
    alert_type = serializers.CharField()
    period = serializers.DateField(help_text="Monday of the ISO week")
    alerts_sent = serializers.IntegerField(help_text="Ward warnings that reached subscribers")
    hits = serializers.IntegerField()
    false_alarms = serializers.IntegerField()
    hit_rate = serializers.FloatField(allow_null=True)
    false_alarm_rate = serializers.FloatField(allow_null=True)
    avg_lead_days = serializers.FloatField(allow_null=True)
    brier_skill = serializers.FloatField(allow_null=True, help_text="1 - BS/BS_climatology; > 0 beats the base rate")


class ScorecardList(serializers.Serializer):
    items = ScorecardRow(many=True)


class CalibrationRow(serializers.Serializer):
    alert_type = serializers.CharField()
    prob_bin = serializers.FloatField()
    predicted = serializers.FloatField()
    observed = serializers.FloatField()
    n = serializers.IntegerField()


class CalibrationList(serializers.Serializer):
    items = CalibrationRow(many=True)


class Health(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "degraded"])
    database = serializers.CharField(help_text="oracle | sqlite | unreachable")
    sms_backend = serializers.ChoiceField(choices=["console", "africastalking", "wasiliana"])
    sms_dispatch = serializers.ChoiceField(choices=["inline", "worker"])
    reply_reader = serializers.ChoiceField(choices=["rules", "genai"])
    alerts_require_approval = serializers.BooleanField()


class DispatchResult(serializers.Serializer):
    messages_queued = serializers.IntegerField()
    suppressed_cooldown = serializers.IntegerField(required=False)
    suppressed_cap = serializers.IntegerField(required=False)
    skipped = serializers.CharField(required=False, help_text="replay | stale: the alert was not sent")


class AlertApproved(serializers.Serializer):
    alert_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=["APPROVED", "SENT"])
    dispatch = DispatchResult()


class AlertRejected(serializers.Serializer):
    alert_id = serializers.IntegerField()
    status = serializers.CharField(help_text="Always REJECTED")


# ----- job summaries (§2.2) --------------------------------------------------------------------------


class ScoreSummary(serializers.Serializer):
    as_of = serializers.DateField()
    county = serializers.CharField(allow_null=True)
    model_version = serializers.CharField()
    wards = serializers.IntegerField()
    scores_written = serializers.IntegerField()
    act = serializers.IntegerField()
    watch = serializers.IntegerField()
    abstain = serializers.IntegerField()
    below_watch = serializers.IntegerField(help_text="Probability below the watch threshold: nothing stored")
    no_data = serializers.IntegerField(help_text="The rule's inputs are absent for that ward")
    kept_existing = serializers.IntegerField(help_text="Rows from another model_version (e.g. Trevor's) left untouched")
    act_wards = serializers.ListField(child=serializers.CharField(), help_text="'A1:Moiben', ...")


class ApprovedDispatchSummary(serializers.Serializer):
    alerts_dispatched = serializers.IntegerField()
    messages_queued = serializers.IntegerField()
    suppressed_cooldown = serializers.IntegerField()
    suppressed_cap = serializers.IntegerField()
    skipped_replay = serializers.IntegerField()
    skipped_stale = serializers.IntegerField()


class AlertPreview(serializers.Serializer):
    ward = serializers.CharField()
    alert_type = serializers.CharField()
    lang = serializers.ChoiceField(choices=["sw", "en"])
    body = serializers.CharField()
    template_source = serializers.ChoiceField(choices=["ALERT_TEMPLATE", "ALERT_TYPES", "DEFAULT"])


class SendAlertsSummary(serializers.Serializer):
    as_of = serializers.DateField()
    dry_run = serializers.BooleanField()
    approval_required = serializers.BooleanField()
    act_scores = serializers.IntegerField()
    alerts_created = serializers.IntegerField()
    pending_approval = serializers.IntegerField()
    messages_queued = serializers.IntegerField()
    suppressed_cooldown = serializers.IntegerField()
    suppressed_cap = serializers.IntegerField()
    already_drafted = serializers.IntegerField()
    no_subscribers = serializers.IntegerField()
    preview = AlertPreview(many=True, required=False, help_text="dry_run only")
    approved_dispatch = ApprovedDispatchSummary(required=False)


class PlanRoutesSummary(serializers.Serializer):
    route_date = serializers.DateField()
    dryers = serializers.IntegerField()
    points_with_demand = serializers.IntegerField()
    routes = serializers.IntegerField()
    stops = serializers.IntegerField()
    planned_bags = serializers.FloatField()
    unserved_bags = serializers.FloatField()
    bookings = serializers.IntegerField()
    messages_queued = serializers.IntegerField()
    replaced_routes = serializers.IntegerField()
    placeholder_dryers = serializers.IntegerField()


ReplaySummary = type("ReplaySummary", (serializers.Serializer,), {  # "from" is a Python keyword
    "scenario": serializers.CharField(),
    "county": serializers.CharField(),
    "from": serializers.DateField(),
    "to": serializers.DateField(),
    "weeks": serializers.IntegerField(),
    "scores_written": serializers.IntegerField(),
    "kept_existing": serializers.IntegerField(),
    "first_act_date": serializers.DateField(allow_null=True),
    "index_case_date": serializers.DateField(help_text="Index human case symptom onset (CDC MMWR)"),
    "lead_days_before_index_case": serializers.IntegerField(allow_null=True),
})


class ResetDemoSummary(serializers.Serializer):
    phones = serializers.IntegerField()
    reports = serializers.IntegerField()
    outcomes = serializers.IntegerField()
    bookings = serializers.IntegerField()
    routes = serializers.IntegerField()
    messages = serializers.IntegerField()
    alerts = serializers.IntegerField()
    subscribers = serializers.IntegerField()


class SmsSummary(serializers.Serializer):
    sent = serializers.IntegerField()
    simulated = serializers.IntegerField(help_text="Synthetic subscribers: marked SENT, never sent")
    failed = serializers.IntegerField()
    retry_later = serializers.IntegerField()


class DispatchSummary(serializers.Serializer):
    approved_dispatch = ApprovedDispatchSummary()
    sms = SmsSummary()


class GradeSummary(serializers.Serializer):
    as_of = serializers.DateField()
    graded = serializers.IntegerField()
    hits = serializers.IntegerField()
    misses = serializers.IntegerField()
    waiting_for_data = serializers.IntegerField()


def job_response(name, summary_serializer):
    """The §2.2 202 envelope with a typed summary, as its own named component."""
    return type(f"{name}JobAccepted", (serializers.Serializer,), {
        "job_id": serializers.IntegerField(help_text="Row in JOB_RUNS; see GET /jobs/{job_id}/status"),
        "job": serializers.CharField(),
        "status": serializers.CharField(help_text="Always DONE"),
        "summary": summary_serializer(),
    })


class JobStatus(serializers.Serializer):
    job_id = serializers.IntegerField()
    job = serializers.CharField()
    status = serializers.ChoiceField(choices=["RUNNING", "DONE", "FAILED"])
    params = serializers.DictField(allow_null=True)
    summary = serializers.DictField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)


# ----- USSD (§5) -------------------------------------------------------------------------------------


class LanguageSaved(serializers.Serializer):
    preferred_lang = serializers.ChoiceField(choices=["sw", "en"])
    subscriber_id = serializers.IntegerField()


class Registered(serializers.Serializer):
    subscriber_id = serializers.IntegerField()
    status = serializers.CharField(help_text="Always ACTIVE")


class ReportCreated(serializers.Serializer):
    report_id = serializers.IntegerField()


class WardStatusLine(serializers.Serializer):
    text = serializers.CharField(help_text="One line for the USSD screen, in the subscriber's language")
    registered = serializers.BooleanField()
    ward_id = serializers.IntegerField(allow_null=True)
    decision = serializers.ChoiceField(choices=["ACT", "WATCH", "ABSTAIN"], allow_null=True)
    alert_type = serializers.CharField(allow_null=True)


class Unsubscribed(serializers.Serializer):
    status = serializers.CharField(help_text="Always STOPPED")


# ----- SMS -------------------------------------------------------------------------------------------


class DeliveryReportBody(serializers.Serializer):
    """Africa's Talking delivery report (form-encoded)."""

    id = serializers.CharField(help_text="messageId returned when the SMS was sent (MESSAGES.provider_msg_id)")
    status = serializers.ChoiceField(choices=["Sent", "Submitted", "Buffered", "Rejected", "Success", "Failed"],
                                     help_text="Success = delivered to the handset; Failed/Rejected = FAILED")
    phoneNumber = serializers.CharField(required=False)
    networkCode = serializers.CharField(required=False, help_text="e.g. 63902 Safaricom")
    failureReason = serializers.CharField(required=False, allow_blank=True,
                                          help_text="e.g. InsufficientCredit, UserIsInactive, DeliveryFailure")
    retryCount = serializers.CharField(required=False)


class InboundSmsBody(serializers.Serializer):
    """Africa's Talking incoming message (form-encoded)."""

    to = serializers.CharField(required=False, help_text="Your shortcode")
    text = serializers.CharField(help_text="Message text")
    date = serializers.CharField(required=False)
    id = serializers.CharField(required=False, help_text="Provider message id")
    linkId = serializers.CharField(required=False, help_text="Only for premium / on-demand shortcodes")
    networkCode = serializers.CharField(required=False)


# "from" is a Python keyword: add the sender field after the class is built.
InboundSmsBody._declared_fields = {"from": serializers.CharField(help_text="Sender phone, +2547XXXXXXXX"),
                                   **InboundSmsBody._declared_fields}


class SimulatedReport(serializers.Serializer):
    report_id = serializers.IntegerField()
    report_type = serializers.CharField()
    report_count = serializers.FloatField(allow_null=True)
    review_status = serializers.ChoiceField(choices=["AUTO_ACCEPTED", "NEEDS_REVIEW"])
    ai_confidence = serializers.FloatField(allow_null=True)
    source = serializers.ChoiceField(choices=["SMS_AI", "SMS_KEYWORD"])


class SimulatedInbound(serializers.Serializer):
    message_id = serializers.IntegerField()
    report = SimulatedReport(allow_null=True, help_text="null when the text was a keyword (STOP, LUGHA EN, 1, ...)")
