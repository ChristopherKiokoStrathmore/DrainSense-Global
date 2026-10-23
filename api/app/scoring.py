"""Load the committed flood-model artifacts. No fitting happens here."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "model" / "artifacts"

FEATURE_LABELS = {
    "elev_m": "Elevation",
    "annual_precip_mm": "Previous-year rainfall",
    "p95_daily_mm": "Previous-year daily 95th percentile",
    "max_daily_mm": "Previous-year wettest day",
    "max_7day_mm": "Previous-year wettest 7-day total",
    "wet_day_frac": "Previous-year fraction of days ≥ 1 mm",
    "heavy_day_count": "Previous-year days ≥ 20 mm",
}


@lru_cache(maxsize=1)
def metrics() -> dict:
    return json.loads((ARTIFACTS / "metrics.json").read_text())


@lru_cache(maxsize=1)
def _cells() -> dict[str, dict]:
    collection = json.loads((ARTIFACTS / "scores.geojson").read_text())
    cells = {}
    for feature in collection["features"]:
        props = feature["properties"]
        cells[props["cell_id"]] = {**props, "geometry": feature["geometry"]}
    return cells


def cells() -> list[dict]:
    return list(_cells().values())


def cell(cell_id: str) -> dict | None:
    return _cells().get(cell_id)


def grid() -> dict:
    collection = json.loads((ARTIFACTS / "scores.geojson").read_text())
    features = []
    for feature in collection["features"]:
        props = feature["properties"]
        features.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                "cell_id": props["cell_id"],
                "probability": props["probability"],
                "above_threshold": props["above_threshold"],
                "center_lat": props["center_lat"],
                "center_lon": props["center_lon"],
                "rank": props["rank"],
                "elev_m": props["elev_m"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def hotspots(limit: int = 12) -> list[dict]:
    ranked = sorted(cells(), key=lambda row: (row["rank"], row["cell_id"]))
    return [_public_cell(row) for row in ranked[:limit]]


def _public_cell(row: dict) -> dict:
    shap = []
    for name, value in row["shap_log_odds"].items():
        shap.append({
            "feature": name,
            "label": FEATURE_LABELS.get(name, name),
            "log_odds": value,
            "direction": "raises flood score" if value > 0 else "lowers flood score" if value < 0 else "no push",
        })
    shap.sort(key=lambda item: abs(item["log_odds"]), reverse=True)
    return {
        "cell_id": row["cell_id"],
        "center_lat": row["center_lat"],
        "center_lon": row["center_lon"],
        "elev_m": row["elev_m"],
        "probability": row["probability"],
        "above_threshold": row["above_threshold"],
        "rank": row["rank"],
        "outlook_year": row["outlook_year"],
        "feature_year": row["feature_year"],
        "expected_log_odds": row["expected_log_odds"],
        "top_shap_feature": row["top_shap_feature"],
        "shap": shap,
        "features": row["features"],
        "dfo_positive_years_2014_2018": row["dfo_positive_years_2014_2018"],
        "data_origin": "model",
        "label_status": row["label_status"],
        "reading": (
            f"Model probability {row['probability']:.3f} that this cell is the bounding-box center "
            f"of at least one DFO large flood in {row['outlook_year']}, using {row['feature_year']} rainfall "
            "and elevation. This year has no label in the committed run."
        ),
    }


def sector_indicators(row: dict) -> dict:
    """Deterministic flags. They repeat the model score in other words. They are not new models."""
    card = metrics()
    threshold = card["model"]["threshold"]
    median_rain = card["train_feature_medians"]["annual_precip_mm"]
    probability = row["probability"]
    rain = row["features"]["annual_precip_mm"]
    elev = row["elev_m"]
    wetter_than_train_median = rain >= median_rain
    high = probability >= threshold
    history = row["dfo_positive_years_2014_2018"]
    return {
        "disclaimer": (
            "Derived indicators only. Each one is a rule on the flood-model score, elevation, "
            "and previous-year rainfall. None of them is a crop, disease, claim, or logistics model."
        ),
        "agri_suppliers": {
            "audience": "Agricultural suppliers",
            "kind": "derived indicator",
            "active": bool(high and wetter_than_train_median),
            "statement": (
                "Attention flag for a wetter-than-median previous year in a cell the flood model "
                f"places at or above its F1 threshold ({threshold}). "
                "Not a yield forecast, not a price, and not an observed crop loss."
            ),
            "inputs": {
                "model_probability": probability,
                "threshold": threshold,
                "previous_year_rain_mm": rain,
                "train_median_rain_mm": median_rain,
                "previous_year_wetter_than_train_median": wetter_than_train_median,
            },
        },
        "health": {
            "audience": "Health and epidemiology planners",
            "kind": "derived indicator",
            "active": bool(high and elev < 200),
            "statement": (
                "Attention flag for a low-lying cell (elevation under 200 m) at or above the flood-model "
                "threshold. A prompt to review water and vector conditions with local surveillance. "
                "Not a cholera, malaria, or any other disease forecast, and not a case count."
            ),
            "inputs": {"model_probability": probability, "threshold": threshold, "elev_m": elev},
        },
        "insurers_logistics": {
            "audience": "Insurers and logistics planners",
            "kind": "derived indicator",
            "active": bool(high or history >= 2),
            "statement": (
                f"Accumulation flag. This cell had {history} DFO-positive label years in the training window "
                "2014-2018 (observed catalogue counts, not losses). "
                "The outlook probability is the flood model, not a loss ratio, premium, or route closure."
            ),
            "inputs": {
                "model_probability": probability,
                "threshold": threshold,
                "dfo_positive_years_2014_2018": history,
            },
        },
    }


def detail(cell_id: str) -> dict | None:
    row = cell(cell_id)
    if row is None:
        return None
    public = _public_cell(row)
    public["sectors"] = sector_indicators(row)
    return public
