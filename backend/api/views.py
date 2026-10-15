"""REST /ngao/v1 (API_CONTRACT §2): action jobs, reads for tests/PWA, alert approval, scorecard.

Every view carries an @extend_schema block; that text is what Swagger UI (/ngao/v1/docs) shows.
"""
import datetime as dt
import re

from django.conf import settings
from django.db import connection, transaction
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from alerts import engine
from alerts.scorecard import calibration, scorecard
from api import openapi as doc
from api.jobs import run_job
from api.serializers import (JOB_INPUTS, AlertList, AlertOut, ApproveIn, ReportList, ReportOut, ReportPatchIn,
                             parse_job_params)
from ngao.models import Alert, AppUser, JobRun, Report, RiskScore, Ward
from ngao_core.errors import InvalidInput, NotFoundError
from ngao_core.timeutil import EAT, iso_eat, today_eat
from routing.service import confirm_booking, routes_for


def _query_date(request, name, default=None):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    value = parse_date(raw)
    if value is None:
        raise InvalidInput(f"{name} must be a date (YYYY-MM-DD)", field=name)
    return value


def _query_datetime(request, name):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    # An unencoded "+03:00" arrives as " 03:00" in a query string.
    raw = re.sub(r" (\d{2}:?\d{2})$", r"+\1", raw.strip())
    value = parse_datetime(raw) or (parse_date(raw) and dt.datetime.combine(parse_date(raw), dt.time.min))
    if value is None:
        raise InvalidInput(f"{name} must be an ISO-8601 datetime", field=name)
    if value.tzinfo is None:
        value = value.replace(tzinfo=EAT)
    return value


def _query_int(request, name, default=None, maximum=None):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    if not raw.isdigit():
        raise InvalidInput(f"{name} must be a positive integer", field=name)
    value = int(raw)
    return min(value, maximum) if maximum else value


# ----- health ------------------------------------------------------------------------------------


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=["Health & docs"], auth=doc.NO_AUTH, operation_id="health",
        summary="Liveness + configuration",
        description="Checks the database connection and reports which SMS backend, dispatch mode and reply "
                    "reader are active. `503` when the database is unreachable. Unauthenticated.",
        responses={200: doc.Health, 503: doc.Health},
        examples=[doc.example("ok", {"status": "ok", "database": "oracle", "sms_backend": "africastalking",
                                     "sms_dispatch": "inline", "reply_reader": "genai",
                                     "alerts_require_approval": True}, response=True)],
    )
    def get(self, request):
        db_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL" if connection.vendor == "oracle" else "SELECT 1")
        except Exception:
            db_ok = False
        return Response({
            "status": "ok" if db_ok else "degraded",
            "database": connection.vendor if db_ok else "unreachable",
            "sms_backend": settings.NGAO_SMS_BACKEND,
            "sms_dispatch": settings.NGAO_SMS_DISPATCH,
            "reply_reader": settings.NGAO_REPLY_READER,
            "alerts_require_approval": settings.NGAO_ALERTS_REQUIRE_APPROVAL,
        }, status=200 if db_ok else 503)


# ----- §2.2 jobs ---------------------------------------------------------------------------------


class _JobView(APIView):
    """POST /jobs/<job>: validate the body, run the job synchronously, record it in JOB_RUNS."""

    job = None

    def post(self, request):
        params = parse_job_params(self.job, request.data)
        run = run_job(self.job, params)
        return Response({"job_id": run.job_id, "job": self.job, "status": run.status, "summary": run.summary},
                        status=status.HTTP_202_ACCEPTED)


def _job_schema(job, summary, title, description, request_examples, summary_example, error_codes=(400, 401, 403)):
    return extend_schema(
        tags=["Jobs"], auth=doc.API_AUTH, operation_id=f"job_{job.replace('-', '_')}", summary=title,
        description=description + "\n\nAnswers `202` once the job has **finished** (`status: DONE`); "
                                  "the row stays in `JOB_RUNS` (see `GET /jobs/{job_id}/status`).",
        request=JOB_INPUTS[job],
        responses={202: doc.job_response(job.title().replace("-", ""), summary), **doc.errors(*error_codes)},
        examples=[doc.example(name, body, request=True) for name, body in request_examples]
        + [doc.example("done", {"job_id": 1042, "job": job, "status": "DONE", "summary": summary_example},
                       response=True, status="202")],
    )


