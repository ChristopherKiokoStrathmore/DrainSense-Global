"""heuristic-v0 risk rules: the fallback scorer behind POST /jobs/score and /jobs/replay.

Trevor's trained, calibrated models (data/models) are the real scorer and write RISK_SCORES
directly; this module only exists so the demo controls work end to end before they land, and is
never allowed to overwrite a row written by another model_version (see scoring/service.py).

Each rule is a pure function of one ward's leakage-safe inputs (only rows with
available_date <= as_of) and returns a Signal: a probability in 0..1, or an abstain reason.
The starting rules come from the alert catalogue in ARCHITECTURE §5. Probabilities are
logistic transforms of hand-set weights; they are NOT calibrated and the model_version says so.
"""
import math
from dataclasses import dataclass, field
from statistics import mean

DRY_DAY_MM = 2.0          # a day with < ~2 mm counts as dry
HEAVY_RAIN_MM = 20.0      # A2: > 20 mm/day
MODEL_DISAGREEMENT = 0.35  # |P_gfs - P_ecmwf| above this -> abstain ("GFS/ECMWF disagree")
SINGLE_MODEL_SHRINK = 0.9  # one forecast model only: pull toward 0.5 a little


@dataclass
class Signal:
    probability: float | None = None
    abstain_reason: str | None = None
    raw_score: float | None = None
    hints: dict = field(default_factory=dict)  # template slot hints, e.g. {"day_offset": 1}
    no_data: bool = False  # the rule's inputs are absent for this ward: store nothing


def missing(reason):
    return Signal(abstain_reason=reason, no_data=True)


@dataclass
class WardInputs:
    """Everything a rule may look at for one ward on one as_of date."""

    as_of: object
    features: list            # WardFeatureDaily-like rows, oldest first, available_date <= as_of
    forecasts: dict           # {"GFS": {lead_day: mm}, "ECMWF": {...}} from the latest usable issue

    @property
    def latest(self):
        return self.features[-1] if self.features else None

    def latest_value(self, name):
        for row in reversed(self.features):
            value = getattr(row, name, None)
            if value is not None:
                return float(value)
        return None

    def values_between(self, name, days_back_from, days_back_to):
        """Values of `name` for feature_date in [as_of - days_back_to, as_of - days_back_from]."""
        out = []
        for row in self.features:
            age = (self.as_of - row.feature_date).days
            value = getattr(row, name, None)
            if days_back_from <= age <= days_back_to and value is not None:
                out.append(float(value))
        return out


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def clamp(p, low=0.01, high=0.99):
    return max(low, min(high, p))


def rain_series(inputs):
    """Daily forecast rain by model. FORECASTS rows first; else the GFS columns on the feature row."""
    series = {src: dict(days) for src, days in inputs.forecasts.items() if src in ("GFS", "ECMWF") and days}
    if "GFS" not in series and inputs.latest is not None:
        row = inputs.latest
        known = {1: row.gfs_rain_d1_mm, 2: row.gfs_rain_d2_mm, 3: row.gfs_rain_d3_mm,
                 5: row.gfs_rain_d5_mm, 7: row.gfs_rain_d7_mm}
        known = {d: float(v) for d, v in known.items() if v is not None}
        if known:
            # Day 4 and 6 are not exported; use the mean of their neighbours when both exist.
            for gap, (left, right) in {4: (3, 5), 6: (5, 7)}.items():
                if left in known and right in known:
                    known[gap] = (known[left] + known[right]) / 2
            series["GFS"] = known
    return series


def _combine(per_model, label):
    """Average model probabilities; abstain when GFS and ECMWF disagree."""
    values = {src: p for src, p in per_model.items() if p is not None}
    if not values:
        return missing(f"{label}: forecast inputs missing")
    if len(values) == 1:
        (src, p), = values.items()
        return Signal(probability=clamp(0.5 + (p - 0.5) * SINGLE_MODEL_SHRINK), raw_score=p,
                      hints={"models": src})
    if abs(values["GFS"] - values["ECMWF"]) > MODEL_DISAGREEMENT:
        return Signal(
            probability=clamp(mean(values.values())),
            abstain_reason=f"GFS/ECMWF disagree (GFS {values['GFS']:.0%}, ECMWF {values['ECMWF']:.0%})",
        )
    p = mean(values.values())
    return Signal(probability=clamp(p), raw_score=p, hints={"models": "GFS+ECMWF"})


