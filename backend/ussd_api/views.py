"""USSD to backend contract (API_CONTRACT §5), called by Louisa's ussd/ service.

Wasiliana waits ~10 s for the USSD screen, so these endpoints only touch the database (target
under 2 s): no GenAI, and SMS (the welcome message) is queued and sent in the background.
Auth: Authorization: Bearer <USSD_SERVICE_TOKEN>.
"""
from django.conf import settings
from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from alerts.catalogue import WARD_STATUS_LINES, WARD_STATUS_PHRASES
from alerts.templates import fill, render_system
from api import openapi as doc
from ngao.models import Farm, Herd, Message, Report, RiskScore, Subscriber, Ward
from ngao_core.auth import IsUssdService
from ngao_core.errors import InvalidInput, NotFoundError
from ngao_core.phones import InvalidPhone, normalize_phone
from ngao_core.timeutil import today_eat
from reply_reader.schema import REPORT_TYPES, SPECIES
from reply_reader.service import create_report
from sms.dispatcher import kick, queue_sms

USSD_REPORT_TYPES = [t for t in REPORT_TYPES if t not in ("other", "stop", "booking_confirm")]
ROLE_SERVICES = {"farmer": ["FARM"], "herder": ["HERD"], "both": ["FARM", "HERD"], "health_promoter": ["HEALTH"]}


class PhoneField(serializers.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("help_text", "Kenyan mobile: +2547XXXXXXXX, 2547XXXXXXXX or 07XXXXXXXX (stored as E.164)")
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        try:
            return normalize_phone(super().to_internal_value(data))
        except InvalidPhone as exc:
            raise serializers.ValidationError(str(exc)) from exc


class LanguageIn(serializers.Serializer):
    phone = PhoneField()
    preferred_lang = serializers.ChoiceField(choices=["sw", "en"])


class RegisterIn(serializers.Serializer):
    phone = PhoneField()
    ward_id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=Subscriber.ROLES)
    preferred_lang = serializers.ChoiceField(choices=["sw", "en"], required=False,
                                             help_text="Default: the language saved by /ussd/language, else sw")
    consent = serializers.BooleanField(help_text="Must be true (Data Protection Act consent at registration)")
    # optional detail (FARMS / HERDS)
    crop = serializers.CharField(max_length=30, required=False)
    est_bags = serializers.FloatField(required=False, min_value=0)
    species = serializers.ChoiceField(choices=SPECIES, required=False)
    head_count = serializers.FloatField(required=False, min_value=0)


class ReportIn(serializers.Serializer):
    phone = PhoneField()
    session_id = serializers.CharField(max_length=100, required=False, allow_blank=True,
                                       help_text="Wasiliana USSD session id (stored in the report payload)")
    report_type = serializers.ChoiceField(choices=USSD_REPORT_TYPES)
    count = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=100000)
    unit = serializers.ChoiceField(choices=["bags", "animals"], required=False, allow_null=True)
    species = serializers.ChoiceField(choices=SPECIES, required=False, allow_null=True)
    ward_id = serializers.IntegerField(required=False, help_text="Default: the subscriber's registered ward")


class PhoneIn(serializers.Serializer):
    phone = PhoneField()


