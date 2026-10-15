"""Seasonal outlook endpoints: the job, per-ward outlooks, the public model card, the USSD line."""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from api import openapi as doc
from api.views import _job_schema, _JobView
from ngao.models import SeasonalOutlook, Subscriber
from ngao_core.auth import IsUssdService
from ngao_core.errors import InvalidInput
from seasonal.model import ModelUnavailable, load_artifact
from seasonal.service import latest_for_ward, outlook_line


class SeasonalOutlookJobView(_JobView):
    job = "seasonal-outlook"

    @_job_schema(
        "seasonal-outlook", doc.SeasonalSummary, "Issue the Oct-Dec outlook → SEASONAL_OUTLOOK",
        "The v3 El Niño / Indian Ocean model (`docs/SEASONAL_MODEL.md`). Reads August-September SST from NOAA "
        "ERSSTv5 (and July-September wind and pressure from ERA5 when `CDSAPI_KEY` is set), scores every ward and "
        "writes `SEASONAL_OUTLOOK`: P(OND rain > 125% of the 1991-2020 mean), category HIGH (≥ 0.5), MEDIUM "
        "(≥ the model threshold) or LOW. Wards at WATCH/ACT also get an `S1` row in `RISK_SCORES`, so "
        "`send-alerts` drafts SMS alerts that an officer approves in APEX.\n\n"
        "Before the September SST is published (early October) the answer is `waiting_for_data` and nothing "
        "is written. The scheduler issues the season's outlook once, from October.",
        [("this season", {}), ("re-issue 2023 as a check", {"year": 2023, "source": "sst"}),
         ("preview", {"dry_run": True})],
        {"season": "OND 2026", "status": "ISSUED", "dry_run": False, "source": "auto",
         "model_version": "ngao-seasonal-v3-sst4", "variant": "sst4",
         "features": {"wio": 27.41, "eio": 28.12, "dmi": -0.71, "nino34": 26.95}, "threshold": 0.31,
         "wards": 61, "high": 0, "medium": 12, "low": 49, "probability_by_region": {"0": 0.21, "3": 0.34},
         "s1_act": 0, "s1_watch": 12, "s1_alert_type": "present", "model_card_rows": 4, "notes": []},
    )
    def post(self, request):
        return super().post(request)


def _item(o):
    return {"ward_id": o.ward_id, "ward_name": o.ward.ward_name, "county": o.ward.county, "season": o.season,
            "probability": o.probability, "category": o.category, "confidence": o.confidence,
            "model_version": o.model_version, "issued_at": o.issued_at, "notes": o.notes}


class SeasonalOutlookView(APIView):
    @extend_schema(
        tags=["Seasonal outlook"], auth=doc.API_AUTH, operation_id="seasonal_outlook",
        summary="Per-ward outlook (latest issue)",
        description="The newest `SEASONAL_OUTLOOK` row per ward (and per season when `season` is given). "
                    "APEX reads the same data from `V_SEASONAL_OUTLOOK_LATEST`.",
        parameters=[OpenApiParameter("season", str, description="e.g. `OND 2026`. Default: the newest season"),
                    OpenApiParameter("county", str), OpenApiParameter("ward_id", int)],
        responses={200: doc.SeasonalOutlookList, **doc.errors(400, 401)},
    )
    def get(self, request):
        rows = SeasonalOutlook.objects.select_related("ward")
        season = request.query_params.get("season")
        if not season:
            newest = rows.order_by("-issued_at").first()
            season = newest.season if newest else None
        rows = rows.filter(season=season)
        if request.query_params.get("county"):
            rows = rows.filter(ward__county__iexact=request.query_params["county"])
        if request.query_params.get("ward_id"):
            try:
                rows = rows.filter(ward_id=int(request.query_params["ward_id"]))
            except ValueError as exc:
                raise InvalidInput("ward_id must be an integer", field="ward_id") from exc
        latest = {}
        for row in rows.order_by("ward_id", "-issued_at", "-outlook_id"):
            latest.setdefault(row.ward_id, row)
        items = sorted(latest.values(), key=lambda o: (-o.probability, o.ward.county, o.ward.ward_name))
        return Response({"items": [_item(o) for o in items]})


class PublicSeasonalModelCardView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(
        tags=["Seasonal outlook"], auth=doc.NO_AUTH, operation_id="public_seasonal_model_card",
        summary="Seasonal model card (public)",
        description="Honest test scores of each model variant on unseen seasons 2016-2025, with the per-year "
                    "backtest and the caveats. From the model artifact the backend runs.",
        responses={200: doc.SeasonalModelCard},
    )
    def get(self, request):
        try:
            artifact = load_artifact()
        except ModelUnavailable as exc:
            raise InvalidInput(str(exc)) from exc
        items = []
        for variant in artifact["variants"].values():
            for target, spec in variant["targets"].items():
                m = spec["metrics"]
                items.append({
                    "model_version": variant["model_version"], "role": variant["role"], "target": target,
                    "target_desc": spec["target_desc"], "train_period": spec["train_period"],
                    "test_period": spec["test_period"], "features": ", ".join(variant["features"]),
                    "auc": m["auc"], "auc_ci90": m["auc_ci90"], "f1": m["f1"], "brier": m["brier"],
                    "decision_threshold": spec["threshold"], "backtest": spec["backtest"],
                })
        return Response({"items": items, "caveats": artifact["caveats"]})


class UssdSeasonalView(APIView):
    """GET /ussd/seasonal-outlook?phone=: the ward's outlook as one line (USSD menu 6)."""

    permission_classes = [IsUssdService]

    @extend_schema(
        tags=["USSD contract"], auth=doc.USSD_AUTH, operation_id="ussd_seasonal_outlook",
        summary="Season outlook line (menu 6)",
        description="The newest outlook for the subscriber's ward as one short line in their language, shown "
                    "above the El Niño advice topics. Unregistered phones and wards without an outlook get a "
                    "neutral line with `probability: null`. Always `200`.",
        parameters=[OpenApiParameter("phone", str, required=True, description="URL-encode the `+` as `%2B`")],
        responses={200: doc.SeasonalLine, **doc.errors(400, 401, 403)},
        examples=[doc.example("English", {"text": "Kapseret: 34% chance Oct-Dec 2026 rain is far above normal.",
                                          "registered": True, "season": "OND 2026", "probability": 0.34,
                                          "category": "MEDIUM"}, response=True)],
    )
    def get(self, request):
        from ussd_api.views import PhoneIn, _valid

        data = _valid(PhoneIn, request.query_params)
        subscriber = Subscriber.objects.select_related("ward").filter(phone=data["phone"]).first()
        lang = subscriber.spoken_lang() if subscriber else "sw"
        outlook = latest_for_ward(subscriber.ward_id) if subscriber and subscriber.ward_id else None
        return Response({
            "text": outlook_line(outlook, lang),
            "registered": bool(subscriber and subscriber.ward_id),
            "season": outlook.season if outlook else None,
            "probability": outlook.probability if outlook else None,
            "category": outlook.category if outlook else None,
        })
