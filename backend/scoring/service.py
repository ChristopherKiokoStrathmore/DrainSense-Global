"""Load leakage-safe inputs, run the heuristic rules, upsert RISK_SCORES (POST /jobs/score, /jobs/replay)."""
import datetime as dt
import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from ngao.models import AlertType, Forecast, RiskScore, Ward, WardFeatureDaily
from ngao_core.errors import InvalidInput
from scoring.heuristics import RULES, WardInputs, decide

logger = logging.getLogger(__name__)

HISTORY_DAYS = 180

# Replay scenarios for POST /jobs/replay.
REPLAYS = {
    "garissa_rvf_2006": {
        "county": "Garissa",
        "alert_types": ["B3", "C2", "B1"],
        "from": dt.date(2006, 9, 1),
        "to": dt.date(2007, 1, 15),
        "step_days": 7,
        # CDC MMWR 2007;56(04):73-76: index human case symptom onset.
        "index_case_date": dt.date(2006, 11, 30),
    },
}


def _wards(county):
    wards = Ward.objects.all()
    if county:
        wards = wards.filter(county__iexact=county)
    return list(wards)


def load_inputs(wards, as_of):
    """Features with feature_date <= as_of AND available_date <= as_of (never peek ahead), and
    each ward's latest forecast issue that was available on as_of."""
    ward_ids = [w.ward_id for w in wards]
    features = defaultdict(list)
    rows = (
        WardFeatureDaily.objects.filter(
            ward_id__in=ward_ids,
            feature_date__lte=as_of,
            feature_date__gte=as_of - dt.timedelta(days=HISTORY_DAYS),
            available_date__lte=as_of,
        )
        .order_by("ward_id", "feature_date")
    )
    for row in rows:
        features[row.ward_id].append(row)

    forecasts = defaultdict(lambda: defaultdict(dict))
    latest_issue = {}
    fc_rows = Forecast.objects.filter(
        ward_id__in=ward_ids,
        source__in=["GFS", "ECMWF"],
        issue_date__lte=as_of,
        issue_date__gte=as_of - dt.timedelta(days=3),
        available_date__lte=as_of,
    ).order_by("ward_id", "source", "-issue_date", "lead_day")
    for fc in fc_rows:
        key = (fc.ward_id, fc.source)
        latest_issue.setdefault(key, fc.issue_date)
        if fc.issue_date != latest_issue[key] or fc.rain_mm is None:
            continue
        # lead_day counts from the issue date; re-base it on as_of.
        lead = fc.lead_day - (as_of - fc.issue_date).days
        if lead >= 1:
            forecasts[fc.ward_id][fc.source][lead] = float(fc.rain_mm)

    return {
        w.ward_id: WardInputs(as_of=as_of, features=features[w.ward_id],
                              forecasts={k: dict(v) for k, v in forecasts[w.ward_id].items()})
        for w in wards
    }


def _alert_types(codes):
    types = {t.alert_type: t for t in AlertType.objects.filter(alert_type__in=list(RULES))}
    if codes:
        unknown = [c for c in codes if c not in RULES]
        if unknown:
            raise InvalidInput(f"no scoring rule for alert types {unknown}", field="alert_types")
        missing = [c for c in codes if c not in types]
        if missing:
            raise InvalidInput(f"alert types {missing} are not in ALERT_TYPES; run seed_reference",
                               field="alert_types")
        return [types[c] for c in codes]
    return [types[c] for c in RULES if c in types]