class ScoreJobView(_JobView):
    job = "score"

    @_job_schema(
        "score", doc.ScoreSummary, "Score wards → RISK_SCORES",
        "Runs the **heuristic-v0** fallback rules on leakage-safe inputs (only rows with "
        "`available_date <= as_of`) and upserts `RISK_SCORES` with `model_version='heuristic-v0-backend'`.\n\n"
        "- Decisions: `ACT` (≥ act threshold), `WATCH` (≥ watch threshold), `ABSTAIN` (GFS/ECMWF disagree or "
        "inputs partly missing, with `abstain_reason`). Below watch nothing is stored.\n"
        "- Rows written by **another model_version** (Trevor's trained models) are kept unless `overwrite=true`.",
        [("contract", {"as_of": "2026-10-15", "county": "Uasin Gishu"}), ("everything today", {}),
         ("dry run", {"dry_run": True}), ("only dry window + heavy rain", {"alert_types": ["A1", "A2"]})],
        {"as_of": "2026-10-15", "county": "Uasin Gishu", "model_version": "heuristic-v0-backend", "wards": 31,
         "scores_written": 14, "act": 2, "watch": 1, "abstain": 1, "below_watch": 26, "no_data": 50,
         "kept_existing": 0, "act_wards": ["A2:Kapseret", "A1:Moiben"]},
    )
    def post(self, request):
        return super().post(request)


class SendAlertsJobView(_JobView):
    job = "send-alerts"

    @_job_schema(
        "send-alerts", doc.SendAlertsSummary, "Draft alerts from ACT scores (and send approved ones)",
        "For every `ACT` score of `as_of` (not replay, not A5) drafts **one `ALERTS` row per language** spoken by "
        "the ward's eligible subscribers (ACTIVE, consented, role in the alert's audience). The body is rendered "
        "from `ALERT_TEMPLATE` (hazard, severity, lang) in Swahili or English.\n\n"
        "- `ALERTS_REQUIRE_APPROVAL=true` (default): status `PENDING_APPROVAL`; an officer approves in APEX or via "
        "`POST /alerts/{alert_id}/approve`.\n"
        "- Otherwise: `APPROVED` by `auto` and fanned out at once.\n"
        "- A ward-level cooldown (`ALERT_TYPES.cooldown_hours`, 48 h) stops repeat drafts; reruns are idempotent "
        "(`already_drafted`).\n- Also fans out any alert already `APPROVED` in APEX (`approved_dispatch`).",
        [("contract", {"as_of": "2026-10-15", "dry_run": False}), ("preview only", {"dry_run": True})],
        {"as_of": "2026-10-15", "dry_run": False, "approval_required": True, "act_scores": 2, "alerts_created": 4,
         "pending_approval": 4, "messages_queued": 0, "suppressed_cooldown": 0, "suppressed_cap": 0,
         "already_drafted": 0, "no_subscribers": 0,
         "approved_dispatch": {"alerts_dispatched": 0, "messages_queued": 0, "suppressed_cooldown": 0,
                               "suppressed_cap": 0, "skipped_replay": 0, "skipped_stale": 0}},
    )
    def post(self, request):
        return super().post(request)


