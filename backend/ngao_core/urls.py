"""URL map. The same routes are served under /ngao/v1/ (the contract's ORDS-style path that
Louisa's USSD service calls) and /api/v1/ (the Django base URL in API_CONTRACT §2)."""
from django.conf import settings
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from api import views as api
from ngao_core.auth import IsApiClient
from sms import views as sms
from ussd_api import views as ussd

v1 = [
    path("health", api.HealthView.as_view()),
    # §2.2 actions
    path("jobs/score", api.ScoreJobView.as_view()),
    path("jobs/send-alerts", api.SendAlertsJobView.as_view()),
    path("jobs/plan-routes", api.PlanRoutesJobView.as_view()),
    path("jobs/replay", api.ReplayJobView.as_view()),
    path("jobs/reset-demo", api.ResetDemoJobView.as_view()),
    path("jobs/dispatch", api.DispatchJobView.as_view()),
    path("jobs/grade-outcomes", api.GradeOutcomesJobView.as_view()),
    path("jobs/<int:job_id>/status", api.JobDetailView.as_view()),
    # §2.3 reads
    path("wards", api.WardsView.as_view()),
    path("wards/<int:ward_id>/risk", api.WardRiskView.as_view()),
    path("reports", api.ReportsView.as_view()),
    path("reports/<int:report_id>", api.ReportDetailView.as_view()),
    path("routes", api.RoutesView.as_view()),
    path("bookings/<int:booking_id>/confirm", api.BookingConfirmView.as_view()),
    path("public/scorecard", api.PublicScorecardView.as_view()),
    path("public/calibration", api.PublicCalibrationView.as_view()),
    # alert approval (additive)
    path("alerts", api.AlertsView.as_view()),
    path("alerts/<int:alert_id>/approve", api.AlertApproveView.as_view()),
    path("alerts/<int:alert_id>/reject", api.AlertRejectView.as_view()),
    # §3 Wasiliana callbacks + demo fixture
    path("sms/delivery", sms.DeliveryReportView.as_view()),
    path("sms/inbound", sms.InboundSmsView.as_view()),
    path("demo/inbound-sms", sms.SimulateInboundView.as_view()),
    # §5 USSD to backend
    path("ussd/language", ussd.LanguageView.as_view()),
    path("ussd/register", ussd.RegisterView.as_view()),
    path("ussd/report", ussd.ReportView.as_view()),
    path("ussd/ward-status", ussd.WardStatusView.as_view()),
    path("ussd/unsubscribe", ussd.UnsubscribeView.as_view()),
]

_docs_permission = [AllowAny] if settings.NGAO_API_DOCS_PUBLIC else [IsApiClient]
docs = [
    # OpenAPI 3 schema (YAML; ?format=json for JSON), Swagger UI and ReDoc. Assets are served locally.
    path("schema", SpectacularAPIView.as_view(permission_classes=_docs_permission), name="openapi-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="openapi-schema", permission_classes=_docs_permission),
         name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="openapi-schema", permission_classes=_docs_permission),
         name="redoc"),
]

urlpatterns = [
    path("", RedirectView.as_view(url="/ngao/v1/docs/", permanent=False)),
    path("ngao/v1/", include(docs)),
    path("ngao/v1/", include(v1)),
    path("api/v1/", include(v1)),
]

handler404 = "ngao_core.errors.json_404"
handler500 = "ngao_core.errors.json_500"