# ----- MvuaSmart ---------------------------------------------------------------------------------


def _p_dry(mm):
    return sigmoid(-(mm - DRY_DAY_MM) / 0.5)


def _dry_window_probability(days):
    """P(3+ consecutive dry days starting on day 1..5) from a {lead_day: mm} series."""
    starts = []
    for start in range(1, 6):
        window = [days.get(start + k) for k in range(3)]
        if None in window:
            continue
        p = 1.0
        for mm in window:
            p *= _p_dry(mm)
        starts.append((start, p))
    if not starts:
        return None, None
    p_none = 1.0
    for _, p in starts:
        p_none *= 1.0 - p
    best_start = max(starts, key=lambda s: s[1])[0]
    return 1.0 - p_none, best_start


def score_a1(inputs):
    """A1 dry window: P(3+ dry days starting within 5 days) >= 70%."""
    per_model, best = {}, {}
    for src, days in rain_series(inputs).items():
        per_model[src], best[src] = _dry_window_probability(days)
    signal = _combine(per_model, "A1")
    starts = [d for d in best.values() if d]
    if starts:
        signal.hints["day_offset"] = min(starts)
    agreement = inputs.latest_value("ecmwf_agreement")
    if not signal.abstain_reason and "ECMWF" not in per_model and agreement is not None and agreement < 0.5:
        signal.abstain_reason = f"GFS/ECMWF disagree (agreement {agreement:.2f})"
    return signal


def _p_heavy(mm):
    return sigmoid((mm - HEAVY_RAIN_MM) / 4.0)


def score_a2(inputs):
    """A2 heavy rain: P(> 20 mm/day within 48 h) >= 60%."""
    per_model = {}
    for src, days in rain_series(inputs).items():
        if days.get(1) is None and days.get(2) is None:
            per_model[src] = None
            continue
        p_none = 1.0
        for lead in (1, 2):
            if days.get(lead) is not None:
                p_none *= 1.0 - _p_heavy(days[lead])
        per_model[src] = 1.0 - p_none
    return _combine(per_model, "A2")


def _forecast_total(inputs, leads):
    totals = []
    for days in rain_series(inputs).values():
        values = [days.get(d) for d in leads]
        if None not in values:
            totals.append(sum(values))
    return mean(totals) if totals else None


def score_a3(inputs):
    """A3 aflatoxin: wet harvest conditions (recent rain, wet-bag reports, warmth)."""
    rain_14d = inputs.latest_value("rain_14d_mm")
    if rain_14d is None:
        return missing("A3: rain_14d_mm missing")
    wet_bags = inputs.latest_value("wet_bag_reports_3d") or 0.0
    lst_k = inputs.latest_value("lst_k")
    temp_c = (lst_k - 273.15) if lst_k else 25.0
    z = -3.5 + 0.025 * rain_14d + 0.8 * math.log1p(wet_bags) + 0.15 * (temp_c - 25.0)
    return Signal(probability=clamp(sigmoid(z)), raw_score=z)


def score_a4(inputs):
    """A4 cropland flood within 3 days: forecast rain + recent rain + wet soil + river signal."""
    f3 = _forecast_total(inputs, (1, 2, 3))
    if f3 is None:
        return missing("A4: 3-day forecast missing")
    rain_7d = inputs.latest_value("rain_7d_mm") or 0.0
    soil = inputs.latest_value("soil_moist_top")
    glofas = inputs.latest_value("glofas_p_5yr") or 0.0
    z = -4.0 + 0.04 * f3 + 0.015 * rain_7d + 6.0 * ((soil if soil is not None else 0.30) - 0.30) + 3.0 * glofas
    signal = Signal(probability=clamp(sigmoid(z)), raw_score=z)
    if soil is None:
        signal.hints["note"] = "soil moisture missing; assumed 0.30"
    return signal


# ----- Ngao Mifugo -------------------------------------------------------------------------------