class PlanRoutesJobView(_JobView):
    job = "plan-routes"

    @_job_schema(
        "plan-routes", doc.PlanRoutesSummary, "Plan tomorrow's dryer routes + booking SMS",
        "Greedy dryer router (ARCHITECTURE §6.3):\n\n"
        "1. **Demand**: accepted `wet_bags` reports of the last 3 days, the latest per farmer, at the ward's "
        "collection point.\n"
        "2. **Urgency**: wet bags × P(heavy rain 48 h, A2) × aflatoxin boost (A3 ACT 1.5, WATCH 1.2).\n"
        "3. **Capacity**: t/h × hours ÷ 0.09 bags per day; the most urgent point goes to the dryer with "
        "the most room; points are split across dryers when needed.\n"
        "4. **ETA**: 08:00 EAT + travel at 30 km/h + drying time, rounded up to 5 minutes.\n\n"
        "Creates `ROUTES`, `ROUTE_STOPS`, `BOOKINGS (OFFERED)` and queues the A5 booking SMS (`Jibu 1`) plus "
        "each operator's stop list. `409` once any booking for that date is confirmed.",
        [("contract", {"route_date": "2026-10-16"}), ("tomorrow", {}), ("plan without SMS", {"notify": False})],
        {"route_date": "2026-10-16", "dryers": 2, "points_with_demand": 2, "routes": 1, "stops": 2,
         "planned_bags": 60.0, "unserved_bags": 0.0, "bookings": 2, "messages_queued": 3, "replaced_routes": 0,
         "placeholder_dryers": 2},
        error_codes=(400, 401, 403, 409),
    )
    def post(self, request):
        return super().post(request)


class ReplayJobView(_JobView):
    job = "replay"

    @_job_schema(
        "replay", doc.ReplaySummary, "Run the Garissa 2006 RVF replay",
        "Re-runs the scorer week by week with `is_replay='Y'`, reading only data that was available on each "
        "date. Returns the first `ACT` date and the lead time before the index human case (symptom onset "
        "30 Nov 2006, CDC MMWR). Replay rows are **never** alerted.",
        [("contract", {"scenario": "garissa_rvf_2006", "from": "2006-09-01", "to": "2007-01-15"})],
        {"scenario": "garissa_rvf_2006", "county": "Garissa", "from": "2006-09-01", "to": "2007-01-15", "weeks": 20,
         "scores_written": 90, "kept_existing": 0, "first_act_date": "2006-11-03", "index_case_date": "2006-11-30",
         "lead_days_before_index_case": 27},
    )
    def post(self, request):
        return super().post(request)


class ResetDemoJobView(_JobView):
    job = "reset-demo"

    @_job_schema(
        "reset-demo", doc.ResetDemoSummary, "Reset the live demo",
        "Deletes everything belonging to the presenter phones (`phones`, default `DEMO_PHONES`): the subscriber "
        "row (so the USSD language prompt shows again), messages, reports, bookings, farms, herds, USSD sessions. "
        "Also deletes alerts drafted today and routes from today on. The synthetic dataset is untouched.",
        [("contract", {}), ("explicit phone", {"phones": ["+254711000001"]})],
        {"phones": 1, "reports": 2, "outcomes": 0, "bookings": 2, "routes": 1, "messages": 9, "alerts": 4,
         "subscribers": 1},
    )
    def post(self, request):
        return super().post(request)


class DispatchJobView(_JobView):
    job = "dispatch"

    @_job_schema(
        "dispatch", doc.DispatchSummary, "Send approved alerts and queued SMS now",
        "Fans out every `APPROVED` alert that has no messages yet (e.g. approved on the APEX page), then sends "
        "the `MESSAGES` queue through Africa's Talking. The scheduler does this every 15 s anyway. Synthetic "
        "subscribers are marked `SENT` with `provider_msg_id='SIMULATED'`.",
        [("now", {}), ("retry failures", {"retry_failed": True})],
        {"approved_dispatch": {"alerts_dispatched": 1, "messages_queued": 3, "suppressed_cooldown": 0,
                               "suppressed_cap": 0, "skipped_replay": 0, "skipped_stale": 0},
         "sms": {"sent": 1, "simulated": 2, "failed": 0, "retry_later": 0}},
    )
    def post(self, request):
        return super().post(request)


class GradeOutcomesJobView(_JobView):
    job = "grade-outcomes"

    @_job_schema(
        "grade-outcomes", doc.GradeSummary, "Grade past alerts → OUTCOMES (scorecard)",
        "Grades ACT/WATCH scores whose window has passed against observed rain in `WARD_FEATURES_DAILY`: "
        "A1 hit = 3 consecutive days < 2 mm starting on day 1-5; A2 hit = a day > 20 mm on day 1-2. "
        "Scores without all observations wait (`waiting_for_data`).",
        [("today", {})],
        {"as_of": "2026-10-20", "graded": 3, "hits": 2, "misses": 1, "waiting_for_data": 4},
    )
    def post(self, request):
        return super().post(request)


