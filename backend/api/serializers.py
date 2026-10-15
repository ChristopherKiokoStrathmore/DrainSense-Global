"""Request validation and response shapes for /ngao/v1 (API_CONTRACT §2.2, §2.3)."""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ngao.models import Alert, Report
from reply_reader.schema import REPORT_TYPES


# ----- §2.2 job bodies ---------------------------------------------------------------------------


class ScoreJobIn(serializers.Serializer):
    as_of = serializers.DateField(required=False, help_text="Score date (EAT). Default: today")
    county = serializers.CharField(required=False, allow_blank=False, max_length=60, help_text="Default: all counties")
    alert_types = serializers.ListField(child=serializers.CharField(max_length=3), required=False, allow_empty=False,
                                        help_text="Default: every type with a rule (A1-A4, B1-B4, C2, C3)")
    overwrite = serializers.BooleanField(required=False, default=False,
                                         help_text="Replace rows from another model_version (e.g. Trevor's models)")
    dry_run = serializers.BooleanField(required=False, default=False, help_text="Count decisions, write nothing")


class SendAlertsJobIn(serializers.Serializer):
    as_of = serializers.DateField(required=False, help_text="Score date whose ACT rows are alerted. Default: today")
    dry_run = serializers.BooleanField(required=False, default=False, help_text="Render up to 5 previews, write nothing")
    county = serializers.CharField(required=False, max_length=60)


class PlanRoutesJobIn(serializers.Serializer):
    route_date = serializers.DateField(required=False, help_text="Default: tomorrow (EAT)")
    replace = serializers.BooleanField(required=False, default=True,
                                       help_text="Re-plan when that date only has unconfirmed bookings")
    notify = serializers.BooleanField(required=False, default=True, help_text="Queue booking + operator SMS")


# "from" and "to" are Python keywords, so the class is built with type() to declare them as fields.
ReplayJobIn = type("ReplayJobIn", (serializers.Serializer,), {
    "scenario": serializers.ChoiceField(choices=["garissa_rvf_2006"], required=False, default="garissa_rvf_2006"),
    "from": serializers.DateField(required=False, help_text="First replay date (default 2006-09-01)"),
    "to": serializers.DateField(required=False, help_text="Last replay date (default 2007-01-15)"),
    "overwrite": serializers.BooleanField(required=False, default=False,
                                          help_text="Also replace replay rows written by another model_version"),
})


class ResetDemoJobIn(serializers.Serializer):
    phones = serializers.ListField(child=serializers.CharField(max_length=20), required=False,
                                   help_text="Presenter phones to wipe. Default: DEMO_PHONES")
    alerts_today = serializers.BooleanField(required=False, default=True, help_text="Also delete alerts drafted today")
    routes = serializers.BooleanField(required=False, default=True, help_text="Also delete routes from today on")


class DispatchJobIn(serializers.Serializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=1000, help_text="Max SMS this call")
    retry_failed = serializers.BooleanField(required=False, default=False,
                                            help_text="Re-queue OUT messages that FAILED in the last 24 h")


class GradeJobIn(serializers.Serializer):
    as_of = serializers.DateField(required=False, help_text="Grade windows that ended before this date")


class SeasonalOutlookJobIn(serializers.Serializer):
    year = serializers.IntegerField(required=False, min_value=1982, max_value=2100,
                                    help_text="Season year (OND <year>). Default: this year from October, else last year")
    source = serializers.ChoiceField(choices=["auto", "sst", "era5"], required=False,
                                     help_text="auto: full 6-index model when ERA5 (CDSAPI_KEY) is configured, else "
                                               "SST-only. Default: SEASONAL_FEATURE_SOURCE")
    dry_run = serializers.BooleanField(required=False, default=False, help_text="Compute, write nothing")


JOB_INPUTS = {
    "score": ScoreJobIn,
    "send-alerts": SendAlertsJobIn,
    "plan-routes": PlanRoutesJobIn,
    "replay": ReplayJobIn,
    "reset-demo": ResetDemoJobIn,
    "dispatch": DispatchJobIn,
    "grade-outcomes": GradeJobIn,
    "seasonal-outlook": SeasonalOutlookJobIn,
}


def parse_job_params(name, data):
    serializer = JOB_INPUTS[name](data=data or {})
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


# ----- §2.3 reads --------------------------------------------------------------------------------


class ReportOut(serializers.ModelSerializer):
    raw_text = serializers.SerializerMethodField()
    received_at = serializers.DateTimeField(source="created_at")

    class Meta:
        model = Report
        fields = ["report_id", "ward_id", "report_type", "report_count", "unit", "ai_confidence",
                  "review_status", "raw_text", "source", "payload", "received_at", "reviewed_by_id"]

    @extend_schema_field({"type": "string", "nullable": True, "description": "The SMS text (or USSD summary)"})
    def get_raw_text(self, report):
        return report.message.body if report.message_id and report.message else None


class ReportPatchIn(serializers.Serializer):
    review_status = serializers.ChoiceField(choices=[Report.ACCEPTED, Report.CORRECTED, Report.REJECTED])
    payload = serializers.DictField(required=False, help_text="Corrected fields, merged into the stored payload; "
                                    "report_type and count also update the columns. Required for CORRECTED")
    reviewed_by = serializers.IntegerField(required=False, allow_null=True, help_text="APP_USERS.user_id")

    def validate(self, attrs):
        payload = attrs.get("payload")
        if attrs["review_status"] == Report.CORRECTED and not payload:
            raise serializers.ValidationError({"payload": "payload is required when review_status is CORRECTED"})
        if payload:
            report_type = payload.get("report_type")
            if report_type is not None and report_type not in REPORT_TYPES:
                raise serializers.ValidationError({"payload": f"report_type must be one of {REPORT_TYPES}"})
            count = payload.get("count")
            if count is not None and (isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0):
                raise serializers.ValidationError({"payload": "count must be a non-negative number"})
        return attrs


class AlertOut(serializers.ModelSerializer):
    ward_name = serializers.CharField(source="ward.ward_name")
    county = serializers.CharField(source="ward.county")
    subscriber_id = serializers.IntegerField()
    phone = serializers.CharField(source="subscriber.phone")
    probability = serializers.FloatField(source="score.probability", default=None)
    confidence_pct = serializers.IntegerField(source="score.confidence_pct", default=None)
    score_date = serializers.DateField(source="score.score_date", default=None)
    messages = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = ["alert_id", "score_id", "subscriber_id", "phone", "alert_type_id", "ward_id", "ward_name",
                  "county", "hazard", "severity", "lang", "body", "status", "probability", "confidence_pct",
                  "score_date", "created_at", "approved_by", "approved_at", "sent_at", "messages"]

    @extend_schema_field({"type": "object", "additionalProperties": {"type": "integer"},
                          "description": "The SMS linked by ALERTS.MESSAGE_ID, e.g. {\"SENT\": 1}. Empty until queued."})
    def get_messages(self, alert):
        if not alert.message_id or alert.message is None:
            return {}
        return {alert.message.status: 1}


class ApproveIn(serializers.Serializer):
    approved_by = serializers.CharField(max_length=100, required=False, default="api",
                                        help_text="Officer name, stored in ALERTS.approved_by")


class ReportList(serializers.Serializer):
    items = ReportOut(many=True)


class AlertList(serializers.Serializer):
    items = AlertOut(many=True)
