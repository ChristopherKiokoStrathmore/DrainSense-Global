"""Serve the CatBoost flood-impact models in model/artifacts/impact. No fitting happens here."""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import date
from functools import lru_cache
from typing import Literal

import pandas as pd
from catboost import CatBoostRegressor, Pool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.app.scoring import ARTIFACTS

IMPACT_DIR = ARTIFACTS / "impact"
VARIANT_NAMES = ("brief", "no_size")
SEVERITY_LEVELS = (1.0, 1.5, 2.0)
FORMULA = "log10(days_inclusive × severity × area_km2)"

# API field -> model column. Observed-weather fields are the model names in lower case without "wx_".
# Each is a daily Open-Meteo (ERA5 / ERA5-Land) value at the centre of the flooded area.
WEATHER_FIELDS = {
    "elevation_m": "Elevation_m",
    "precip_antecedent_7d_mm": "Wx_Precip_Antecedent_7d_mm",
    "precip_antecedent_14d_mm": "Wx_Precip_Antecedent_14d_mm",
    "soil_moisture_before_m3m3": "Wx_Soil_Moisture_Before_m3m3",
    "precip_max_1d_first3d_mm": "Wx_Precip_Max_1d_First3d_mm",
    "precip_first3d_mm": "Wx_Precip_First3d_mm",
    "wind_gust_max_first3d_kmh": "Wx_Wind_Gust_Max_First3d_kmh",
    "temp_mean_first3d_c": "Wx_Temp_Mean_First3d_C",
    "pressure_min_first3d_hpa": "Wx_Pressure_Min_First3d_hPa",
    "precip_event_mm": "Wx_Precip_Event_mm",
    "rain_event_mm": "Wx_Rain_Event_mm",
    "snowfall_event_cm": "Wx_Snowfall_Event_cm",
    "precip_max_1d_mm": "Wx_Precip_Max_1d_mm",
    "et0_event_mm": "Wx_ET0_Event_mm",
    "temp_max_c": "Wx_Temp_Max_C",
    "rh_mean_pct": "Wx_RH_Mean_pct",
    "soil_moisture_event_max_m3m3": "Wx_Soil_Moisture_Event_Max_m3m3",
}
# Model columns built from other fields, and the API fields each one needs.
DERIVED = {
    "Log_Area": ("area_km2",),
    "Event_Duration_Days": ("start_date", "end_date"),
    "Start_Month": ("start_date",),
    "Cause_Group": ("main_cause",),
    "Footprint_Lat": ("latitude",),
    "Footprint_Lon": ("longitude",),
    "Continent_Region": ("continent_region",),
}

BY_DAY_3 = "the 14 days before start_date and the first three days of the event"
WHOLE_EVENT = "start_date through end_date (at most the first 45 days)"


def _wx(description: str, **limits):
    return Field(default=None, description=description, **limits)