class JobDetailView(APIView):
    @extend_schema(
        tags=["Jobs"], auth=doc.API_AUTH, operation_id="job_status", summary="One job run",
        description="The `JOB_RUNS` row for a job id returned by any `/jobs/*` call: params, summary, error text.",
        responses={200: doc.JobStatus, **doc.errors(401, 404)},
    )
    def get(self, request, job_id):
        run = JobRun.objects.filter(pk=job_id).first()
        if run is None:
            raise NotFoundError(f"job {job_id} not found")
        return Response({"job_id": run.job_id, "job": run.job_name, "status": run.status, "params": run.params,
                         "summary": run.summary, "error": run.error_text, "started_at": iso_eat(run.started_at),
                         "finished_at": iso_eat(run.finished_at)})


# ----- §2.3 reads --------------------------------------------------------------------------------


class WardsView(APIView):
    @extend_schema(
        tags=["Wards & risk"], auth=doc.API_AUTH, operation_id="wards_list", summary="List wards",
        parameters=[OpenApiParameter("county", str, description="Exact county name, case-insensitive",
                                     examples=[doc.example("Uasin Gishu", "Uasin Gishu")])],
        responses={200: doc.WardList, **doc.errors(401)},
        examples=[doc.example("contract", {"items": [{"ward_id": 12, "ward_name": "Moiben", "county": "Uasin Gishu",
                                                      "centroid": {"lat": 0.82, "lon": 35.38}}]}, response=True)],
    )
    def get(self, request):
        wards = Ward.objects.all()
        county = request.query_params.get("county")
        if county:
            wards = wards.filter(county__iexact=county.strip())
        return Response({"items": [
            {"ward_id": w.ward_id, "ward_name": w.ward_name, "county": w.county,
             "centroid": {"lat": w.centroid_lat, "lon": w.centroid_lon}}
            for w in wards.order_by("county", "ward_name")
        ]})


class WardRiskView(APIView):
    @extend_schema(
        tags=["Wards & risk"], auth=doc.API_AUTH, operation_id="ward_risk", summary="A ward's decisions for a day",
        description="Live (non-replay) `RISK_SCORES` rows for the ward and date, optionally one service.",
        parameters=[doc.DATE_PARAM,
                    OpenApiParameter("service", str, enum=["FARM", "HERD", "HEALTH"],
                                     description="FARM = MvuaSmart, HERD = Ngao Mifugo, HEALTH = Ngao Afya")],
        responses={200: doc.WardRisk, **doc.errors(400, 401, 404)},
        examples=[doc.example("contract", {
            "ward_id": 12, "ward_name": "Moiben", "score_date": "2026-10-15", "risks": [
                {"alert_type": "A1", "service": "FARM", "probability": 0.75, "confidence_pct": 75, "decision": "ACT",
                 "abstain_reason": None, "model_version": "a1-xgb-2026-10-14-3f2a1c9"},
                {"alert_type": "A2", "service": "FARM", "probability": 0.41, "confidence_pct": 41, "decision": "WATCH",
                 "abstain_reason": None, "model_version": "heuristic-v0-backend"}]}, response=True)],
    )
    def get(self, request, ward_id):
        ward = Ward.objects.filter(pk=ward_id).first()
        if ward is None:
            raise NotFoundError(f"ward {ward_id} not found")
        day = _query_date(request, "date", today_eat())
        scores = RiskScore.objects.filter(ward=ward, score_date=day, is_replay=False).select_related("alert_type")
        service = request.query_params.get("service")
        if service:
            service = service.upper()
            if service not in ("FARM", "HERD", "HEALTH"):
                raise InvalidInput("service must be FARM, HERD or HEALTH", field="service")
            scores = scores.filter(alert_type__service=service)
        return Response({
            "ward_id": ward.ward_id, "ward_name": ward.ward_name, "score_date": day.isoformat(),
            "risks": [
                {"alert_type": s.alert_type_id, "service": s.alert_type.service, "probability": s.probability,
                 "confidence_pct": s.confidence_pct, "decision": s.decision, "abstain_reason": s.abstain_reason,
                 "model_version": s.model_version}
                for s in scores.order_by("alert_type")
            ],
        })