def score_b1(inputs):
    """B1 river flood: share of GloFAS ensemble members above the 5-year level (act at > 30%)."""
    share = inputs.latest_value("glofas_p_5yr")
    if share is None:
        return missing("B1: GloFAS ensemble missing")
    return Signal(probability=clamp(share, 0.0, 1.0), raw_score=share)


def score_b2(inputs):
    """B2 flash flood within 2 days."""
    series = rain_series(inputs)
    peaks = [max(v for d, v in days.items() if d in (1, 2)) for days in series.values()
             if any(d in days for d in (1, 2))]
    if not peaks:
        return missing("B2: 2-day forecast missing")
    soil = inputs.latest_value("soil_moist_top")
    rain_7d = inputs.latest_value("rain_7d_mm") or 0.0
    z = -4.0 + 0.06 * mean(peaks) + 5.0 * ((soil if soil is not None else 0.25) - 0.25) + 0.01 * rain_7d
    return Signal(probability=clamp(sigmoid(z)), raw_score=z)


def ndvi_signal(inputs):
    """NDVI anomaly over the last ~3 months. Uses ndvi_anom when exported, else the rise of NDVI
    over its own level 3-5 months earlier (Garissa anomalies were not computed)."""
    anomalies = inputs.values_between("ndvi_anom", 0, 90)
    if anomalies:
        return mean(anomalies), "ndvi_anom"
    recent = inputs.values_between("ndvi", 0, 45)
    baseline = inputs.values_between("ndvi", 90, 150)
    if recent and baseline:
        return mean(recent) - mean(baseline), "ndvi_rise"
    return None, None


def score_b3(inputs):
    """B3 Rift Valley fever: NDVI anomaly > +0.1 over 3 months after above-normal rain."""
    ndvi, how = ndvi_signal(inputs)
    rain_anom = inputs.latest_value("rain_anom_30d")
    if ndvi is None and rain_anom is None:
        return missing("B3: NDVI and rainfall anomaly missing")
    if ndvi is None:
        return missing("B3: no NDVI anomaly (needs ~5 months of NDVI)")
    if rain_anom is None:
        return Signal(abstain_reason="B3: 30-day rainfall anomaly missing")
    z = 2.5 * (ndvi - 0.1) / 0.1 + 1.0 * (rain_anom / 50.0 - 1.0)
    return Signal(probability=clamp(sigmoid(z)), raw_score=z, hints={"ndvi": round(ndvi, 3), "ndvi_source": how})


def score_b4(inputs):
    """B4 drought: VCI3M projected 6 weeks ahead below 35."""
    vci = inputs.latest_value("vci3m")
    if vci is None:
        return missing("B4: VCI3M missing")
    older = inputs.values_between("vci3m", 50, 70)
    slope = (vci - mean(older)) / 60.0 if older else 0.0
    projected = vci + slope * 42
    return Signal(probability=clamp(sigmoid((35.0 - projected) / 6.0)), raw_score=projected)


# ----- Ngao Afya ---------------------------------------------------------------------------------


def score_c2(inputs):
    """C2 human RVF risk: same signal as B3 (health teams see it before outbreaks)."""
    return score_b3(inputs)


def score_c3(inputs):
    """C3 food-safety aflatoxin: same wet-harvest signal as A3."""
    return score_a3(inputs)


RULES = {
    "A1": score_a1, "A2": score_a2, "A3": score_a3, "A4": score_a4,
    "B1": score_b1, "B2": score_b2, "B3": score_b3, "B4": score_b4,
    "C2": score_c2, "C3": score_c3,
}
# C1 (cholera) needs the WASH index and neighbour outbreaks; A5 is set by the dryer router.


def decide(signal, act_threshold, watch_threshold):
    """ACT / WATCH / ABSTAIN, or None when the probability is below the watch threshold (nothing to
    store: the map shows the ward without a warning)."""
    if signal.abstain_reason:
        return "ABSTAIN"
    p = signal.probability
    if act_threshold is not None and p >= act_threshold:
        return "ACT"
    if watch_threshold is not None and p >= watch_threshold:
        return "WATCH"
    return None