class ImpactEventIn(BaseModel):
    start_date: date
    end_date: date | None = None
    area_km2: float | None = Field(default=None, gt=0, description="Mapped flooded area")
    severity: float | None = Field(default=None, description="DFO severity class: 1, 1.5 or 2. Only feeds dfo_formula.")
    latitude: float | None = Field(default=None, ge=-90, le=90, description="Centre of the flooded area")
    longitude: float | None = Field(default=None, ge=-180, le=180, description="Centre of the flooded area")
    continent_region: str
    main_cause: str | None = Field(default=None, max_length=120, description="Free text, e.g. Heavy rain")
    elevation_m: float | None = _wx("Ground elevation at the point, as Open-Meteo reports it")
    precip_antecedent_7d_mm: float | None = _wx("Rain in the 7 days before start_date", ge=0)
    precip_antecedent_14d_mm: float | None = _wx("Rain in the 14 days before start_date", ge=0)
    soil_moisture_before_m3m3: float | None = _wx("Topsoil (0-7 cm) moisture the day before start_date", ge=0, le=1)
    precip_max_1d_first3d_mm: float | None = _wx("Wettest day among the first three days", ge=0)
    precip_first3d_mm: float | None = _wx("Rain over the first three days", ge=0)
    wind_gust_max_first3d_kmh: float | None = _wx("Strongest gust over the first three days", ge=0)
    temp_mean_first3d_c: float | None = _wx("Mean temperature over the first three days")
    pressure_min_first3d_hpa: float | None = _wx("Lowest sea-level pressure over the first three days", gt=0)
    precip_event_mm: float | None = _wx(f"Rain over {WHOLE_EVENT}", ge=0)
    rain_event_mm: float | None = _wx(f"Liquid rain over {WHOLE_EVENT}", ge=0)
    snowfall_event_cm: float | None = _wx(f"Snowfall over {WHOLE_EVENT}", ge=0)
    precip_max_1d_mm: float | None = _wx(f"Wettest day over {WHOLE_EVENT}", ge=0)
    et0_event_mm: float | None = _wx(f"Reference evapotranspiration over {WHOLE_EVENT}", ge=0)
    temp_max_c: float | None = _wx(f"Highest daily maximum temperature over {WHOLE_EVENT}")
    rh_mean_pct: float | None = _wx(f"Mean relative humidity over {WHOLE_EVENT}", ge=0, le=100)
    soil_moisture_event_max_m3m3: float | None = _wx(f"Highest topsoil moisture over {WHOLE_EVENT}", ge=0, le=1)

    @model_validator(mode="after")
    def _check(self):
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.severity is not None and self.severity not in SEVERITY_LEVELS:
            raise ValueError("severity must be 1, 1.5, or 2 (the DFO severity classes)")
        return self


class ImpactBatchIn(BaseModel):
    events: list[ImpactEventIn] = Field(min_length=1, max_length=500)


class ImpactInputError(ValueError):
    """An event that a variant cannot score. The message names what is missing or unknown."""


class ModelOutOfStep(RuntimeError):
    """The saved model files and this module disagree on the inputs, e.g. mid-retrain. Served as 503."""


# Response shapes. They exist for the OpenAPI schema; predict() still builds plain dicts.

class Contribution(BaseModel):
    feature: str = Field(description="Model column")
    input: int | float | str = Field(description="Value the model saw, after derivation")
    contribution: float = Field(description="CatBoost SHAP value in index points")


class DfoFormulaInputs(BaseModel):
    days_inclusive: int
    severity: float
    area_km2: float


class DfoFormula(BaseModel):
    value: float = Field(description="log10(days_inclusive × severity × area_km2)")
    rounded: float = Field(description="Rounded to 0.1, as the DFO table stores it")
    definition: str
    inputs: DfoFormulaInputs
    note: str


class ImpactPrediction(BaseModel):
    variant: Literal["brief", "no_size"]
    flood_impact_index: float = Field(description="Model estimate of the DFO flood impact index")
    dfo_formula: DfoFormula | None = Field(description="Exact value, when dates, area and severity were sent")
    model_minus_formula: float | None
    expected_value: float = Field(description="SHAP base value; plus the contributions equals the estimate")
    contributions: list[Contribution] = Field(description="Sorted by absolute size")
    outside_training_range: list[str] = Field(description="Model columns outside the 1985-2018 training range")
    model_test_rmse: float = Field(description="RMSE of this variant on 2019-2024 events")
    data_origin: str
    reading: str


class ImpactBatchOut(BaseModel):
    variant: Literal["brief", "no_size"]
    count: int
    results: list[ImpactPrediction]


class HeldOutEvent(BaseModel):
    event_id: int
    start_date: str
    end_date: str
    country: str
    iso_country_code: str
    continent_region: str
    cause_group: str
    area_km2: float
    days_inclusive: int
    severity: float
    flood_impact_index: float = Field(description="Recorded DFO value")
    dfo_formula: float
    predictions: dict[str, float] = Field(description="Holdout-model prediction per variant")
    origin: str