REPORT_EXAMPLE = {"report_id": 77, "ward_id": 12, "report_type": "wet_bags", "report_count": 20, "unit": "bags",
                  "ai_confidence": 0.62, "review_status": "NEEDS_REVIEW", "raw_text": "magunia 20 mabichi",
                  "source": "SMS_AI", "received_at": "2026-10-15T09:12:03+03:00", "reviewed_by_id": None,
                  "payload": {"report_type": "wet_bags", "count": 20, "unit": "bags", "crop": "maize",
                              "species": None, "rain_reported": False, "moisture_pct": None, "language": "sw",
                              "confidence": 0.62, "review_reason": "LOW_CONFIDENCE"}}


class ReportsView(APIView):
    @extend_schema(
        tags=["Reports"], auth=doc.API_AUTH, operation_id="reports_list", summary="List reports (inbox / review queue)",
        description="Newest first. `raw_text` is the farmer's SMS (or a USSD summary). Reports with "
                    "`NEEDS_REVIEW` are the officers' review queue (`V_REVIEW_QUEUE`).",
        parameters=[
            OpenApiParameter("status", str, enum=["AUTO_ACCEPTED", "NEEDS_REVIEW", "ACCEPTED", "CORRECTED", "REJECTED"],
                             description="review_status"),
            OpenApiParameter("since", str, description="ISO-8601 with offset, e.g. `2026-10-15T00:00:00+03:00` "
                                                       "(URL-encode the `+` as `%2B`)"),
            OpenApiParameter("ward_id", int), doc.LIMIT_PARAM,
        ],
        responses={200: ReportList, **doc.errors(400, 401)},
        examples=[doc.example("contract", {"items": [REPORT_EXAMPLE]}, response=True)],
    )
    def get(self, request):
        reports = Report.objects.select_related("message").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            reports = reports.filter(review_status=status_filter.upper())
        since = _query_datetime(request, "since")
        if since:
            reports = reports.filter(created_at__gte=since)
        ward_id = _query_int(request, "ward_id")
        if ward_id:
            reports = reports.filter(ward_id=ward_id)
        limit = _query_int(request, "limit", 200, maximum=1000)
        return Response({"items": ReportOut(reports[:limit], many=True).data})


