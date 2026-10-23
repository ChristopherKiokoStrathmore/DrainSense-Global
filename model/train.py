#!/usr/bin/env python3
"""Leak-free global flood-susceptibility model for DrainSense.

Label
    A 5-degree cell-year is positive when the bounding-box center of at least one
    Dartmouth Flood Observatory large-flood event falls in that cell during the
    label year. Events come from the public Global Flood Records GeoPackage
    (Zenodo 19288171, CC BY 4.0). The center is the rtree bounding box center,
    not a surveyed gauge and not an inundation polygon.

Features (available before the label year)
    Static elevation from the Open-Meteo elevation API, plus precipitation
    statistics from the previous calendar year only, at the cell center, from
    the Open-Meteo historical archive (daily precipitation_sum). The heavy-day
    threshold is HEAVY_RAIN_MM from the NGAO scorer (backend/scoring/heuristics.py).

Split
    Train labels 2014-2018 (features 2013-2017).
    Test labels 2019-2023 (features 2018-2022).
    Those feature years are disjoint.
    The map outlook is label year 2024 from 2023 rainfall. It has no label here.

    python -m model.train --download     # network: rebuild the feature table
    python -m model.train --refit        # no network: refit from the committed CSV
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shap
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scipy.optimize import OptimizeWarning

warnings.filterwarnings("ignore", category=OptimizeWarning)
warnings.filterwarnings("ignore", category=FutureWarning, module="shap")

REPO = Path(__file__).resolve().parents[1]
# Append, so NGAO's backend/api package does not shadow DrainSense's api package.
_backend = str(REPO / "backend")
if _backend not in sys.path:
    sys.path.append(_backend)
from scoring.heuristics import HEAVY_RAIN_MM  # noqa: E402

ARTIFACTS = REPO / "model" / "artifacts"
CACHE = REPO / "model" / ".cache"
FEATURE_CSV = ARTIFACTS / "cell_year_features.csv"
METRICS_PATH = ARTIFACTS / "metrics.json"
SCORES_PATH = ARTIFACTS / "scores.geojson"
GPKG_URL = "https://zenodo.org/api/records/19288171/files/Global_Flood_Records.gpkg/content"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

STEP = 5.0
LAT_SW = list(range(-55, 70, 5))  # -55 .. 65
LON_SW = list(range(-180, 180, 5))  # -180 .. 175
TRAIN_YEARS = (2014, 2015, 2016, 2017, 2018)
TEST_YEARS = (2019, 2020, 2021, 2022, 2023)
OUTLOOK_YEAR = 2024
FEATURE_START = min(TRAIN_YEARS) - 1  # 2013
FEATURE_END = OUTLOOK_YEAR - 1  # 2023
MIN_DAYS = 300
MAX_BOX_DEG = 15.0
C_GRID = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0)
WET_DAY_MM = 1.0
SPATIAL_HOLDOUT_LON = (30.0, 90.0)  # [30, 90): East Africa through South Asia
BATCH = 12

FEATURE_COLUMNS = [
    "elev_m",
    "annual_precip_mm",
    "p95_daily_mm",
    "max_daily_mm",
    "max_7day_mm",
    "wet_day_frac",
    "heavy_day_count",
]

INCLUDE_TOKENS = (
    "rain", "monsoon", "cyclone", "storm", "typhoon", "hurricane", "snow",
    "dam", "levee", "levy", "surge", "tide", "glacial", "glacier",
)
EXCLUDE_TOKENS = ("tsunami", "avalanche", "avalance", "landslide", "ice jam", "ice jams")

LIMITATIONS = [
    "The label is a Dartmouth Flood Observatory large-flood catalogue event, assigned to a 5-degree cell by the center of the event bounding box. It is not a mapped inundation footprint, a gauge exceedance, or a household survey.",
    "Cells are 5 degrees on a side, about 500 km at the equator. A score is for that whole cell, not a street, a drain, or a ward.",
    "Rainfall features are the previous calendar year's Open-Meteo daily precipitation at the cell center, plus elevation. The model does not use a weather forecast, river discharge, soil moisture, drainage infrastructure, or flood defenses.",
    "Training label years are 2014-2018 and test label years are 2019-2023. Feature years do not overlap. The same cells appear in both splits, so the temporal test measures a later period at seen locations. The longitude-block holdout is the check on unseen regions.",
    "DFO records large events drawn from news, agencies, and satellites. Absence of a record is not proof that no flood occurred. Small and unreported floods are missing, and reporting effort differs by country.",
    "Events whose bounding box is wider or taller than 15 degrees are dropped so a continent-scale outline does not stamp a single cell. Cells the elevation API treats as ocean (elevation 0) are dropped, which removes some coastal box centers.",
    "The 2024 outlook uses 2023 rainfall and has no DFO label in this run. It is a model score, not a verified outcome.",
    "Sector panels are deterministic relabellings of the score, elevation, and rainfall. They are not crop, epidemiological, or insurance-loss models.",
    "Drainage reports, inspections, repairs, and incidents shipped with the prototype are synthetic drill rows. Rows created through the API are stored as community reports and are not flood observations.",
    "DrainSense is not a warning service and does not replace national hydrological agencies.",
]


def cell_id(sw_lat: float, sw_lon: float) -> str:
    def part(value: float, pos: str, neg: str) -> str:
        number = abs(int(round(value)))
        return f"{pos}{number}" if value >= 0 else f"{neg}{number}"

    return f"{part(sw_lat, 'n', 's')}_{part(sw_lon, 'e', 'w')}"


def sw_corner(lat: float, lon: float) -> tuple[float, float]:
    if lon == 180:
        lon = -180
    sw_lat = math.floor(lat / STEP) * STEP
    sw_lon = math.floor(lon / STEP) * STEP
    return float(sw_lat), float(sw_lon)


def _get(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DrainSense-research/0.1"})
    last = None
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after retries: {url}") from last


def download_gpkg() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "Global_Flood_Records.gpkg"
    if not path.exists():
        print("downloading Global Flood Records gpkg ...", flush=True)
        path.write_bytes(_get(GPKG_URL, timeout=300))
    return path


def cause_kept(cause: str) -> bool:
    text = (cause or "").strip().lower()
    if not text or text in {"0", "see notes"}:
        return False
    if any(token in text for token in EXCLUDE_TOKENS):
        return False
    return any(token in text for token in INCLUDE_TOKENS)


def load_flood_counts(gpkg: Path) -> tuple[pd.DataFrame, dict]:
    con = sqlite3.connect(gpkg)
    rows = con.execute(
        """
        select f.BeginDate, f.MainCause, r.minx, r.maxx, r.miny, r.maxy
        from combined_floods f
        join rtree_combined_floods_geom r on r.id = f.fid
        """
    ).fetchall()
    con.close()
    dropped_cause = dropped_box = dropped_coord = kept = 0
    counts: dict[tuple[str, int], int] = {}
    for began, cause, minx, maxx, miny, maxy in rows:
        if not cause_kept(cause or ""):
            dropped_cause += 1
            continue
        if maxx - minx > MAX_BOX_DEG or maxy - miny > MAX_BOX_DEG:
            dropped_box += 1
            continue
        lat = (miny + maxy) / 2
        lon = (minx + maxx) / 2
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            dropped_coord += 1
            continue
        year = int(str(began)[:4])
        sw_lat, sw_lon = sw_corner(lat, lon)
        if sw_lat not in LAT_SW or sw_lon not in LON_SW:
            dropped_coord += 1
            continue
        key = (cell_id(sw_lat, sw_lon), year)
        counts[key] = counts.get(key, 0) + 1
        kept += 1
    frame = pd.DataFrame(
        [{"cell_id": key[0], "year": key[1], "n_events": value} for key, value in counts.items()]
    )
    filters = {
        "events_in_gpkg": len(rows),
        "events_kept": kept,
        "dropped_cause": dropped_cause,
        "dropped_bbox_over_15deg": dropped_box,
        "dropped_outside_grid_or_coord": dropped_coord,
        "cell_years_with_a_kept_event": int(len(frame)),
    }
    print("flood filters", filters, flush=True)
    return frame, filters


def grid_points() -> list[dict]:
    points = []
    for sw_lat in LAT_SW:
        for sw_lon in LON_SW:
            points.append({
                "cell_id": cell_id(sw_lat, sw_lon),
                "sw_lat": float(sw_lat),
                "sw_lon": float(sw_lon),
                "center_lat": sw_lat + STEP / 2,
                "center_lon": sw_lon + STEP / 2,
            })
    return points


def fetch_elevation(points: list[dict]) -> list[dict]:
    kept = []
    elev_cache = CACHE / "elevation.json"
    cached = json.loads(elev_cache.read_text()) if elev_cache.exists() else {}
    for start in range(0, len(points), 100):
        batch = points[start:start + 100]
        missing = [p for p in batch if p["cell_id"] not in cached]
        if missing:
            query = urllib.parse.urlencode({
                "latitude": ",".join(str(p["center_lat"]) for p in missing),
                "longitude": ",".join(str(p["center_lon"]) for p in missing),
            })
            payload = json.loads(_get(f"{ELEVATION_URL}?{query}"))
            for point, elev in zip(missing, payload["elevation"]):
                cached[point["cell_id"]] = elev
            elev_cache.parent.mkdir(parents=True, exist_ok=True)
            elev_cache.write_text(json.dumps(cached))
        elevations = [cached[p["cell_id"]] for p in batch]
        for point, elev in zip(batch, elevations):
            if elev is None or float(elev) <= 0:
                continue
            kept.append({**point, "elev_m": float(elev)})
        print(f"elevation {start + len(batch)}/{len(points)} land {len(kept)}", flush=True)
    return kept


def _year_stats(days: list[str], rain: list[float], year: int) -> dict | None:
    values = []
    for stamp, amount in zip(days, rain):
        if not stamp.startswith(f"{year}-"):
            continue
        if amount is None or (isinstance(amount, float) and math.isnan(amount)):
            continue
        values.append(float(amount))
    if len(values) < MIN_DAYS:
        return None
    series = np.asarray(values, dtype=float)
    roll = pd.Series(series).rolling(7).sum().max()
    return {
        "annual_precip_mm": float(series.sum()),
        "p95_daily_mm": float(np.percentile(series, 95)),
        "max_daily_mm": float(series.max()),
        "max_7day_mm": float(roll) if pd.notna(roll) else float(series.max()),
        "wet_day_frac": float(np.mean(series >= WET_DAY_MM)),
        "heavy_day_count": int(np.sum(series >= HEAVY_RAIN_MM)),
        "days_used": int(len(series)),
    }


def fetch_precip(cells: list[dict]) -> list[dict]:
    """One row per cell per feature year, 2013-2023 inclusive."""
    rows = []
    years = list(range(FEATURE_START, FEATURE_END + 1))
    precip_cache = CACHE / "precip"
    precip_cache.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(cells), BATCH):
        batch = cells[start:start + BATCH]
        cache_file = precip_cache / f"batch_{start}.json"
        if cache_file.exists():
            payload = json.loads(cache_file.read_text())
        else:
            query = urllib.parse.urlencode({
                "latitude": ",".join(f"{c['center_lat']:.4f}" for c in batch),
                "longitude": ",".join(f"{c['center_lon']:.4f}" for c in batch),
                "start_date": f"{FEATURE_START}-01-01",
                "end_date": f"{FEATURE_END}-12-31",
                "daily": "precipitation_sum",
                "timezone": "UTC",
            })
            payload = json.loads(_get(f"{ARCHIVE_URL}?{query}"))
            cache_file.write_text(json.dumps(payload))
        if isinstance(payload, dict):
            payload = [payload]
        for cell, block in zip(batch, payload):
            daily = block["daily"]
            for year in years:
                stats = _year_stats(daily["time"], daily["precipitation_sum"], year)
                if stats is None:
                    continue
                rows.append({**cell, "feature_year": year, **stats})
        print(f"precip cells {min(start + BATCH, len(cells))}/{len(cells)} rows {len(rows)}", flush=True)
        time.sleep(0.2)
    return rows


def assemble(feature_rows: list[dict], floods: pd.DataFrame) -> pd.DataFrame:
    features = pd.DataFrame(feature_rows)
    label_years = list(TRAIN_YEARS) + list(TEST_YEARS) + [OUTLOOK_YEAR]
    pieces = []
    for year in label_years:
        block = features[features.feature_year == year - 1].copy()
        block["label_year"] = year
        pieces.append(block)
    frame = pd.concat(pieces, ignore_index=True)
    if floods.empty:
        frame["n_events"] = 0
    else:
        labelled = floods.rename(columns={"year": "label_year"})
        frame = frame.merge(labelled, on=["cell_id", "label_year"], how="left")
        frame["n_events"] = frame["n_events"].fillna(0).astype(int)
    frame["flooded"] = np.where(frame.label_year == OUTLOOK_YEAR, np.nan, (frame.n_events > 0).astype(float))
    columns = [
        "cell_id", "sw_lat", "sw_lon", "center_lat", "center_lon", "elev_m",
        "label_year", "feature_year", "n_events", "flooded", "days_used", *FEATURE_COLUMNS[1:],
    ]
    # elev_m is already in FEATURE_COLUMNS[0]; avoid duplicate
    columns = [
        "cell_id", "sw_lat", "sw_lon", "center_lat", "center_lon",
        "label_year", "feature_year", "n_events", "flooded", "days_used", *FEATURE_COLUMNS,
    ]
    return frame[columns].sort_values(["cell_id", "label_year"]).reset_index(drop=True)


def _round(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), 6)


def _scores(y_true, proba, threshold: float) -> dict:
    pred = proba >= threshold
    out = {
        "roc_auc": _round(roc_auc_score(y_true, proba)) if len(set(y_true)) > 1 else None,
        "f1": _round(f1_score(y_true, pred, zero_division=0)),
        "precision": _round(precision_score(y_true, pred, zero_division=0)),
        "recall": _round(recall_score(y_true, pred, zero_division=0)),
        "accuracy": _round(accuracy_score(y_true, pred)),
        "brier": _round(brier_score_loss(y_true, np.clip(proba, 0, 1))),
        "n": int(len(y_true)),
        "positives": int(np.sum(y_true)),
        "threshold": _round(threshold),
    }
    return out


def _year_bootstrap_auc(y_true, proba, years, draws: int = 1000) -> list[float] | None:
    if len(set(y_true)) < 2:
        return None
    rng = np.random.default_rng(0)
    unique = np.unique(years)
    groups = {year: np.flatnonzero(years == year) for year in unique}
    aucs = []
    for _ in range(draws):
        chosen = rng.choice(unique, len(unique))
        idx = np.concatenate([groups[year] for year in chosen])
        if len(set(y_true[idx])) < 2:
            continue
        aucs.append(roc_auc_score(y_true[idx], proba[idx]))
    if not aucs:
        return None
    return [_round(np.percentile(aucs, 5)), _round(np.percentile(aucs, 95))]


def _positive_proba(model, X) -> np.ndarray:
    proba = model.predict_proba(X)
    classes = list(model.classes_ if hasattr(model, "classes_") else model.named_steps["logisticregression"].classes_)
    if 1 not in classes:
        return np.zeros(len(X))
    return proba[:, classes.index(1)]


def _make_lr(C: float):
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=C, max_iter=2000, random_state=0),
    )


def _oof(C: float, X, y, years) -> np.ndarray:
    out = np.full(len(y), np.nan)
    for year in sorted(set(np.asarray(years).tolist())):
        test = years == year
        if test.all() or (~test).sum() == 0 or len(set(y[~test])) < 2:
            continue
        out[test] = _positive_proba(_make_lr(C).fit(X[~test], y[~test]), X[test])
    return out


def _best_threshold(y, proba) -> float:
    grid = np.linspace(0.02, 0.8, 79)
    scores = [f1_score(y, proba >= t, zero_division=0) for t in grid]
    return float(grid[int(np.argmax(scores))])


def _fit_chosen(X, y, years):
    usable = []
    for C in C_GRID:
        oof = _oof(C, X, y, years)
        mask = np.isfinite(oof)
        if mask.sum() < 10 or len(set(y[mask])) < 2:
            continue
        usable.append((brier_score_loss(y[mask], np.clip(oof[mask], 0, 1)), C, oof))
    if not usable:
        raise RuntimeError("year-block CV produced no usable fold")
    _, C, oof = min(usable, key=lambda item: item[0])
    mask = np.isfinite(oof)
    threshold = _best_threshold(y[mask], oof[mask])
    model = _make_lr(C).fit(X, y)
    return model, C, threshold, oof


def _pack_model(model) -> dict:
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]
    return {
        "feature_columns": FEATURE_COLUMNS,
        "mean": [round(float(v), 8) for v in scaler.mean_],
        "scale": [round(float(v), 8) for v in scaler.scale_],
        "coef": [round(float(v), 8) for v in clf.coef_[0]],
        "intercept": round(float(clf.intercept_[0]), 8),
    }


def linear_shap(model, X_explain: np.ndarray) -> tuple[np.ndarray, float]:
    """Exact interventional SHAP for a standardized linear model, log-odds scale.

    phi_j = (z_j - mean(z_j)) * coef_j, with z the StandardScaler output.
    shap.LinearExplainer returns this same quantity; the test suite checks it.
    """
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]
    z = scaler.transform(X_explain)
    coef = clf.coef_[0]
    # Training rows were standardized to mean 0, so the background mean in z-space is 0.
    values = (z - 0.0) * coef
    expected = float(clf.intercept_[0])
    return values, expected


def shap_library_values(model, X_train: np.ndarray, X_explain: np.ndarray) -> tuple[np.ndarray, float]:
    """shap.LinearExplainer on the standardized logistic model.

    Rainfall features move together, so this correlation-aware attribution
    shares credit across them. It is the value the map stores. linear_shap is
    the independent-features formula, kept so the gap can be reported.
    """
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]
    explainer = shap.LinearExplainer(clf, scaler.transform(X_train))
    values = explainer.shap_values(scaler.transform(X_explain))
    if hasattr(values, "values"):
        values = values.values
    if isinstance(values, list):
        values = values[-1]
    expected = explainer.expected_value
    if isinstance(expected, (list, np.ndarray)):
        expected = float(np.asarray(expected).reshape(-1)[0])
    return np.asarray(values, dtype=float), float(expected)


def _per_year(y, proba, years) -> list[dict]:
    rows = []
    for year in sorted(set(years.tolist())):
        mask = years == year
        rows.append({
            "year": int(year),
            "n": int(mask.sum()),
            "observed_positive_rate": _round(float(np.mean(y[mask]))),
            "mean_predicted_probability": _round(float(np.mean(proba[mask]))),
        })
    return rows


def fit_frame(frame: pd.DataFrame) -> dict:
    """Fit from the committed feature table. No network."""
    work = frame.copy()
    work["flooded"] = pd.to_numeric(work["flooded"], errors="coerce")
    labelled = work[work.flooded.notna()].copy()
    labelled["flooded"] = labelled["flooded"].astype(int)
    labelled["label_year"] = labelled["label_year"].astype(int)
    labelled["feature_year"] = labelled["feature_year"].astype(int)

    if not (labelled.feature_year == labelled.label_year - 1).all():
        raise AssertionError("leakage: a feature year is not the year before its label")
    train = labelled[labelled.label_year.isin(TRAIN_YEARS)].copy()
    test = labelled[labelled.label_year.isin(TEST_YEARS)].copy()
    if set(train.feature_year).intersection(set(test.feature_year)):
        raise AssertionError("leakage: train and test share a feature year")
    if train.label_year.max() >= test.label_year.min():
        raise AssertionError("leakage: training labels are not strictly before test labels")

    X_train = train[FEATURE_COLUMNS].to_numpy(dtype=float)
    y_train = train.flooded.to_numpy(dtype=int)
    years_train = train.label_year.to_numpy(dtype=int)
    X_test = test[FEATURE_COLUMNS].to_numpy(dtype=float)
    y_test = test.flooded.to_numpy(dtype=int)
    years_test = test.label_year.to_numpy(dtype=int)

    model, C, threshold, oof = _fit_chosen(X_train, y_train, years_train)
    proba = _positive_proba(model, X_test)
    temporal = _scores(y_test, proba, threshold)
    temporal["roc_auc_year_bootstrap_90ci"] = _year_bootstrap_auc(y_test, proba, years_test)
    temporal["by_year"] = _per_year(y_test, proba, years_test)
    oof_mask = np.isfinite(oof)
    temporal["oof_train_roc_auc"] = (
        _round(roc_auc_score(y_train[oof_mask], oof[oof_mask])) if oof_mask.any() and len(set(y_train[oof_mask])) > 1 else None
    )

    dummy_prior = DummyClassifier(strategy="prior", random_state=0).fit(X_train, y_train)
    dummy_mf = DummyClassifier(strategy="most_frequent", random_state=0).fit(X_train, y_train)
    prior_proba = _positive_proba(dummy_prior, X_test)
    mf_proba = _positive_proba(dummy_mf, X_test)
    # Hard F1 for the prior dummy uses its own predict(); probability metrics use the constant prior.
    prior_metrics = _scores(y_test, prior_proba, 0.5)
    prior_metrics["f1_at_predict"] = _round(f1_score(y_test, dummy_prior.predict(X_test), zero_division=0))
    mf_metrics = _scores(y_test, mf_proba, 0.5)
    mf_metrics["f1_at_predict"] = _round(f1_score(y_test, dummy_mf.predict(X_test), zero_division=0))

    hold_mask_cells = (train.sw_lon >= SPATIAL_HOLDOUT_LON[0]) & (train.sw_lon < SPATIAL_HOLDOUT_LON[1])
    spatial = _spatial_holdout(train, test)

    outlook = work[work.label_year == OUTLOOK_YEAR].copy()
    X_out = outlook[FEATURE_COLUMNS].to_numpy(dtype=float)
    out_proba = _positive_proba(model, X_out) if len(outlook) else np.array([])
    shap_values, expected = shap_library_values(model, X_train, X_out) if len(outlook) else (np.zeros((0, len(FEATURE_COLUMNS))), 0.0)
    test_shap, test_expected = shap_library_values(model, X_train, X_test)
    decision = model.decision_function(X_test)
    if not np.allclose(test_expected + test_shap.sum(axis=1), decision, atol=1e-4):
        gap = float(np.max(np.abs(test_expected + test_shap.sum(axis=1) - decision)))
        raise AssertionError(f"SHAP values do not add up to the log-odds (max gap {gap})")
    sample = min(40, len(X_test))
    independent, _ = linear_shap(model, X_test[:sample])
    shap_gap = float(np.max(np.abs(independent - test_shap[:sample])))
    mean_abs = {
        name: _round(float(np.mean(np.abs(test_shap[:, i]))))
        for i, name in enumerate(FEATURE_COLUMNS)
    }

    train_hits = (
        train[train.flooded == 1].groupby("cell_id").label_year.nunique().rename("dfo_positive_years_2014_2018")
    )
    spec = _pack_model(model)
    spec.update({"C": C, "threshold": round(threshold, 4), "class_weight": None, "random_state": 0})

    scores = _scores_geojson(outlook, out_proba, shap_values, expected, train_hits, spec["threshold"])
    metrics = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "model/train.py",
        "heavy_day_mm": HEAVY_RAIN_MM,
        "heavy_day_source": "backend/scoring/heuristics.py HEAVY_RAIN_MM",
        "target": "At least one kept DFO large-flood bounding-box center in the 5-degree cell during the label year",
        "feature_columns": FEATURE_COLUMNS,
        "split": {
            "train_label_years": list(TRAIN_YEARS),
            "test_label_years": list(TEST_YEARS),
            "outlook_label_year": OUTLOOK_YEAR,
            "feature_rule": "precipitation statistics from label_year - 1 only, plus static elevation",
            "train_feature_years": sorted(int(y) for y in set(train.feature_year)),
            "test_feature_years": sorted(int(y) for y in set(test.feature_year)),
            "feature_years_disjoint": True,
        },
        "n_cells": int(labelled.cell_id.nunique()),
        "n_train_rows": int(len(train)),
        "n_test_rows": int(len(test)),
        "train_positive_rate": _round(float(y_train.mean())),
        "test_positive_rate": _round(float(y_test.mean())),
        "model": spec,
        "temporal_test": temporal,
        "dummy_prior": prior_metrics,
        "dummy_most_frequent": mf_metrics,
        "spatial_holdout": spatial,
        "mean_abs_shap_test_log_odds": mean_abs,
        "shap": {
            "method": "shap.LinearExplainer",
            "link": "identity on the logistic log-odds after StandardScaler",
            "background": "all training rows, standardized",
            "additivity": "sum of SHAP values plus expected_value matches decision_function within 1e-4",
            "max_abs_diff_vs_independent_formula_on_40_test_rows": round(shap_gap, 6),
            "independent_formula": "phi_j = z_j * coef_j. Rainfall features are correlated, so this shares credit differently from LinearExplainer.",
            "rows_compared": sample,
        },
        "sources": {
            "floods": {
                "name": "Global Flood Records",
                "publisher": "DFO Flood Observatory",
                "zenodo": "https://zenodo.org/records/19288171",
                "version": "0.9.0",
                "license": "CC-BY-4.0",
                "accessed": "2026-10-23",
                "geometry": "bounding-box center from the GeoPackage rtree, not a surveyed point",
            },
            "rainfall": {
                "name": "Open-Meteo Historical Weather API",
                "url": ARCHIVE_URL,
                "variable": "daily precipitation_sum",
                "docs": "https://open-meteo.com/en/docs/historical-weather-api",
                "license": "CC-BY-4.0",
                "note": "Open-Meteo documents this archive as reanalysis (ERA5 / ERA5-Land blend). DrainSense did not download CHIRPS or ERA5 files itself.",
            },
            "elevation": {
                "name": "Open-Meteo Elevation API",
                "url": ELEVATION_URL,
                "note": "Open-Meteo documents a 90 m digital elevation model. Cells with elevation 0 were treated as ocean and dropped.",
            },
        },
        "grid": {"step_degrees": STEP, "lat_sw": [LAT_SW[0], LAT_SW[-1]], "lon_sw": [LON_SW[0], LON_SW[-1]]},
        "spatial_holdout_definition": {
            "sw_lon_from_inclusive": SPATIAL_HOLDOUT_LON[0],
            "sw_lon_to_exclusive": SPATIAL_HOLDOUT_LON[1],
            "train": "other cells, label years 2014-2018",
            "test": "holdout cells, label years 2019-2023",
            "note": "A second model. The map uses the model trained on every cell's 2014-2018 labels.",
        },
        "train_feature_medians": {name: _round(float(train[name].median())) for name in FEATURE_COLUMNS},
        "cells_in_spatial_holdout_train_rows": int(hold_mask_cells.sum()),
        "limitations": LIMITATIONS,
    }
    return {"metrics": metrics, "scores": scores, "model": model}


def _spatial_holdout(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    def block(frame):
        return (frame.sw_lon >= SPATIAL_HOLDOUT_LON[0]) & (frame.sw_lon < SPATIAL_HOLDOUT_LON[1])

    tr = train.loc[~block(train)]
    te = test.loc[block(test)]
    info = {"train_rows": int(len(tr)), "test_rows": int(len(te)), "test_positives": int(te.flooded.sum()) if len(te) else 0}
    if len(tr) < 50 or len(te) < 20 or tr.flooded.nunique() < 2 or te.flooded.nunique() < 2:
        info["status"] = "not_scored"
        info["reason"] = "A split had fewer than two classes or too few rows."
        return info
    model, C, threshold, _ = _fit_chosen(
        tr[FEATURE_COLUMNS].to_numpy(float), tr.flooded.to_numpy(int), tr.label_year.to_numpy(int)
    )
    proba = _positive_proba(model, te[FEATURE_COLUMNS].to_numpy(float))
    scored = _scores(te.flooded.to_numpy(int), proba, threshold)
    scored["status"] = "scored"
    scored["C"] = C
    scored.update(info)
    return scored


def _scores_geojson(outlook, proba, shap_values, expected, train_hits, threshold) -> dict:
    features = []
    order = np.argsort(-proba) if len(proba) else []
    rank_of = {int(idx): rank + 1 for rank, idx in enumerate(order)}
    for pos, (_, row) in enumerate(outlook.reset_index(drop=True).iterrows()):
        sw_lat, sw_lon = float(row.sw_lat), float(row.sw_lon)
        ring = [
            [sw_lon, sw_lat],
            [sw_lon + STEP, sw_lat],
            [sw_lon + STEP, sw_lat + STEP],
            [sw_lon, sw_lat + STEP],
            [sw_lon, sw_lat],
        ]
        shap_map = {name: round(float(shap_values[pos, i]), 6) for i, name in enumerate(FEATURE_COLUMNS)}
        top = max(shap_map, key=lambda name: abs(shap_map[name])) if shap_map else None
        try:
            hits = int(train_hits.loc[row.cell_id])
        except (KeyError, TypeError):
            hits = 0
        properties = {
            "cell_id": row.cell_id,
            "center_lat": round(float(row.center_lat), 4),
            "center_lon": round(float(row.center_lon), 4),
            "sw_lat": sw_lat,
            "sw_lon": sw_lon,
            "elev_m": round(float(row.elev_m), 2),
            "probability": round(float(proba[pos]), 6),
            "above_threshold": bool(proba[pos] >= threshold),
            "outlook_year": OUTLOOK_YEAR,
            "feature_year": int(row.feature_year),
            "rank": rank_of[pos],
            "shap_log_odds": shap_map,
            "expected_log_odds": round(float(expected), 6),
            "top_shap_feature": top,
            "dfo_positive_years_2014_2018": hits,
            "features": {name: round(float(row[name]), 6) for name in FEATURE_COLUMNS},
            "data_origin": "model",
            "label_status": "unlabelled outlook; 2023 rainfall features, no 2024 DFO label in this run",
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": properties,
        })
    return {"type": "FeatureCollection", "features": features}


def write_outputs(result: dict, filters: dict | None) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    metrics = result["metrics"]
    if filters is not None:
        metrics["flood_filters"] = filters
    elif "flood_filters" not in metrics:
        metrics["flood_filters"] = {"note": "refit from the committed feature table; event filters were applied when the table was built"}
    METRICS_PATH.write_text(json.dumps(metrics, indent=2) + "\n")
    SCORES_PATH.write_text(json.dumps(result["scores"]) + "\n")
    temporal = metrics["temporal_test"]
    print(
        f"temporal AUC {temporal['roc_auc']} F1 {temporal['f1']} Brier {temporal['brier']} "
        f"dummy prior Brier {metrics['dummy_prior']['brier']}",
        flush=True,
    )
    print(f"wrote {METRICS_PATH} and {SCORES_PATH}", flush=True)


def download_and_build() -> pd.DataFrame:
    gpkg = download_gpkg()
    floods, filters = load_flood_counts(gpkg)
    cells = fetch_elevation(grid_points())
    print(f"land cells {len(cells)}", flush=True)
    rows = fetch_precip(cells)
    frame = assemble(rows, floods)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(FEATURE_CSV, index=False)
    filters_path = ARTIFACTS / "flood_filters.json"
    filters_path.write_text(json.dumps(filters, indent=2) + "\n")
    print(f"wrote {FEATURE_CSV} rows {len(frame)}", flush=True)
    return frame


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="rebuild the feature table from Zenodo and Open-Meteo")
    parser.add_argument("--refit", action="store_true", help="refit from the committed CSV only")
    args = parser.parse_args(argv)
    if args.download and args.refit:
        parser.error("choose one of --download or --refit")
    if args.download or not FEATURE_CSV.exists():
        frame = download_and_build()
        filters = json.loads((ARTIFACTS / "flood_filters.json").read_text())
    else:
        frame = pd.read_csv(FEATURE_CSV)
        filters = None
        if (ARTIFACTS / "flood_filters.json").exists():
            filters = json.loads((ARTIFACTS / "flood_filters.json").read_text())
    result = fit_frame(frame)
    write_outputs(result, filters)


if __name__ == "__main__":
    main()