class HeldOutEventsOut(BaseModel):
    test_years: str
    rmse: dict[str, float]
    events: list[HeldOutEvent]


class Scores(BaseModel):
    rmse: float
    mae: float
    r2: float
    n: int


class VariantCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    file: str
    description: str
    numeric_features: list[str]
    categorical_features: list[str]
    iterations: int
    temporal_test: Scores
    feature_importance: dict[str, float]
    mean_abs_shap_test: dict[str, float]


class VariantInputs(BaseModel):
    required: list[str]
    used_for_dfo_formula: list[str]


class ModelCard(BaseModel):
    """The metrics file plus the input rules. Fields not listed here are passed through."""

    model_config = ConfigDict(extra="allow")

    generated_at: str
    target: str
    target_definition: str
    catboost_params: dict
    split: dict
    variants: dict[str, VariantCard]
    baselines_temporal_test: dict
    brief_reproduction: dict
    data_checks: dict
    categories: dict[str, list[str]]
    limitations: list[str]
    inputs: dict[str, VariantInputs]
    weather_fields: dict[str, str]


# Request examples for the OpenAPI schema, from event_features.csv (weather rounded to 3 decimals).
# Event 5477 is the Kenya flood of Oct-Nov 2023.
KENYA_2023 = {
    "start_date": "2023-10-15",
    "end_date": "2023-11-11",
    "area_km2": 229835,
    "severity": 1.5,
    "continent_region": "Africa",
    "elevation_m": 244,
    "precip_antecedent_7d_mm": 0.0,
    "precip_antecedent_14d_mm": 1.2,
    "soil_moisture_before_m3m3": 0.115,
    "precip_max_1d_first3d_mm": 5.3,
    "precip_first3d_mm": 9.5,
    "wind_gust_max_first3d_kmh": 40,
    "temp_mean_first3d_c": 29.5,
    "pressure_min_first3d_hpa": 1007.7,
    "precip_event_mm": 200.9,
    "rain_event_mm": 200.9,
    "snowfall_event_cm": 0.0,
    "precip_max_1d_mm": 23.6,
    "et0_event_mm": 135.4,
    "temp_max_c": 36.1,
    "rh_mean_pct": 68.536,
    "soil_moisture_event_max_m3m3": 0.414,
}
# Event 5095, the July 2021 floods in Germany and Belgium, with only the fields no_size needs.
EUROPE_2021_BY_DAY_3 = {
    "start_date": "2021-07-14",
    "latitude": 50.6021,
    "longitude": 5.887,
    "continent_region": "Europe",
    "main_cause": "Torrential rain",
    "elevation_m": 174,
    "precip_antecedent_7d_mm": 34.7,
    "precip_antecedent_14d_mm": 77.4,
    "soil_moisture_before_m3m3": 0.423,
    "precip_max_1d_first3d_mm": 66.7,
    "precip_first3d_mm": 90.1,
    "wind_gust_max_first3d_kmh": 65.9,
    "temp_mean_first3d_c": 16.833,
    "pressure_min_first3d_hpa": 1008.3,
}
PREDICT_EXAMPLES = {
    "brief": {
        "summary": "brief: Kenya, Oct-Nov 2023 (event 5477)",
        "description": "Every field the brief variant needs, plus severity so the response carries the formula.",
        "value": KENYA_2023,
    },
    "no_size": {
        "summary": "no_size: Germany and Belgium, July 2021 (event 5095)",
        "description": "Send with ?variant=no_size. No area, end date or severity: what is known by the third day.",
        "value": EUROPE_2021_BY_DAY_3,
    },
}
BATCH_EXAMPLES = {
    "two_events": {
        "summary": "The Kenya event, and the same event at 10 km²",
        "value": {"events": [KENYA_2023, {**KENYA_2023, "area_km2": 10}]},
    },
}