def run_scoring(as_of, county=None, alert_types=None, overwrite=False, is_replay=False, dry_run=False):
    model_version = settings.NGAO_SCORER_MODEL_VERSION
    wards = _wards(county)
    if county and not wards:
        raise InvalidInput(f"no wards in county '{county}'", field="county")
    types = _alert_types(alert_types)
    inputs = load_inputs(wards, as_of)
    # NULL is_replay is not the same key as 'N' on Oracle, and the views filter on 'N' or 'Y'.
    # Treat a NULL flag as non-replay so a rescore updates that row instead of inserting another.
    replay_key = Q(is_replay=True) if is_replay else (Q(is_replay=False) | Q(is_replay__isnull=True))
    existing = {}
    for row in RiskScore.objects.filter(
        ward__in=wards, score_date=as_of, alert_type__in=types,
    ).filter(replay_key):
        key = (row.ward_id, row.alert_type_id)
        previous = existing.get(key)
        if previous is None or (previous.is_replay is None and row.is_replay is not None):
            existing[key] = row

    summary = {"as_of": as_of.isoformat(), "county": county, "model_version": model_version,
               "wards": len(wards), "scores_written": 0, "act": 0, "watch": 0, "abstain": 0,
               "below_watch": 0, "no_data": 0, "kept_existing": 0, "act_wards": []}
    with transaction.atomic():
        for ward in wards:
            ward_inputs = inputs[ward.ward_id]
            for alert_type in types:
                signal = RULES[alert_type.alert_type](ward_inputs)
                if signal.no_data:
                    summary["no_data"] += 1
                    continue
                decision = decide(signal, alert_type.act_threshold, alert_type.watch_threshold)
                current = existing.get((ward.ward_id, alert_type.alert_type))
                if current and current.model_version != model_version and not overwrite:
                    if current.is_replay is None and not dry_run:
                        current.is_replay = bool(is_replay)
                        current.save(update_fields=["is_replay"])
                    summary["kept_existing"] += 1  # e.g. Trevor's trained model or Chris's seed rows
                    continue
                if decision is None:
                    summary["below_watch"] += 1
                    referenced = current and (current.alert_set.exists() or current.outcome_set.exists())
                    if current and not dry_run and not referenced:
                        current.delete()  # our own earlier row is no longer a warning
                    continue
                summary[decision.lower()] += 1
                if decision == "ACT":
                    summary["act_wards"].append(f"{alert_type.alert_type}:{ward.ward_name}")
                if dry_run:
                    continue
                p = signal.probability
                values = {
                    "raw_score": signal.raw_score,
                    "probability": round(p, 4) if p is not None else None,
                    "confidence_pct": round(p * 100) if p is not None else None,
                    "decision": decision,
                    "abstain_reason": (signal.abstain_reason or "")[:200] or None,
                    "model_version": model_version,
                }
                if current is None:
                    current = RiskScore(
                        ward=ward, score_date=as_of, alert_type=alert_type, is_replay=bool(is_replay),
                    )
                for field, value in values.items():
                    setattr(current, field, value)
                if current.is_replay is None:
                    current.is_replay = bool(is_replay)
                current.save()
                existing[(ward.ward_id, alert_type.alert_type)] = current
                summary["scores_written"] += 1
    logger.info("scoring %s %s: %s", as_of, county or "all", summary)
    return summary


def slot_hints(score):
    """Template hints for one stored score (A1 {day}: the first day of the dry window)."""
    if score.alert_type_id != "A1":
        return {}
    inputs = load_inputs([score.ward], score.score_date)[score.ward_id]
    signal = RULES["A1"](inputs)
    return {"day": score.score_date + dt.timedelta(days=signal.hints.get("day_offset", 1))}


def run_replay(scenario, date_from=None, date_to=None, overwrite=False):
    spec = REPLAYS.get(scenario)
    if spec is None:
        raise InvalidInput(f"unknown scenario '{scenario}'; known: {sorted(REPLAYS)}", field="scenario")
    start = date_from or spec["from"]
    end = date_to or spec["to"]
    if end < start:
        raise InvalidInput("'to' is before 'from'", field="to")
    day, weeks, written, kept = start, 0, 0, 0
    first_act = None
    while day <= end:
        result = run_scoring(day, county=spec["county"], alert_types=spec["alert_types"],
                             overwrite=overwrite, is_replay=True)
        weeks += 1
        written += result["scores_written"]
        kept += result["kept_existing"]
        if first_act is None and result["act"]:
            first_act = day
        day += dt.timedelta(days=spec["step_days"])

    # The first ACT across stored replay rows (ours or another model's).
    first_stored = (
        RiskScore.objects.filter(is_replay=True, decision="ACT", ward__county__iexact=spec["county"],
                                 alert_type__in=spec["alert_types"], score_date__range=(start, end))
        .order_by("score_date").values_list("score_date", flat=True).first()
    )
    first_act = first_act or first_stored
    index_case = spec["index_case_date"]
    return {
        "scenario": scenario, "county": spec["county"], "from": start.isoformat(), "to": end.isoformat(),
        "weeks": weeks, "scores_written": written, "kept_existing": kept,
        "first_act_date": first_act.isoformat() if first_act else None,
        "index_case_date": index_case.isoformat(),
        "lead_days_before_index_case": (index_case - first_act).days if first_act else None,
    }
