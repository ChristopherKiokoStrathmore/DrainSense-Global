"""Grading and the public scorecard (ARCHITECTURE M9; "a public scorecard grades every alert").

grade_outcomes() checks Act/Watch scores whose verification window has passed against what was
later observed in WARD_FEATURES_DAILY (only rows already available, never revised data from the
future), and writes OUTCOMES. Today that is possible for the rain-based alerts:
  A1 dry window   hit = 3 consecutive days < 2 mm starting on day 1..5
  A2 heavy rain   hit = a day > 20 mm on day 1..2
Other alert types need ground truth from officers (floods, outbreaks) and are not auto-graded.

scorecard() returns the V_SCORECARD columns from API_CONTRACT §1.2. An "alert" is one ward
warning (one ACT score that reached subscribers), however many SMS and languages it used.
"""
import datetime as dt
from collections import defaultdict

from django.db import transaction

from ngao.models import Alert, Outcome, RiskScore, WardFeatureDaily
from ngao_core.timeutil import EAT, today_eat

DRY_MM = 2.0
HEAVY_MM = 20.0
WINDOWS = {"A1": 7, "A2": 2}


def _observed_rain(ward_id, start, end, as_of):
    rows = WardFeatureDaily.objects.filter(
        ward_id=ward_id, feature_date__range=(start, end), available_date__lte=as_of,
        rain_1d_mm__isnull=False,
    ).values_list("feature_date", "rain_1d_mm")
    return {day: float(mm) for day, mm in rows}


def _grade(score, as_of):
    window = WINDOWS[score.alert_type_id]
    start = score.score_date + dt.timedelta(days=1)
    rain = _observed_rain(score.ward_id, start, score.score_date + dt.timedelta(days=window), as_of)
    days = [start + dt.timedelta(days=i) for i in range(window)]
    if any(day not in rain for day in days):
        return None  # not all observations are in yet
    if score.alert_type_id == "A2":
        heavy = [i + 1 for i, day in enumerate(days) if rain[day] > HEAVY_MM]
        return ("heavy_rain", bool(heavy), heavy[0] if heavy else None)
    for offset in range(5):
        if all(rain[days[offset + k]] < DRY_MM for k in range(3)):
            return ("dry_window", True, offset + 1)
    return ("dry_window", False, None)


def grade_outcomes(as_of=None):
    as_of = as_of or today_eat()
    summary = {"as_of": as_of.isoformat(), "graded": 0, "hits": 0, "misses": 0, "waiting_for_data": 0}
    candidates = (
        RiskScore.objects.filter(alert_type__in=list(WINDOWS), decision__in=["ACT", "WATCH"], is_replay=False,
                                 score_date__lt=as_of, outcome__isnull=True)
        .order_by("score_date")
    )
    with transaction.atomic():
        for score in candidates:
            result = _grade(score, as_of)
            if result is None:
                summary["waiting_for_data"] += 1
                continue
            outcome_type, hit, lead = result
            alert = Alert.objects.filter(score=score, status=Alert.SENT).order_by("alert_id").first()
            Outcome.objects.create(alert=alert, score=score, outcome_type=outcome_type, hit=hit,
                                   lead_days=lead, observed_at=as_of)
            summary["graded"] += 1
            summary["hits" if hit else "misses"] += 1
    return summary


def _week_start(day):
    return day - dt.timedelta(days=day.weekday())


def _brier_skill(pairs):
    if not pairs:
        return None
    bs = sum((p - o) ** 2 for p, o in pairs) / len(pairs)
    base = sum(o for _, o in pairs) / len(pairs)
    reference = base * (1 - base)
    return round(1 - bs / reference, 3) if reference > 0 else None


def scorecard():
    rows = defaultdict(lambda: {"scores": set(), "hits": 0, "false_alarms": 0, "leads": [], "pairs": []})
    sent = (
        Alert.objects.filter(status=Alert.SENT, score__isnull=False, score__is_replay=False)
        .values_list("alert_type_id", "score_id", "sent_at", "created_at")
    )
    for alert_type, score_id, sent_at, created_at in sent:
        period = _week_start((sent_at or created_at).astimezone(EAT).date())
        rows[(alert_type, period)]["scores"].add(score_id)

    outcomes = (
        Outcome.objects.filter(score__isnull=False, hit__isnull=False, score__is_replay=False)
        .select_related("score")
    )
    for outcome in outcomes:
        score = outcome.score
        alerted_period = next((period for (code, period), row in rows.items()
                               if code == score.alert_type_id and score.score_id in row["scores"]), None)
        if alerted_period is not None:
            row = rows[(score.alert_type_id, alerted_period)]
            row["hits" if outcome.hit else "false_alarms"] += 1
            if outcome.hit and outcome.lead_days is not None:
                row["leads"].append(float(outcome.lead_days))
        if score.probability is not None:
            period = alerted_period or _week_start(score.score_date)
            rows[(score.alert_type_id, period)]["pairs"].append((float(score.probability), 1.0 if outcome.hit else 0.0))

    items = []
    for (alert_type, period), row in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True):
        graded = row["hits"] + row["false_alarms"]
        items.append({
            "alert_type": alert_type,
            "period": period.isoformat(),
            "alerts_sent": len(row["scores"]),
            "hits": row["hits"],
            "false_alarms": row["false_alarms"],
            "hit_rate": round(row["hits"] / graded, 3) if graded else None,
            "false_alarm_rate": round(row["false_alarms"] / graded, 3) if graded else None,
            "avg_lead_days": round(sum(row["leads"]) / len(row["leads"]), 1) if row["leads"] else None,
            "brier_skill": _brier_skill(row["pairs"]),
        })
    return items


def calibration(bins=10):
    """V_CALIBRATION: predicted vs observed frequency per probability bin."""
    grouped = defaultdict(list)
    for outcome in Outcome.objects.filter(score__isnull=False, hit__isnull=False).select_related("score"):
        p = outcome.score.probability
        if p is None:
            continue
        index = min(int(float(p) * bins), bins - 1)
        grouped[(outcome.score.alert_type_id, index)].append((float(p), 1.0 if outcome.hit else 0.0))
    return [
        {"alert_type": code, "prob_bin": round(index / bins, 2),
         "predicted": round(sum(p for p, _ in pairs) / len(pairs), 3),
         "observed": round(sum(o for _, o in pairs) / len(pairs), 3), "n": len(pairs)}
        for (code, index), pairs in sorted(grouped.items())
    ]
