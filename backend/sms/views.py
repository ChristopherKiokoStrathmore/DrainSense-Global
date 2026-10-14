"""Wasiliana callbacks (API_CONTRACT §3.3, §3.4) and the demo inbound fixture.

Webhooks always answer 200 with an empty body, whatever happens: errors are logged, never
returned to Wasiliana. When WEBHOOK_SECRET is set, the callback URL must carry ?k=<secret>
(or the X-Webhook-Secret header); mismatches are logged and ignored.
"""
import hmac
import logging

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api import openapi as doc
from ngao_core.auth import IsApiClient
from sms import inbound
from sms.dispatcher import apply_delivery_report
from sms.wasiliana import parse_delivery_report, parse_inbound

logger = logging.getLogger("sms.webhooks")


def _secret_ok(request):
    expected = settings.NGAO_WEBHOOK_SECRET
    if not expected:
        return True
    presented = request.query_params.get("k") or request.headers.get("X-Webhook-Secret") or ""
    return hmac.compare_digest(presented.encode(), expected.encode())


def _body(request):
    try:
        data = request.data
    except Exception:  # unparseable body: keep the raw text in the log
        data = {}
    if not data and request.query_params:
        data = request.query_params
    return data


class _Webhook(APIView):
    authentication_classes = []
    permission_classes = []

    def handle_body(self, data):
        raise NotImplementedError

    @extend_schema(exclude=True)  # some providers probe with GET; documented as POST only
    def get(self, request):
        return self.post(request)

    def post(self, request):
        try:
            raw = request.body[:2000]
        except Exception:
            raw = b""
        logger.info("%s raw body: %r", self.__class__.__name__, raw)
        if not _secret_ok(request):
            logger.warning("%s: webhook secret mismatch, ignored", self.__class__.__name__)
            return HttpResponse(status=200)
        try:
            self.handle_body(_body(request))
        except Exception:
            logger.exception("%s failed; answering 200 anyway", self.__class__.__name__)
        return HttpResponse(status=200)


class DeliveryReportView(_Webhook):
    """POST /sms/delivery: SENT -> DELIVERED / FAILED by provider_msg_id."""

    @extend_schema(
        tags=["Wasiliana webhooks"], auth=doc.WEBHOOK_AUTH, operation_id="sms_delivery_report",
        summary="Delivery report callback",
        description="Maps the report onto `MESSAGES.status` (`SENT` → `DELIVERED` / `FAILED`) by `provider_msg_id`. "
                    "JSON or form-encoded. **Always `200`, empty body**: unknown statuses change nothing and every "
                    "raw body is logged. Field names are the pytek-wasiliana reference shape; remap them with "
                    "`WASILIANA_DLR_FIELDS` once confirmed at docs.wasiliana.com.",
        request=doc.DeliveryReportBody, responses={200: OpenApiResponse(description="Empty body, always")},
        examples=[
            doc.example("delivered", {"phone": "254712345678", "correlator": "message_15883801465eacc1f2643a9",
                                      "deliveryStatus": "0", "failure_reason": ""}, request=True),
            doc.example("failed", {"phone": "254712345678", "correlator": "message_15883801465eacc1f2643a9",
                                   "deliveryStatus": "1", "failure_reason": "AbsentSubscriber"}, request=True),
        ],
    )
    def post(self, request):
        return super().post(request)

    def handle_body(self, data):
        report = parse_delivery_report(data)
        updated = apply_delivery_report(report)
        logger.info("delivery report %s status=%s outcome=%s updated=%d",
                    report.provider_msg_id, report.status_raw, report.outcome, updated)


class InboundSmsView(_Webhook):
    """POST /sms/inbound: insert MESSAGES(IN), then keywords / reply reader in the background."""

    @extend_schema(
        tags=["Wasiliana webhooks"], auth=doc.WEBHOOK_AUTH, operation_id="sms_inbound", summary="Inbound SMS callback",
        description="Stores `MESSAGES(direction=IN)` and answers `200` at once; processing runs in the background:\n\n"
                    "1. **Keywords first** (no AI): `STOP`; `LUGHA SW|EN` / `LANG SW|EN`; `1` / `NDIYO` "
                    "(confirm the open dryer booking, else NDIYO = harvested); `RVF [n] [species]`.\n"
                    "2. Otherwise the **reply reader** (OCI GenAI with the §2.4 JSON schema, or the keyword + number "
                    "fallback) writes `REPORTS`: `AUTO_ACCEPTED` when confident (≥ 0.7), else `NEEDS_REVIEW`.\n\n"
                    "Inbound forwarding is still to be confirmed with Wasiliana; remap fields with "
                    "`WASILIANA_INBOUND_FIELDS`. **Always `200`, empty body.**",
        request=doc.InboundSmsBody, responses={200: OpenApiResponse(description="Empty body, always")},
        examples=[
            doc.example("farmer reply (Kiswahili)", {"phone": "254712345678", "linkid": "L-88412",
                        "message": "Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu"}, request=True),
            doc.example("change language", {"phone": "254712345678", "message": "LANG EN"}, request=True),
            doc.example("confirm booking", {"phone": "254712345678", "message": "1"}, request=True),
            doc.example("operator drying report", {"phone": "254700000901", "message": "Dried 120 bags, 13%"},
                        request=True),
        ],
    )
    def post(self, request):
        return super().post(request)

    def handle_body(self, data):
        sms = parse_inbound(data)
        if not sms.phone or sms.text is None:
            logger.warning("inbound SMS without phone/text after mapping: %r", data)
            return
        inbound.receive(sms.phone, sms.text, sms.provider_msg_id)


class SimulatedInboundSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20, help_text="Sender, any Kenyan format")
    text = serializers.CharField(max_length=1000, help_text="The SMS text, exactly as the farmer would send it")


class SimulateInboundView(APIView):
    """POST /demo/inbound-sms {"phone","text"}: the reply path without Wasiliana forwarding
    (EXECUTION_PLAN: "demo the reply with a fixture if the callback is late")."""

    permission_classes = [IsApiClient]

    @extend_schema(
        tags=["Demo"], auth=doc.API_AUTH, operation_id="demo_inbound_sms", summary="Simulate an inbound SMS",
        description="Runs exactly the `/sms/inbound` path (keywords, then the reply reader) **synchronously** and "
                    "returns the resulting report. For the demo, and for testing before Wasiliana forwards replies.",
        request=SimulatedInboundSerializer,
        responses={201: doc.SimulatedInbound, **doc.errors(400, 401, 403)},
        examples=[
            doc.example("demo reply", {"phone": "+254711000001",
                                       "text": "Nimevuna magunia 20, mvua imenyesha, mahindi yana unyevu"}, request=True),
            doc.example("parsed", {"message_id": 311, "report": {"report_id": 78, "report_type": "wet_bags",
                                   "report_count": 20, "review_status": "AUTO_ACCEPTED", "ai_confidence": 0.8,
                                   "source": "SMS_KEYWORD"}}, response=True, status="201"),
        ],
    )
    def post(self, request):
        data = SimulatedInboundSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        message = inbound.receive(data.validated_data["phone"], data.validated_data["text"], process_now=True)
        report = message.report_set.order_by("-report_id").first()
        return Response({
            "message_id": message.message_id,
            "report": None if report is None else {
                "report_id": report.report_id, "report_type": report.report_type,
                "report_count": report.report_count, "review_status": report.review_status,
                "ai_confidence": report.ai_confidence, "source": report.source,
            },
        }, status=201)