class ReportDetailView(APIView):
    def _get(self, report_id):
        report = Report.objects.select_related("message").filter(pk=report_id).first()
        if report is None:
            raise NotFoundError(f"report {report_id} not found")
        return report

    @extend_schema(tags=["Reports"], auth=doc.API_AUTH, operation_id="report_get", summary="One report",
                   responses={200: ReportOut, **doc.errors(401, 404)})
    def get(self, request, report_id):
        return Response(ReportOut(self._get(report_id)).data)

    @extend_schema(
        tags=["Reports"], auth=doc.API_AUTH, operation_id="report_review", summary="Officer review: accept / correct / reject",
        description="Same write APEX makes (§1.3). `CORRECTED` needs a `payload`; its `report_type` and `count` "
                    "also update the report's columns, so a corrected wet-bag count moves the dryer plan.",
        request=ReportPatchIn,
        responses={200: ReportOut, **doc.errors(400, 401, 404)},
        examples=[
            doc.example("correct the count", {"review_status": "CORRECTED",
                                              "payload": {"report_type": "wet_bags", "count": 25}}, request=True),
            doc.example("accept", {"review_status": "ACCEPTED", "reviewed_by": 3}, request=True),
            doc.example("reject", {"review_status": "REJECTED"}, request=True),
        ],
    )
    def patch(self, request, report_id):
        body = ReportPatchIn(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        with transaction.atomic():
            report = self._get(report_id)
            report.review_status = data["review_status"]
            fields = ["review_status"]
            if data.get("payload"):
                merged = dict(report.payload or {}) if isinstance(report.payload, dict) else {}
                merged.update(data["payload"])
                report.payload = merged
                fields.append("payload")
                if data["payload"].get("report_type"):
                    report.report_type = data["payload"]["report_type"]
                    fields.append("report_type")
                if "count" in data["payload"]:
                    report.report_count = data["payload"]["count"]
                    fields.append("report_count")
            if data.get("reviewed_by") is not None:
                if not AppUser.objects.filter(pk=data["reviewed_by"]).exists():
                    raise InvalidInput(f"reviewed_by {data['reviewed_by']} is not an APP_USERS id", field="reviewed_by")
                report.reviewed_by_id = data["reviewed_by"]
                fields.append("reviewed_by")
            report.save(update_fields=fields)
        return Response(ReportOut(report).data)


class RoutesView(APIView):
    @extend_schema(
        tags=["Dryer routes"], auth=doc.API_AUTH, operation_id="routes_list", summary="Dryer plan for a day",
        description="Routes, ordered stops and bookings (`V_DRYER_PLAN`). Default date: tomorrow.",
        parameters=[OpenApiParameter("date", str, description="`YYYY-MM-DD`; default tomorrow (EAT)")],
        responses={200: doc.RouteList, **doc.errors(400, 401)},
        examples=[doc.example("contract", {"route_date": "2026-10-16", "routes": [{
            "route_id": 5, "dryer_id": 2, "dryer_name": "Mobile dryer 2 (placeholder)", "is_placeholder": True,
            "status": "NOTIFIED", "stops": [{
                "stop_order": 1, "stop_id": 9, "point_id": 3, "point_name": "Moiben market", "ward_id": 12,
                "eta": "2026-10-16T08:00:00+03:00", "planned_bags": 140, "urgency": 112.0, "bookings_offered": 3,
                "bookings_confirmed": 1, "bookings": [{"booking_id": 91, "subscriber_id": 44, "bags": 20,
                                                       "fee_kes": 4200, "status": "CONFIRMED"}]}]}]}, response=True)],
    )
    def get(self, request):
        day = _query_date(request, "date", today_eat() + dt.timedelta(days=1))
        return Response({"route_date": day.isoformat(), "routes": routes_for(day)})


class BookingConfirmView(APIView):
    @extend_schema(
        tags=["Dryer routes"], auth=doc.API_AUTH, operation_id="booking_confirm", summary="Confirm a booking",
        description="`OFFERED` → `CONFIRMED` and a confirmation SMS in the farmer's language. Same effect as the "
                    "farmer replying `1`. Confirming twice is harmless; `409` for DRIED/NO_SHOW.",
        request=None, responses={200: doc.BookingConfirmed, **doc.errors(401, 404, 409)},
        examples=[doc.example("contract", {"booking_id": 91, "status": "CONFIRMED"}, response=True)],
    )
    def post(self, request, booking_id):
        booking = confirm_booking(booking_id)
        return Response({"booking_id": booking.booking_id, "status": booking.status})


class PublicScorecardView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=["Public scorecard"], auth=doc.NO_AUTH, operation_id="public_scorecard", summary="Public scorecard",
        description="Rows of `V_SCORECARD`: per alert type and ISO week, how many ward warnings went out, how "
                    "many came true, average warning time and Brier skill. Unauthenticated.",
        responses={200: doc.ScorecardList},
        examples=[doc.example("graded week", {"items": [{
            "alert_type": "A2", "period": "2026-10-12", "alerts_sent": 4, "hits": 3, "false_alarms": 1,
            "hit_rate": 0.75, "false_alarm_rate": 0.25, "avg_lead_days": 1.3, "brier_skill": 0.21}]}, response=True)],
    )
    def get(self, request):
        return Response({"items": scorecard()})


class PublicCalibrationView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=["Public scorecard"], auth=doc.NO_AUTH, operation_id="public_calibration", summary="Calibration curve",
        description="Rows of `V_CALIBRATION`: predicted probability vs observed frequency per 0.1 bin.",
        responses={200: doc.CalibrationList},
    )
    def get(self, request):
        return Response({"items": calibration()})


# ----- alert approval (additive; mirrors the APEX Alert Approvals page) ---------------------------