def _valid(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class LanguageView(APIView):
    """POST /ussd/language: save the choice before registration finishes (new phone -> new row)."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_language", summary="Save the language choice",
        description="First screen of a new number's first session (`CON Chagua lugha / Choose language`), and the "
                    "main-menu **Lugha / Language** item. Creates the subscriber row when the phone is new "
                    "(phone + preferred_lang + ACTIVE); registration fills ward, role and consent later.",
        request=LanguageIn, responses={200: doc.LanguageSaved, **doc.errors(400, 401, 403)},
        examples=[doc.example("Kiswahili", {"phone": "+254712345678", "preferred_lang": "sw"}, request=True),
                  doc.example("English", {"phone": "0712345678", "preferred_lang": "en"}, request=True),
                  doc.example("saved", {"preferred_lang": "sw", "subscriber_id": 44}, response=True)],
    )
    def post(self, request):
        data = _valid(LanguageIn, request.data)
        lang = data["preferred_lang"]
        with transaction.atomic():
            subscriber, created = Subscriber.objects.get_or_create(
                phone=data["phone"],
                defaults={"preferred_lang": lang, "language": lang, "status": Subscriber.ACTIVE,
                          "is_demo": data["phone"] in settings.NGAO_DEMO_PHONES},
            )
            if not created:
                subscriber.set_language(lang)
                subscriber.save(update_fields=["preferred_lang", "language"])
        return Response({"preferred_lang": lang, "subscriber_id": subscriber.subscriber_id})


class RegisterView(APIView):
    """POST /ussd/register: MERGE SUBSCRIBERS (phone, ward, role, preferred_lang, consent_at)."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_register", summary="Register (menu 1)",
        description="MERGE into `SUBSCRIBERS`: ward, role, preferred_lang, consent_at, status ACTIVE (a STOPPED "
                    "subscriber who registers again is re-activated). Optional farm/herd detail creates `FARMS` / "
                    "`HERDS` rows. A welcome SMS in the chosen language is queued and sent in the background, so "
                    "this answers in well under 2 s.",
        request=RegisterIn, responses={201: doc.Registered, **doc.errors(400, 401, 403)},
        examples=[
            doc.example("contract", {"phone": "+254712345678", "ward_id": 12, "role": "farmer",
                                     "preferred_lang": "sw", "consent": True}, request=True),
            doc.example("herder with herd", {"phone": "+254722000111", "ward_id": 41, "role": "herder",
                                             "preferred_lang": "sw", "consent": True, "species": "cattle",
                                             "head_count": 30}, request=True),
            doc.example("registered", {"subscriber_id": 44, "status": "ACTIVE"}, response=True, status="201"),
        ],
    )
    def post(self, request):
        data = _valid(RegisterIn, request.data)
        if not data["consent"]:
            raise InvalidInput("consent is required to register", field="consent")
        ward = Ward.objects.filter(pk=data["ward_id"]).first()
        if ward is None:
            raise InvalidInput(f"ward_id {data['ward_id']} does not exist", field="ward_id")
        with transaction.atomic():
            subscriber = Subscriber.objects.filter(phone=data["phone"]).first()
            was_active = bool(subscriber and subscriber.consent_at and subscriber.status == Subscriber.ACTIVE
                              and subscriber.ward_id == ward.ward_id)
            subscriber = subscriber or Subscriber(phone=data["phone"])
            lang = data.get("preferred_lang") or subscriber.preferred_lang or "sw"
            subscriber.ward = ward
            subscriber.role = data["role"]
            subscriber.set_language(lang)
            subscriber.consent_at = subscriber.consent_at or timezone.now()
            subscriber.status = Subscriber.ACTIVE
            if data["phone"] in settings.NGAO_DEMO_PHONES:
                subscriber.is_demo = True
            subscriber.save()
            if data["role"] in ("farmer", "both") and ("crop" in data or "est_bags" in data):
                Farm.objects.create(subscriber=subscriber, ward=ward, crop=data.get("crop", "maize"),
                                    est_bags=data.get("est_bags"))
            if data["role"] in ("herder", "both") and "species" in data:
                head = data.get("head_count")
                Herd.objects.create(subscriber=subscriber, ward=ward, species=data["species"], head_count=head,
                                    tlu=round(head * Herd.TLU_PER_HEAD[data["species"]], 2) if head else None)
            if settings.NGAO_SEND_WELCOME_SMS and not was_active:
                queue_sms(subscriber.phone, render_system("welcome", lang, ward=ward.ward_name),
                          subscriber=subscriber)
                kick()
        return Response({"subscriber_id": subscriber.subscriber_id, "status": subscriber.status},
                        status=status.HTTP_201_CREATED)


def _default_unit(report_type, count):
    if count is None:
        return None
    if report_type in ("wet_bags", "bags_dried", "harvested"):
        return "bags"
    if report_type in ("sick_animals", "dead_animals", "rvf_signs"):
        return "animals"
    return None


