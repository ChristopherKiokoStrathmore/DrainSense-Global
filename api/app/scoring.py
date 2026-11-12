"""Load the committed flood-model artifacts. No fitting happens here."""
from __future__ import annotations

import csv
import json
import math
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


@lru_cache(maxsize=1)
def _places() -> dict[str, dict]:
    """Reverse-geocoded names from model/geocode_cells.py. Missing file: every cell falls back to coordinates."""
    path = ARTIFACTS / "cell_places.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())["cells"]


def _coords(lat: float, lon: float) -> str:
    return f"{abs(lat):g}°{'S' if lat < 0 else 'N'} {abs(lon):g}°{'W' if lon < 0 else 'E'}"


def place_name(cell_id: str) -> str:
    """A readable name for the cell: its region and country, or its centre's coordinates over open water."""
    name = (_places().get(cell_id) or {}).get("name")
    if name:
        return name
    row = _cells().get(cell_id)
    return _coords(row["center_lat"], row["center_lon"]) if row else cell_id


def place_names() -> dict[str, str]:
    return {cell_id: place_name(cell_id) for cell_id in _cells()}


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
                "place_name": place_name(props["cell_id"]),
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
    place = _places().get(row["cell_id"]) or {}
    return {
        "cell_id": row["cell_id"],
        "place_name": place_name(row["cell_id"]),
        "country_code": place.get("country_code"),
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


# Which part of the committed run each label year belongs to. The map model is fitted on 2014-2018 only.
PHASES = [
    {
        "phase": "fit",
        "label": "Fitted",
        "note": "The model was fitted on these years. Its scores here are in-sample, so they flatter the model.",
    },
    {
        "phase": "test",
        "label": "Held-out test",
        "note": "Later years the model never saw. Predicted from the previous year's rainfall, then checked against the DFO catalogue.",
    },
    {
        "phase": "outlook",
        "label": "Outlook",
        "note": "The forward prediction. No DFO label in this run, so there is nothing yet to check it against.",
    },
]


def _probability(model: dict, row: dict) -> float:
    z = model["intercept"]
    for name, mean, scale, coef in zip(model["feature_columns"], model["mean"], model["scale"], model["coef"]):
        z += coef * (float(row[name]) - mean) / scale
    return 1.0 / (1.0 + math.exp(-z))


def _roc_auc(labels: list[int], scores: list[float]) -> float | None:
    """Mann-Whitney ROC-AUC with tied scores sharing their average rank."""
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return None
    order = sorted(range(len(scores)), key=scores.__getitem__)
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    pos_rank_sum = sum(rank for rank, label in zip(ranks, labels) if label)
    return (pos_rank_sum - pos * (pos + 1) / 2) / (pos * neg)


@lru_cache(maxsize=1)
def timeline() -> dict:
    """The committed model applied to every label year in the feature table, beside the DFO label where one exists.

    Same coefficients as the map (metrics.json), same inputs (cell_year_features.csv), so the outlook year
    reproduces scores.geojson. Nothing is refitted.
    """
    card = metrics()
    model = card["model"]
    threshold = model["threshold"]
    fit_years = set(card["split"]["train_label_years"])
    test_years = set(card["split"]["test_label_years"])
    outlook_year = card["split"]["outlook_label_year"]

    by_cell: dict[str, dict[int, dict]] = {}
    with (ARTIFACTS / "cell_year_features.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            by_cell.setdefault(row["cell_id"], {})[int(row["label_year"])] = row
    known = _cells()
    years = sorted({year for rows in by_cell.values() for year in rows})

    cells: dict[str, dict] = {}
    for cell_id in sorted(by_cell):
        if cell_id not in known:
            continue
        rows = by_cell[cell_id]
        probability, flooded, rain = [], [], []
        for year in years:
            row = rows.get(year)
            if row is None:
                probability.append(None)
                flooded.append(None)
                rain.append(None)
                continue
            probability.append(round(_probability(model, row), 6))
            flooded.append(None if row["flooded"] in ("", "nan") else int(float(row["flooded"])))
            rain.append(round(float(row["annual_precip_mm"]), 1))
        cells[cell_id] = {"probability": probability, "flooded": flooded, "previous_year_rain_mm": rain}

    summary = []
    for i, year in enumerate(years):
        phase = "outlook" if year == outlook_year else "fit" if year in fit_years else "test" if year in test_years else "other"
        scored = [(c["probability"][i], c["flooded"][i]) for c in cells.values() if c["probability"][i] is not None]
        flagged = [p >= threshold for p, _ in scored]
        labelled = [(p, f) for p, f in scored if f is not None]
        entry = {
            "year": year,
            "feature_year": year - 1,
            "phase": phase,
            "cells": len(scored),
            "mean_probability": round(sum(p for p, _ in scored) / len(scored), 6) if scored else None,
            "cells_above_threshold": sum(flagged),
            "recorded_flood_cells": None,
            "hits": None,
            "missed": None,
            "false_alarms": None,
            "roc_auc": None,
        }
        if labelled:
            entry["recorded_flood_cells"] = sum(f for _, f in labelled)
            entry["hits"] = sum(1 for p, f in labelled if f and p >= threshold)
            entry["missed"] = sum(1 for p, f in labelled if f and p < threshold)
            entry["false_alarms"] = sum(1 for p, f in labelled if not f and p >= threshold)
            auc = _roc_auc([f for _, f in labelled], [p for p, _ in labelled])
            entry["roc_auc"] = None if auc is None else round(auc, 4)
        summary.append(entry)

    return {
        "years": years,
        "outlook_year": outlook_year,
        "threshold": threshold,
        "phases": [
            {**phase, "years": [s["year"] for s in summary if s["phase"] == phase["phase"]]} for phase in PHASES
        ],
        "summary": summary,
        "cells": cells,
        "reading": (
            "Each label year is scored from the previous calendar year's rainfall and the cell's elevation, "
            f"with the committed logistic model. Years {min(fit_years)}-{max(fit_years)} were used to fit it; "
            f"{min(test_years)}-{max(test_years)} are held out and checked against the DFO catalogue; "
            f"{outlook_year} is the outlook and has no label yet. A recorded flood means a DFO large-flood "
            "bounding-box centre fell in the cell that year. Missing a record is not proof of no flood."
        ),
    }