ALERT_EXAMPLE = {
    "alert_id": 42, "score_id": 1301, "alert_type_id": "A1", "ward_id": 12, "ward_name": "Moiben",
    "county": "Uasin Gishu", "hazard": "dry_window", "severity": "warning", "lang": "sw",
    "body": "MvuaSmart: Siku 3 za jua zinatarajiwa kuanzia Alhamisi katika kata yako (uhakika 75%). "
            "Vuna mahindi sasa na uyakaushe. Jibu NDIYO ukivuna.",
    "status": "PENDING_APPROVAL", "probability": 0.75, "confidence_pct": 75, "score_date": "2026-10-15",
    "created_at": "2026-10-15T06:00:04+03:00", "approved_by": None, "approved_at": None, "sent_at": None,
    "messages": {},
}


class AlertsView(APIView):
    @extend_schema(
        tags=["Alerts"], auth=doc.API_AUTH, operation_id="alerts_list", summary="List alerts",
        description="One row per (score, language). `messages` counts the subscriber SMS by status.",
        parameters=[
            OpenApiParameter("status", str, enum=["PENDING_APPROVAL", "APPROVED", "REJECTED", "SENT", "SUPPRESSED"]),
            OpenApiParameter("date", str, description="Score date `YYYY-MM-DD`"),
            OpenApiParameter("ward_id", int), OpenApiParameter("lang", str, enum=["sw", "en"]),
            doc.LIMIT_PARAM,
        ],
        responses={200: AlertList, **doc.errors(400, 401)},
        examples=[doc.example("pending", {"items": [ALERT_EXAMPLE]}, response=True)],
    )
    def get(self, request):
        alerts = Alert.objects.select_related("ward", "score").order_by("-created_at")
        status_filter = request.query_params.get("status")
        if status_filter:
            alerts = alerts.filter(status=status_filter.upper())
        day = _query_date(request, "date")
        if day:
            alerts = alerts.filter(score__score_date=day)
        ward_id = _query_int(request, "ward_id")
        if ward_id:
            alerts = alerts.filter(ward_id=ward_id)
        lang = request.query_params.get("lang")
        if lang:
            alerts = alerts.filter(lang=lang.lower())
        limit = _query_int(request, "limit", 200, maximum=1000)
        return Response({"items": AlertOut(alerts[:limit], many=True).data})


class AlertApproveView(APIView):
    @extend_schema(
        tags=["Alerts"], auth=doc.API_AUTH, operation_id="alert_approve", summary="Approve an alert (sends it)",
        description="`PENDING_APPROVAL` → `APPROVED`, then queues one SMS per eligible subscriber in that language "
                    "(per-subscriber 48 h cooldown and 3/week routine cap; floods and disease skip the cap). The "
                    "alert becomes `SENT` once the first SMS is handed to Africa's Talking, or `SUPPRESSED` when nobody is "
                    "eligible. Replay alerts and alerts older than 48 h are approved but **not** sent "
                    "(`dispatch.skipped`).",
        request=ApproveIn,
        responses={200: doc.AlertApproved, **doc.errors(400, 401, 404, 409)},
        examples=[doc.example("officer", {"approved_by": "agri.officer.uasingishu"}, request=True),
                  doc.example("approved", {"alert_id": 42, "status": "SENT",
                                           "dispatch": {"messages_queued": 3, "suppressed_cooldown": 0,
                                                        "suppressed_cap": 0}}, response=True)],
    )
    def post(self, request, alert_id):
        body = ApproveIn(data=request.data)
        body.is_valid(raise_exception=True)
        alert, result = engine.approve(alert_id, body.validated_data["approved_by"])
        return Response({"alert_id": alert.alert_id, "status": alert.status, "dispatch": result})


class AlertRejectView(APIView):
    @extend_schema(
        tags=["Alerts"], auth=doc.API_AUTH, operation_id="alert_reject", summary="Reject an alert",
        description="`PENDING_APPROVAL` → `REJECTED`. Nothing is sent. Rejected alerts do not block a later draft.",
        request=ApproveIn,
        responses={200: doc.AlertRejected, **doc.errors(400, 401, 404, 409)},
        examples=[doc.example("officer", {"approved_by": "agri.officer.uasingishu"}, request=True)],
    )
    def post(self, request, alert_id):
        body = ApproveIn(data=request.data)
        body.is_valid(raise_exception=True)
        alert = engine.reject(alert_id, body.validated_data["approved_by"])
        return Response({"alert_id": alert.alert_id, "status": alert.status})