@lru_cache(maxsize=1)
def metrics() -> dict:
    return json.loads((IMPACT_DIR / "impact_metrics.json").read_text())


@lru_cache(maxsize=2)
def model(variant: str) -> CatBoostRegressor:
    loaded = CatBoostRegressor()
    loaded.load_model(str(IMPACT_DIR / metrics()["variants"][variant]["file"]))
    return loaded


def required_fields(variant: str) -> list[str]:
    spec = metrics()["variants"][variant]
    needed: list[str] = []
    for column in [*spec["numeric_features"], *spec["categorical_features"]]:
        if column in DERIVED:
            fields = DERIVED[column]
        else:
            matches = [k for k, v in WEATHER_FIELDS.items() if v == column]
            if not matches:
                raise ModelOutOfStep(
                    f"The impact model is unavailable: its saved files expect input {column}, which this API "
                    "version no longer takes. Retrain with python -m model.impact and restart the API."
                )
            fields = (matches[0],)
        needed.extend(f for f in fields if f not in needed)
    return needed


def cause_group(cause: str) -> str:
    text = cause.strip().lower()
    for name, pattern in metrics()["cause_rules"]:
        if re.search(pattern, text):
            return name
    return "other"


def _days_inclusive(event: ImpactEventIn) -> int:
    return (event.end_date - event.start_date).days + 1


def _row(event: ImpactEventIn, variant: str) -> dict:
    card = metrics()
    missing = [f for f in required_fields(variant) if getattr(event, f) is None]
    if missing:
        raise ImpactInputError(f"variant {variant} needs: {', '.join(missing)}")
    allowed = card["categories"]["Continent_Region"]
    if event.continent_region not in allowed:
        raise ImpactInputError(f"continent_region must be one of: {'; '.join(allowed)}")
    values = {column: getattr(event, field) for field, column in WEATHER_FIELDS.items()}
    values.update({
        "Footprint_Lat": event.latitude,
        "Footprint_Lon": event.longitude,
        "Continent_Region": event.continent_region,
        "Start_Month": event.start_date.month,
    })
    if event.area_km2 is not None:
        values["Log_Area"] = math.log10(event.area_km2)
    if event.end_date is not None:
        # The training column is end - start, with same-day events stored as 1.
        values["Event_Duration_Days"] = max((event.end_date - event.start_date).days, 1)
    if event.main_cause is not None:
        values["Cause_Group"] = cause_group(event.main_cause)
    return values


def _formula(event: ImpactEventIn) -> dict | None:
    if event.area_km2 is None or event.severity is None or event.end_date is None:
        return None
    days = _days_inclusive(event)
    value = math.log10(days * event.severity * event.area_km2)
    return {
        "value": round(value, 3),
        "rounded": round(value, 1),
        "definition": FORMULA,
        "inputs": {"days_inclusive": days, "severity": event.severity, "area_km2": event.area_km2},
        "note": "This is how the DFO table defines the index. When these inputs are known it is exact.",
    }


def _outside_training_range(row: dict, columns: list[str]) -> list[str]:
    ranges = metrics()["train_feature_ranges"]
    flagged = []
    for column in columns:
        if column in ranges and row.get(column) is not None:
            low, high = ranges[column]
            if not low <= row[column] <= high:
                flagged.append(column)
    return flagged