class ReportView(APIView):
    """POST /ussd/report: structured menu-2 report -> REPORTS(source=USSD, AUTO_ACCEPTED)."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_report", summary="Structured report (menu 2)",
        description="INSERT `REPORTS` with `source=USSD`, `review_status=AUTO_ACCEPTED` (a menu, not an AI parse). "
                    "A USSD-channel `MESSAGES` row records who reported, so the dryer router can book that farmer. "
                    "`other`, `stop` and `booking_confirm` are not menu reports (`400`).",
        request=ReportIn, responses={201: doc.ReportCreated, **doc.errors(400, 401, 403)},
        examples=[
            doc.example("wet bags", {"phone": "+254712345678", "session_id": "s1", "report_type": "wet_bags",
                                     "count": 20, "unit": "bags"}, request=True),
            doc.example("sick animals", {"phone": "+254722000111", "session_id": "s7", "report_type": "sick_animals",
                                         "count": 3, "unit": "animals", "species": "goats"}, request=True),
            doc.example("flooding", {"phone": "+254712345678", "report_type": "flooding"}, request=True),
            doc.example("created", {"report_id": 77}, response=True, status="201"),
        ],
    )
    def post(self, request):
        data = _valid(ReportIn, request.data)
        subscriber = Subscriber.objects.select_related("ward").filter(phone=data["phone"]).first()
        ward = subscriber.ward if subscriber else None
        if data.get("ward_id") is not None:
            ward = Ward.objects.filter(pk=data["ward_id"]).first()
            if ward is None:
                raise InvalidInput(f"ward_id {data['ward_id']} does not exist", field="ward_id")
        count = data.get("count")
        unit = data.get("unit") or _default_unit(data["report_type"], count)
        with transaction.atomic():
            # A USSD-channel IN message keeps who reported (the router books that farmer).
            summary = f"USSD report: {data['report_type']}" + (f" {count:g} {unit or ''}".rstrip() if count is not None else "")
            message = Message.objects.create(direction=Message.IN, channel=Message.USSD, phone=data["phone"],
                                             subscriber=subscriber, body=summary, status=Message.RECEIVED)
            report = create_report(
                report_type=data["report_type"], count=count, unit=unit, ward=ward, message=message,
                payload={"report_type": data["report_type"], "count": count, "unit": unit,
                         "species": data.get("species"), "session_id": data.get("session_id") or None},
                ai_confidence=None, source=Report.SOURCE_USSD, review_status=Report.AUTO_ACCEPTED,
            )
        return Response({"report_id": report.report_id}, status=status.HTTP_201_CREATED)


def ward_status_text(subscriber, day=None):
    day = day or today_eat()
    lang = subscriber.spoken_lang() if subscriber else "sw"
    if subscriber is None or subscriber.ward_id is None:
        return fill(WARD_STATUS_LINES["UNREGISTERED"][lang]), None
    ward = subscriber.ward
    scores = RiskScore.objects.filter(ward=ward, score_date=day, is_replay=False).select_related("alert_type")
    services = ROLE_SERVICES.get(subscriber.role)
    if services:
        scores = scores.filter(alert_type__service__in=services)
    rank = {"ACT": 0, "WATCH": 1, "ABSTAIN": 2}
    ordered = sorted(scores, key=lambda s: (rank[s.decision], -(s.probability or 0)))
    if not ordered:
        return fill(WARD_STATUS_LINES["NONE"][lang], ward=ward.ward_name), None
    top = ordered[0]
    phrase = WARD_STATUS_PHRASES.get(top.alert_type_id, {}).get(lang, top.alert_type.name)
    confidence = top.confidence_pct if top.confidence_pct is not None else round((top.probability or 0) * 100)
    text = fill(WARD_STATUS_LINES[top.decision][lang], ward=ward.ward_name, phrase=phrase, confidence=confidence)
    return text, top


class WardStatusView(APIView):
    """GET /ussd/ward-status?phone=: one short line for menu 3 in the subscriber's language."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_ward_status", summary="Ward status line (menu 3)",
        description="Today's most important decision for the subscriber's ward (ACT before WATCH before ABSTAIN), "
                    "limited to the services of their role, as one line in their language. Always `200`: "
                    "unregistered phones get a 'please register' line with `registered: false`.",
        parameters=[OpenApiParameter("phone", str, required=True, description="URL-encode the `+` as `%2B`",
                                     examples=[doc.example("E.164", "+254712345678")])],
        responses={200: doc.WardStatusLine, **doc.errors(400, 401, 403)},
        examples=[
            doc.example("Kiswahili ACT", {"text": "Kata ya Moiben: siku za jua, uhakika 75%.", "registered": True,
                                          "ward_id": 12, "decision": "ACT", "alert_type": "A1"}, response=True),
            doc.example("English, nothing today", {"text": "Kimumu ward: no warnings today.", "registered": True,
                                                   "ward_id": 15, "decision": None, "alert_type": None}, response=True),
            doc.example("unregistered", {"text": "Hujajiandikisha. Chagua 1 kujiandikisha.", "registered": False,
                                         "ward_id": None, "decision": None, "alert_type": None}, response=True),
        ],
    )
    def get(self, request):
        data = _valid(PhoneIn, request.query_params)
        subscriber = Subscriber.objects.select_related("ward").filter(phone=data["phone"]).first()
        text, top = ward_status_text(subscriber)
        return Response({
            "text": text,
            "registered": bool(subscriber and subscriber.ward_id),
            "ward_id": subscriber.ward_id if subscriber else None,
            "decision": top.decision if top else None,
            "alert_type": top.alert_type_id if top else None,
        })


class UnsubscribeView(APIView):
    """POST /ussd/unsubscribe: SUBSCRIBERS.status = STOPPED."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_unsubscribe", summary="Unsubscribe",
        description="`SUBSCRIBERS.status = STOPPED`; no further alerts. `404` for an unknown phone.",
        request=PhoneIn, responses={200: doc.Unsubscribed, **doc.errors(400, 401, 403, 404)},
        examples=[doc.example("contract", {"phone": "+254712345678"}, request=True),
                  doc.example("stopped", {"status": "STOPPED"}, response=True)],
    )
    def post(self, request):
        data = _valid(PhoneIn, request.data)
        updated = Subscriber.objects.filter(phone=data["phone"]).update(status=Subscriber.STOPPED)
        if not updated:
            raise NotFoundError(f"no subscriber with phone {data['phone']}", {"field": "phone"})
        return Response({"status": Subscriber.STOPPED})
