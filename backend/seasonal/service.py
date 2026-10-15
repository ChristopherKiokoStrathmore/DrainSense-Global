"""Issue the October-December outlook per ward (POST /jobs/seasonal-outlook, the scheduler in October).

  ERSST Aug-Sep (+ ERA5 Jul-Sep when configured) -> indices -> v3 logistic model per region
    -> SEASONAL_OUTLOOK (every ward; APEX reads V_SEASONAL_OUTLOOK_LATEST)
    -> RISK_SCORES alert type S1 for wards at WATCH or ACT, so the normal alert flow applies:
       send-alerts drafts ACT alerts, an officer approves in APEX, the scheduler sends the SMS
    -> SEASONAL_MODEL_RUNS / SEASONAL_BACKTEST (the model card, written once per model version)

The probability is P(OND rain > 125% of the ward's 1991-2020 mean). Features are year-level, so
every ward in one of the six regions gets the same probability. This is decision support next to
the official KMD / ICPAC outlook, not a replacement (ngao_seasonal_model_onepager.pdf).
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ngao.models import AlertType, RiskScore, SeasonalBacktest, SeasonalModelRun, SeasonalOutlook, Ward
from ngao_core.errors import InvalidInput
from ngao_core.timeutil import today_eat
from seasonal.indices import derive
from seasonal.model import RegionLocator, load_artifact, predict
from seasonal.sources import SourceUnavailable, atmos_box_means, era5_configured, sst_box_means

logger = logging.getLogger(__name__)

ALERT_TYPE = "S1"
HIGH_PROBABILITY = 0.5
SOURCES = ("auto", "sst", "era5")


def default_year(day=None):
    """The latest short-rains season whose inputs can exist: this year from October, else last year."""
    day = day or today_eat()
    return day.year if day.month >= 10 else day.year - 1


def season_label(year):
    return f"OND {year}"


def category(probability, threshold):
    if probability >= HIGH_PROBABILITY:
        return SeasonalOutlook.HIGH
    return SeasonalOutlook.MEDIUM if probability >= threshold else SeasonalOutlook.LOW


def _features(artifact, variant_key, year):
    variant = artifact["variants"][variant_key]
    raw = sst_box_means(year)
    if "era5" in variant["needs"]:
        raw.update(atmos_box_means(year, artifact["references"]))
    features = derive(raw)
    missing = [name for name in variant["features"] if name not in features]
    if missing:
        raise SourceUnavailable(f"missing features {missing}")
    return {name: round(features[name], 4) for name in variant["features"]}


def compute(year, source="auto"):
    """(variant key, features, notes) for a season, falling back from ERA5 to SST-only when allowed."""
    if source not in SOURCES:
        raise InvalidInput(f"source must be one of {', '.join(SOURCES)}", field="source")
    artifact = load_artifact()
    if source == "era5" and not era5_configured():
        raise InvalidInput("source=era5 needs CDSAPI_KEY and the cdsapi package", field="source")
    variant_key = "lit6" if source == "era5" or (source == "auto" and era5_configured()) else "sst4"
    notes = []
    try:
        return variant_key, _features(artifact, variant_key, year), notes
    except SourceUnavailable as exc:
        if source != "auto" or variant_key == "sst4":
            raise
        notes.append(f"ERA5 unavailable ({exc}); SST-only variant used")
        return "sst4", _features(artifact, "sst4", year), notes


def sync_model_card(artifact=None):
    """SEASONAL_MODEL_RUNS + SEASONAL_BACKTEST rows for each variant and target, once per version."""
    artifact = artifact or load_artifact()
    created = 0
    for variant in artifact["variants"].values():
        for target, spec in variant["targets"].items():
            if SeasonalModelRun.objects.filter(model_version=variant["model_version"], target=target).exists():
                continue
            m = spec["metrics"]
            run = SeasonalModelRun.objects.create(
                model_version=variant["model_version"], role=variant["role"], target=target,
                target_desc=spec["target_desc"], season="OND", issue_point=artifact["issue_point"],
                train_period=spec["train_period"], test_period=spec["test_period"],
                features=", ".join(variant["features"]) + " + 6 region dummies",
                algorithm=f"Logistic regression (standardised, C={spec['C']})",
                n_test_ward_seasons=m.get("n_test"), n_effective_seasons=m.get("n_test_years"),
                accuracy=m.get("accuracy"), precision_score=m.get("precision"), recall_score=m.get("recall"),
                f1=m.get("f1"), auc=m.get("auc"), auc_ci90_lo=m.get("auc_ci90", [None, None])[0],
                auc_ci90_hi=m.get("auc_ci90", [None, None])[1], brier=m.get("brier"),
                decision_threshold=spec["threshold"], cv_auc=m.get("cv_auc"),
                loyo_f1_min=m.get("loyo_f1", [None, None])[0], loyo_f1_max=m.get("loyo_f1", [None, None])[1],
                year_corr=m.get("year_corr"), caveats=artifact["caveats"],
                source_file="backend/seasonal/model/ngao_seasonal_v3.json",
            )
            SeasonalBacktest.objects.bulk_create([
                SeasonalBacktest(run=run, test_year=int(year), observed_wet_share=observed, mean_pred_prob=predicted)
                for year, (observed, predicted) in sorted(spec["backtest"].items())
            ])
            created += 1
    return created


def _write_s1(ward, day, probability, version, act, watch):
    decision = RiskScore.ACT if probability >= act else RiskScore.WATCH if probability >= watch else None
    existing = RiskScore.matching(ward_id=ward.ward_id, score_date=day, alert_type_id=ALERT_TYPE)
    if decision is None:
        if existing and existing.model_version == version:
            existing.delete()
        return None
    values = dict(probability=round(probability, 4), confidence_pct=round(probability * 100),
                  decision=decision, model_version=version, abstain_reason=None)
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        existing.save()
    else:
        RiskScore.objects.create(ward=ward, score_date=day, alert_type_id=ALERT_TYPE, is_replay=False, **values)
    return decision


def issue_outlook(year=None, source=None, dry_run=False):
    year = year or default_year()
    source = source or settings.NGAO_SEASONAL_FEATURE_SOURCE
    season = season_label(year)
    artifact = load_artifact()
    summary = {"season": season, "dry_run": dry_run, "source": source}
    try:
        variant_key, features, notes = compute(year, source)
    except SourceUnavailable as exc:
        return {**summary, "status": "waiting_for_data", "reason": str(exc)}

    variant = artifact["variants"][variant_key]
    y25, yter = variant["targets"]["y25"], variant["targets"].get("yter")
    locate = RegionLocator(artifact["regions"])
    # Only the current season becomes S1 alerts; re-issuing a past season (a check) must not alert anyone.
    current = year == default_year()
    s1 = AlertType.objects.filter(pk=ALERT_TYPE).first() if current else None
    act = s1.act_threshold if s1 and s1.act_threshold is not None else HIGH_PROBABILITY
    watch = s1.watch_threshold if s1 and s1.watch_threshold is not None else y25["threshold"]
    inputs = "ERSST Aug-Sep" + (" + ERA5 Jul-Sep" if variant_key == "lit6" else "")
    counts = {SeasonalOutlook.HIGH: 0, SeasonalOutlook.MEDIUM: 0, SeasonalOutlook.LOW: 0}
    s1_counts = {RiskScore.ACT: 0, RiskScore.WATCH: 0}
    by_region = {}
    day, now = today_eat(), timezone.now()

    wards = list(Ward.objects.exclude(centroid_lat=None).exclude(centroid_lon=None))
    with transaction.atomic():
        if not dry_run:
            summary["model_card_rows"] = sync_model_card(artifact)
        for ward in wards:
            region = locate(ward.centroid_lat, ward.centroid_lon)
            p = predict(y25, features, region, locate.count)
            p_ter = predict(yter, features, region, locate.count) if yter else None
            level = category(p, y25["threshold"])
            counts[level] += 1
            by_region[region] = round(p, 3)
            if dry_run:
                continue
            note = f"P(OND >125% of normal)={p:.2f}" + (f"; P(above-normal tercile)={p_ter:.2f}" if p_ter is not None else "")
            note += f"; region {region}; {inputs} {year}" + ("; " + "; ".join(notes) if notes else "")
            SeasonalOutlook.objects.update_or_create(
                ward=ward, season=season, model_version=variant["model_version"],
                defaults=dict(probability=round(p, 4), category=level, confidence=round(p * 100, 1),
                              issued_at=now, notes=note[:400]),
            )
            if s1 is not None:
                decision = _write_s1(ward, day, p, variant["model_version"], act, watch)
                if decision:
                    s1_counts[decision] += 1

    summary.update({
        "status": "DRY_RUN" if dry_run else "ISSUED",
        "model_version": variant["model_version"],
        "variant": variant_key,
        "features": features,
        "threshold": y25["threshold"],
        "wards": len(wards),
        "high": counts[SeasonalOutlook.HIGH],
        "medium": counts[SeasonalOutlook.MEDIUM],
        "low": counts[SeasonalOutlook.LOW],
        "probability_by_region": {str(k): v for k, v in sorted(by_region.items())},
        "s1_act": s1_counts[RiskScore.ACT],
        "s1_watch": s1_counts[RiskScore.WATCH],
        "s1_alert_type": ("present" if s1 else "missing (seed ALERT_TYPES S1 to alert by SMS)") if current
        else f"not used: OND {year} is not the current season",
        "notes": notes,
    })
    logger.info("seasonal outlook %s: %s", season, {k: summary[k] for k in ("variant", "high", "medium", "low")})
    return summary


def issue_if_due(day=None):
    """Scheduler hook: in October-December, issue this season's outlook once (when inputs exist)."""
    day = day or today_eat()
    if day.month < 10:
        return None
    if SeasonalOutlook.objects.filter(season=season_label(day.year)).exists():
        return None
    return issue_outlook(day.year)


# ----- read side (API, USSD) ---------------------------------------------------------------------------

LINES = {
    "sw": "{ward}: uwezekano wa {pct}% mvua za Okt-Des {year} kuzidi kawaida sana.",
    "en": "{ward}: {pct}% chance Oct-Dec {year} rain is far above normal.",
}
NO_OUTLOOK = {
    "sw": "Utabiri wa msimu wa Okt-Des haujatolewa bado.",
    "en": "The Oct-Dec season outlook is not out yet.",
}


def latest_for_ward(ward_id):
    return (SeasonalOutlook.objects.filter(ward_id=ward_id).select_related("ward")
            .order_by("-issued_at", "-outlook_id").first())


def outlook_line(outlook, lang):
    if outlook is None:
        return NO_OUTLOOK[lang]
    return LINES[lang].format(ward=outlook.ward.ward_name, pct=round(outlook.probability * 100),
                              year=outlook.season.split()[-1])