def predict(events: list[ImpactEventIn], variant: str, label_index: bool = False) -> list[dict]:
    """Score events. With label_index, an input error starts with the event's position (`event 3: ...`)."""
    if variant not in VARIANT_NAMES:
        raise ImpactInputError(f"variant must be one of: {', '.join(VARIANT_NAMES)}")
    card = metrics()
    spec = card["variants"][variant]
    regressor = model(variant)
    columns = list(regressor.feature_names_)
    rows = []
    for index, event in enumerate(events):
        try:
            rows.append(_row(event, variant))
        except ImpactInputError as exc:
            raise ImpactInputError(f"event {index}: {exc}") if label_index else exc
    frame = pd.DataFrame([{c: row[c] for c in columns} for row in rows], columns=columns)
    pool = Pool(frame, cat_features=spec["categorical_features"])
    predictions = regressor.predict(pool)
    shap = regressor.get_feature_importance(pool, type="ShapValues")
    test_rmse = spec["temporal_test"]["rmse"]

    results = []
    for event, row, prediction, shap_row in zip(events, rows, predictions, shap):
        contributions = [
            {"feature": column, "input": row[column], "contribution": round(float(value), 4)}
            for column, value in zip(columns, shap_row[:-1])
        ]
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        formula = _formula(event)
        estimate = round(float(prediction), 3)
        results.append({
            "variant": variant,
            "flood_impact_index": estimate,
            "dfo_formula": formula,
            "model_minus_formula": round(estimate - formula["value"], 3) if formula else None,
            "expected_value": round(float(shap_row[-1]), 4),
            "contributions": contributions,
            "outside_training_range": _outside_training_range(row, columns),
            "model_test_rmse": test_rmse,
            "data_origin": "model",
            "reading": _reading(variant, estimate, test_rmse, formula),
        })
    return results


def _reading(variant: str, estimate: float, test_rmse: float, formula: dict | None) -> str:
    if variant == "brief":
        text = (
            f"CatBoost estimate {estimate:.2f} of the DFO flood impact index (test RMSE {test_rmse}). "
            "The model is given area and duration, two of the three terms that define the index."
        )
    else:
        text = (
            f"Estimate {estimate:.2f} without area, duration, or severity (test RMSE {test_rmse}). "
            "It uses weather up to the third day of the event, so it is an early estimate, not a forecast."
        )
    if formula:
        text += f" The formula gives {formula['value']:.2f} from the inputs you sent, and that value is exact."
    return text


def model_card() -> dict:
    card = metrics()
    return {
        **card,
        "inputs": {
            variant: {
                "required": required_fields(variant),
                "used_for_dfo_formula": ["start_date", "end_date", "area_km2", "severity"],
            }
            for variant in VARIANT_NAMES
        },
        "weather_fields": WEATHER_FIELDS,
    }


@lru_cache(maxsize=1)
def _test_rows() -> list[dict]:
    with (IMPACT_DIR / "test_predictions.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    numeric = ("Area_km2", "Days_Inclusive", "Severity", "Flood_Impact_Index", "dfo_formula",
               *(f"pred_{name}" for name in VARIANT_NAMES))
    for row in rows:
        row["Event_ID"] = int(row["Event_ID"])
        for key in numeric:
            row[key] = float(row[key])
    rows.sort(key=lambda r: (r["Start_Date"], r["Event_ID"]), reverse=True)
    return rows


def test_events(limit: int = 50, country: str | None = None) -> list[dict]:
    rows = _test_rows()
    if country:
        needle = country.strip().lower()
        rows = [r for r in rows if needle in r["Country"].lower() or needle == r["ISO_Country_Code"].lower()]
    out = []
    for row in rows[:limit]:
        out.append({
            "event_id": row["Event_ID"],
            "start_date": row["Start_Date"],
            "end_date": row["End_Date"],
            "country": row["Country"],
            "iso_country_code": row["ISO_Country_Code"],
            "continent_region": row["Continent_Region"],
            "cause_group": row["Cause_Group"],
            "area_km2": row["Area_km2"],
            "days_inclusive": int(row["Days_Inclusive"]),
            "severity": row["Severity"],
            "flood_impact_index": row["Flood_Impact_Index"],
            "dfo_formula": row["dfo_formula"],
            "predictions": {name: row[f"pred_{name}"] for name in VARIANT_NAMES},
            "origin": "held-out DFO record, start year 2019 or later; predictions from the holdout models",
        })
    return out
