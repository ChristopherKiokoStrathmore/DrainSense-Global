#!/usr/bin/env python3
"""CatBoost flood-impact regressor for DrainSense, as specified in the executive brief.

Target
    "Flood impact index" from the Global Flood Records table (DFO). It is the DFO
    magnitude, log10(days x severity x area_km2), rounded to 0.1, where days counts
    both the start and the end date. The formula reproduces the column on 5502 of
    5503 rows, so a model fed area and duration is relearning arithmetic.

Inputs
    Event fields from data/Enriched_Global_Flood_Records-v2.csv. Weather is NOT
    taken from that file: its 14 weather columns snap every event to one of 376
    points and disagree with reanalysis, so they are kept only for data_checks().
    Weather comes from model/weather.py instead: Open-Meteo (ERA5 / ERA5-Land)
    daily series at the centre of each event's mapped footprint in the DFO
    GeoPackage, for the 14 days before the start and up to 45 days of the event.

Variants
    brief     Log_Area, Event_Duration_Days, all observed weather features and
              continent: the brief's structure, with real weather. It is the
              model saved as catboost_flood_model.cbm.
    no_size   No area, duration or severity, and no weather summed over the event.
              Antecedent rain and soil moisture, the first three days of the
              event, elevation, place, month and cause: what is known by day 3.

Split
    Fit on start years up to 2015, pick the iteration on 2016-2018, refit on
    1985-2018 with that count, and score 2019-2024. The served models are refit on
    every row with the same count.

    python -m model.weather            # fetch reanalysis into model/.cache/weather/ (network)
    python -m model.impact --rebuild   # read data/ and the cache, rewrite the event table, refit
    python -m model.impact             # refit from the committed event table
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split

from model.weather import WX_COLUMNS, footprints, weather_table

REPO = Path(__file__).resolve().parents[1]
SOURCE_CSV = REPO / "data" / "Enriched_Global_Flood_Records-v2.csv"
IMPACT_DIR = REPO / "model" / "artifacts" / "impact"
EVENT_CSV = IMPACT_DIR / "event_features.csv"
METRICS_PATH = IMPACT_DIR / "impact_metrics.json"
PREDICTIONS_PATH = IMPACT_DIR / "test_predictions.csv"
MODEL_PATHS = {
    "brief": IMPACT_DIR / "catboost_flood_model.cbm",
    "no_size": IMPACT_DIR / "catboost_flood_model_no_size.cbm",
}

TARGET = "Flood_Impact_Index"
FIT_END_YEAR = 2015
VALID_YEARS = (2016, 2017, 2018)
TEST_START_YEAR = 2019
SEED = 42

# The brief's hyperparameters. iterations is the ceiling for the validation pass.
PARAMS = {
    "iterations": 1200,
    "depth": 7,
    "learning_rate": 0.03,
    "l2_leaf_reg": 5.0,
    "subsample": 0.8,
    "bootstrap_type": "MVS",
    "loss_function": "RMSE",
    "random_seed": SEED,
    "allow_writing_files": False,
    "verbose": 0,
}

# The enrichment's weather columns. Not model inputs; data_checks() measures them.
ENRICHED_COLUMNS = [
    "Total_Precipitation_mm",
    "Max_24h_Precipitation_mm",
    "Total_Liquid_Rain_mm",
    "Snow_Melt_Equiv_mm",
    "Antecedent_7Day_Precip_mm",
    "Estimated_Surface_Runoff_mm",
    "Mean_Temp_2m_C",
    "Max_Temp_2m_C",
    "Min_Temp_2m_C",
    "Max_Wind_Gust_10m_kmh",
    "Min_Sea_Level_Pressure_hPa",
    "Mean_Relative_Humidity_pct",
    "Topsoil_Moisture_0_7cm_m3m3",
    "Cumulative_ET0_Evapotranspiration_mm",
]
# Observed weather over the whole event window. Sums here grow with duration.
WX_EVENT_WINDOW = [
    "Wx_Precip_Event_mm",
    "Wx_Rain_Event_mm",
    "Wx_Snowfall_Event_cm",
    "Wx_Precip_Max_1d_mm",
    "Wx_ET0_Event_mm",
    "Wx_Temp_Max_C",
    "Wx_RH_Mean_pct",
    "Wx_Soil_Moisture_Event_Max_m3m3",
]
# Known by the third day of the event: before it, and its first three days.
WX_BY_DAY_3 = [c for c in WX_COLUMNS if c not in WX_EVENT_WINDOW]

VARIANTS = {
    "brief": {
        "numeric": ["Log_Area", "Event_Duration_Days", *WX_COLUMNS],
        "categorical": ["Continent_Region"],
        "description": (
            "The brief's model with observed weather. Area and duration are two of the three terms "
            "in the formula that defines the target, so its error is mostly the severity term it is not given."
        ),
    },
    "no_size": {
        "numeric": [*WX_BY_DAY_3, "Footprint_Lat", "Footprint_Lon", "Start_Month"],
        "categorical": ["Continent_Region", "Cause_Group"],
        "description": (
            "No area, duration, severity, or weather summed over the event. Observed weather from "
            "the 14 days before and the first 3 days, plus place, month and cause: the skill left "
            "once the formula inputs are taken away."
        ),
    },
}

EVENT_COLUMNS = [
    "Event_ID", "Start_Date", "End_Date", "Start_Year", "Start_Month", "Country",
    "ISO_Country_Code", "Continent_Region", "Koppen_Climate_Zone", "Enriched_Lat", "Enriched_Lon",
    "Main_Cause", "Cause_Group", "Area_km2", "Log_Area", "Days_Inclusive",
    "Event_Duration_Days", "Severity", *ENRICHED_COLUMNS, TARGET,
]
FOOTPRINT_COLUMNS = ["Footprint_Lat", "Footprint_Lon", "Footprint_Span_Deg"]

# Checked in order. "extra-tropical" has to win over "tropical", and a dam break in
# heavy rain is a dam break.
CAUSE_RULES = (
    ("dam_or_levee", r"dam|levee|levy|glacial|glacier"),
    ("surge_or_tide", r"surge|tide|tidal|tsunami"),
    ("extratropical_storm", r"extra-tropical|winter storm"),
    ("tropical_cyclone", r"tropi?c?al|typhoon|hurricane|cyclone|cylone"),
    ("monsoon", r"mon?soon|monoonal"),
    ("snowmelt_or_ice", r"snow|ice jam|ice"),
    ("torrential_rain", r"torr?ential"),
    ("heavy_rain", r"ra(i)?n|rainy"),
    ("other_storm", r"storm"),
)

LIMITATIONS = [
    "The target is the DFO magnitude, log10(days x severity x area_km2), rounded to 0.1. Given those three numbers the formula is exact. The brief model is given two of them and learns the rest; it is not forecasting impact.",
    "Weather is Open-Meteo reanalysis (ERA5 / ERA5-Land, roughly 9-30 km cells) at one point: the centre of the event's bounding box in the DFO GeoPackage. Large footprints get one point for the whole area, and the centre can fall outside the flooded polygon. The enriched CSV's own weather columns are not used: they snap events to 376 points and disagree with reanalysis.",
    "Area, duration and severity are only known once a flood is over and mapped. The brief model cannot be used before or during an event.",
    "The no_size variant drops the formula inputs and the weather summed over the event. It still sees the first three days, which for one- and two-day events is the whole event, so it is a by-day-3 estimate, not a forecast.",
    "DFO records large events from news, agencies and satellites. Small and unreported floods are missing, and reporting effort differs by country and decade.",
    "The temporal test scores 2019-2024 start years. The same countries and river basins appear on both sides of the split, so it measures a later period at familiar places.",
    "This model does not feed the DrainSense map. The map score is the logistic model in model/train.py.",
]


def dfo_magnitude(days, severity, area_km2):
    """DFO flood magnitude. days counts the start and end dates both."""
    return np.log10(np.asarray(days, dtype=float) * np.asarray(severity, dtype=float)
                    * np.asarray(area_km2, dtype=float))


def cause_group(cause) -> str:
    text = str(cause).strip().lower()
    for name, pattern in CAUSE_RULES:
        if re.search(pattern, text):
            return name
    return "other"


def load_records(path: Path = SOURCE_CSV) -> pd.DataFrame:
    """Read the enriched DFO table and return the event table the model trains on."""
    raw = pd.read_csv(path)
    start = pd.to_datetime(raw["Clean_Start_Date"], format="%Y-%m-%d")
    end = pd.to_datetime(raw["Clean_End_Date"], format="%Y-%m-%d")
    area = pd.to_numeric(raw["Area (km²)"].astype(str).str.replace(",", "", regex=False))
    frame = pd.DataFrame({
        "Event_ID": raw["﹟"].astype(int),
        "Start_Date": start.dt.strftime("%Y-%m-%d"),
        "End_Date": end.dt.strftime("%Y-%m-%d"),
        "Start_Year": start.dt.year,
        "Start_Month": start.dt.month,
        "Country": raw["Country"].astype(str),
        "ISO_Country_Code": raw["ISO_Country_Code"].fillna("").astype(str),
        "Continent_Region": raw["Continent_Region"].fillna("Unknown").astype(str),
        "Koppen_Climate_Zone": raw["Koppen_Climate_Zone"].fillna("Unknown").astype(str),
        # The enrichment's snapped point, not where the flood was. Kept for data_checks().
        "Enriched_Lat": raw["Latitude"].astype(float),
        "Enriched_Lon": raw["Longitude"].astype(float),
        "Main_Cause": raw["Main Cause"].fillna("").astype(str),
        "Area_km2": area.astype(float),
        # Event_Duration_Days in the source is end - start, floored at 1. The
        # formula needs the inclusive count, which is one more except on same-day events.
        "Days_Inclusive": (end - start).dt.days + 1,
        "Event_Duration_Days": raw["Event_Duration_Days"].astype(int),
        "Severity": raw["Severity"].astype(float),
        TARGET: raw["Flood impact index"].astype(float),
    })
    frame["Cause_Group"] = frame["Main_Cause"].map(cause_group)
    frame["Log_Area"] = np.log10(frame["Area_km2"])
    for column in ENRICHED_COLUMNS:
        frame[column] = raw[column].astype(float)
    return frame[EVENT_COLUMNS].sort_values("Event_ID").reset_index(drop=True)


def build_events(path: Path = SOURCE_CSV) -> pd.DataFrame:
    """The event table: CSV event fields, DFO footprint centres, and cached observed weather."""
    from model.train import download_gpkg  # noqa: PLC0415  (pulls in the map model's imports)

    gpkg = download_gpkg()
    frame = load_records(path)
    frame = frame.merge(footprints(gpkg), on="Event_ID", how="left", validate="one_to_one")
    if frame["Footprint_Lat"].isna().any():
        raise RuntimeError("some events have no polygon in the DFO GeoPackage")
    frame = frame.merge(weather_table(frame), on="Event_ID", how="left", validate="one_to_one")
    return frame[[*EVENT_COLUMNS[:-1], *FOOTPRINT_COLUMNS, *WX_COLUMNS, TARGET]]


def read_events(path: Path = EVENT_CSV) -> pd.DataFrame:
    """Read the committed event table. Namibia's ISO code is the string NA, not a gap."""
    return pd.read_csv(path, keep_default_na=False, na_values={"Area_km2": [""]})


def _round(value, places: int = 4):
    if value is None:
        return None
    return round(float(value), places)


def _scores(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    err = np.asarray(y_pred, dtype=float) - y_true
    total = ((y_true - y_true.mean()) ** 2).sum()
    return {
        "rmse": _round(np.sqrt((err ** 2).mean())),
        "mae": _round(np.abs(err).mean()),
        "r2": _round(1 - (err ** 2).sum() / total),
        "n": int(len(y_true)),
    }


def split_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    year = frame["Start_Year"]
    return {
        "fit": year <= FIT_END_YEAR,
        "valid": year.isin(VALID_YEARS),
        "train": year < TEST_START_YEAR,
        "test": year >= TEST_START_YEAR,
    }


def feature_columns(variant: str) -> list[str]:
    spec = VARIANTS[variant]
    return [*spec["numeric"], *spec["categorical"]]


def make_pool(frame: pd.DataFrame, variant: str, with_label: bool = True) -> Pool:
    spec = VARIANTS[variant]
    X = frame[feature_columns(variant)].copy()
    for column in spec["categorical"]:
        X[column] = X[column].astype(str)
    label = frame[TARGET].to_numpy() if with_label else None
    return Pool(X, label, cat_features=spec["categorical"])


def data_checks(frame: pd.DataFrame) -> dict:
    """Numbers behind the two warnings: the target is a formula, and the weather is not observed."""
    formula = dfo_magnitude(frame["Days_Inclusive"], frame["Severity"], frame["Area_km2"])
    rounded_match = np.abs(np.round(formula, 1) - frame[TARGET]) < 0.05
    stored = dfo_magnitude(frame["Event_Duration_Days"], frame["Severity"], frame["Area_km2"])

    precip = np.log1p(frame["Total_Precipitation_mm"])
    base = pd.DataFrame({
        "log_days": np.log10(frame["Days_Inclusive"]),
        "abs_lat": frame["Enriched_Lat"].abs(),
    })
    sized = base.assign(log_area=frame["Log_Area"], severity=frame["Severity"])
    r2_base = LinearRegression().fit(base, precip).score(base, precip)
    r2_sized = LinearRegression().fit(sized, precip).score(sized, precip)

    observed = np.log1p(frame["Wx_Precip_Event_mm"])
    fbase = base.assign(abs_lat=frame["Footprint_Lat"].abs())
    fsized = sized.assign(abs_lat=frame["Footprint_Lat"].abs())
    obs_base = LinearRegression().fit(fbase, observed).score(fbase, observed)
    obs_sized = LinearRegression().fit(fsized, observed).score(fsized, observed)
    km = np.hypot(
        frame["Footprint_Lat"] - frame["Enriched_Lat"],
        (frame["Footprint_Lon"] - frame["Enriched_Lon"]) * np.cos(np.radians(frame["Footprint_Lat"])),
    ) * 111.2

    spread_hi = frame["Max_Temp_2m_C"] - frame["Mean_Temp_2m_C"]
    spread_lo = frame["Mean_Temp_2m_C"] - frame["Min_Temp_2m_C"]
    liquid_plus_melt = frame["Total_Liquid_Rain_mm"] + frame["Snow_Melt_Equiv_mm"]
    return {
        "rows": int(len(frame)),
        "formula": "log10(days_inclusive x severity x area_km2)",
        "formula_rmse_vs_index": _round(np.sqrt(((formula - frame[TARGET]) ** 2).mean())),
        "rounding_floor_rmse": _round(0.1 / np.sqrt(12)),
        "rows_where_rounded_formula_equals_index": int(rounded_match.sum()),
        "rows_where_it_does_not": frame.loc[~rounded_match, "Event_ID"].astype(int).tolist(),
        "formula_rmse_with_stored_duration_column": _round(np.sqrt(((stored - frame[TARGET]) ** 2).mean())),
        "same_day_events_where_stored_duration_is_1": int((frame["Days_Inclusive"] == 1).sum()),
        "distinct_event_coordinates": int(frame[["Enriched_Lat", "Enriched_Lon"]].drop_duplicates().shape[0]),
        "log_total_precip_r2_from_days_and_abs_latitude": _round(r2_base),
        "log_total_precip_r2_adding_log_area_and_severity": _round(r2_sized),
        "max_minus_mean_temp_c_range": [_round(spread_hi.min(), 2), _round(spread_hi.max(), 2)],
        "mean_minus_min_temp_c_range": [_round(spread_lo.min(), 2), _round(spread_lo.max(), 2)],
        "total_precip_equals_liquid_plus_snowmelt_max_abs_diff": _round(
            (frame["Total_Precipitation_mm"] - liquid_plus_melt).abs().max(), 6),
        "spearman_topsoil_moisture_vs_days": _round(
            frame["Topsoil_Moisture_0_7cm_m3m3"].corr(frame["Days_Inclusive"], method="spearman")),
        "spearman_cumulative_et0_vs_days": _round(
            frame["Cumulative_ET0_Evapotranspiration_mm"].corr(frame["Days_Inclusive"], method="spearman")),
        "observed": {
            "source": "Open-Meteo archive (ERA5 / ERA5-Land) at the DFO footprint bounding-box centre",
            "distinct_footprint_centres": int(frame[["Footprint_Lat", "Footprint_Lon"]].drop_duplicates().shape[0]),
            "median_km_snapped_point_to_footprint_centre": _round(km.median(), 0),
            "share_snapped_points_over_500km_away": _round((km > 500).mean(), 3),
            "spearman_enriched_vs_observed_event_precip": _round(
                frame["Total_Precipitation_mm"].corr(frame["Wx_Precip_Event_mm"], method="spearman")),
            "log_event_precip_r2_from_days_and_abs_latitude": _round(obs_base),
            "log_event_precip_r2_adding_log_area_and_severity": _round(obs_sized),
            "footprint_span_deg_median": _round(frame["Footprint_Span_Deg"].median(), 2),
            "footprints_wider_than_15_deg": int((frame["Footprint_Span_Deg"] > 15).sum()),
        },
    }


def fit_variant(frame: pd.DataFrame, variant: str, final: bool = True) -> dict:
    """Pick the iteration on 2016-2018, refit on 1985-2018, score 2019-2024, then refit on all rows."""
    masks = split_masks(frame)
    fit, valid = frame[masks["fit"]], frame[masks["valid"]]
    train, test = frame[masks["train"]], frame[masks["test"]]

    picker = CatBoostRegressor(**PARAMS)
    picker.fit(make_pool(fit, variant), eval_set=make_pool(valid, variant), use_best_model=True)
    curve = picker.get_evals_result()["validation"]["RMSE"]
    iterations = picker.get_best_iteration() + 1

    params = {**PARAMS, "iterations": iterations}
    holdout = CatBoostRegressor(**params)
    holdout.fit(make_pool(train, variant))
    test_pool = make_pool(test, variant)
    test_pred = holdout.predict(test_pool)

    shap = holdout.get_feature_importance(test_pool, type="ShapValues")
    columns = feature_columns(variant)
    mean_abs_shap = dict(zip(columns, np.abs(shap[:, :-1]).mean(axis=0)))
    importance = dict(zip(columns, holdout.get_feature_importance()))

    served = None
    if final:
        served = CatBoostRegressor(**params)
        served.fit(make_pool(frame, variant))

    return {
        "variant": variant,
        "iterations": iterations,
        "validation_rmse_first_iteration": _round(curve[0]),
        "validation_rmse_best": _round(min(curve)),
        "train_rmse": _scores(train[TARGET], holdout.predict(make_pool(train, variant)))["rmse"],
        "temporal_test": _scores(test[TARGET], test_pred),
        "feature_importance": {k: _round(v, 3) for k, v in sorted(importance.items(), key=lambda kv: -kv[1])},
        "mean_abs_shap_test": {k: _round(v) for k, v in sorted(mean_abs_shap.items(), key=lambda kv: -kv[1])},
        "test_pred": test_pred,
        "model": served,
    }


def baselines(frame: pd.DataFrame) -> dict:
    """Reference scores on the same 2019-2024 rows."""
    masks = split_masks(frame)
    train, test = frame[masks["train"]], frame[masks["test"]]
    y = test[TARGET]

    def logs(rows):
        return pd.DataFrame({"log_area": rows["Log_Area"], "log_days": np.log10(rows["Days_Inclusive"])})

    linear = LinearRegression().fit(logs(train), train[TARGET])
    formula = dfo_magnitude(test["Days_Inclusive"], test["Severity"], test["Area_km2"])
    return {
        "train_mean": _scores(y, np.full(len(y), train[TARGET].mean())),
        "dfo_formula": _scores(y, formula),
        "dfo_formula_without_severity": _scores(
            y, dfo_magnitude(test["Days_Inclusive"], 1.0, test["Area_km2"])),
        "linear_log_area_log_days": {
            **_scores(y, linear.predict(logs(test))),
            "coefficients": {
                "log_area": _round(linear.coef_[0]),
                "log_days": _round(linear.coef_[1]),
                "intercept": _round(linear.intercept_),
            },
        },
    }


def brief_reproduction(frame: pd.DataFrame) -> dict:
    """The brief's protocol: a random 80/20 split with the best iteration picked on the test rows."""
    train_idx, test_idx = train_test_split(np.arange(len(frame)), test_size=0.2, random_state=SEED)
    train, test = frame.iloc[train_idx], frame.iloc[test_idx]
    model = CatBoostRegressor(**PARAMS)
    model.fit(make_pool(train, "brief"), eval_set=make_pool(test, "brief"), use_best_model=True)
    curve = model.get_evals_result()
    formula = dfo_magnitude(test["Days_Inclusive"], test["Severity"], test["Area_km2"])
    return {
        "protocol": "random 80/20 split (random_state 42), use_best_model on the test rows",
        "caveat": "Choosing the iteration on the rows it is scored on is optimistic.",
        "best_iteration": int(model.get_best_iteration()),
        "test_rmse_first_iteration": _round(curve["validation"]["RMSE"][0]),
        "test_rmse_best": _round(model.get_best_score()["validation"]["RMSE"]),
        "train_rmse_last_iteration": _round(curve["learn"]["RMSE"][-1]),
        "dfo_formula_rmse_same_rows": _scores(test[TARGET], formula)["rmse"],
        "brief_reported": {"test_rmse": 0.0985, "train_rmse": 0.0695, "best_iteration": 1088,
                           "test_rmse_first_iteration": 1.0963},
    }


def fit_all(frame: pd.DataFrame) -> dict:
    masks = split_masks(frame)
    variants = {name: fit_variant(frame, name) for name in VARIANTS}
    test = frame[masks["test"]]
    predictions = test[["Event_ID", "Start_Date", "End_Date", "Country", "ISO_Country_Code",
                        "Continent_Region", "Cause_Group", "Area_km2", "Days_Inclusive",
                        "Severity", TARGET]].copy()
    predictions["dfo_formula"] = np.round(
        dfo_magnitude(test["Days_Inclusive"], test["Severity"], test["Area_km2"]), 4)
    for name, result in variants.items():
        predictions[f"pred_{name}"] = np.round(result["test_pred"], 4)

    train = frame[masks["train"]]
    metrics = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "model/impact.py",
        "source": "data/Enriched_Global_Flood_Records-v2.csv (DFO Global Flood Records plus an external enrichment)",
        "weather_source": (
            "Open-Meteo historical archive (ERA5 / ERA5-Land), CC BY 4.0, daily series at the centre of each "
            "event's footprint in the DFO GeoPackage (Zenodo 19288171): 14 days before the start and up to "
            "45 days of the event. Fetched by model/weather.py."
        ),
        "target": TARGET,
        "target_definition": "DFO magnitude: log10(days_inclusive x severity x area_km2), rounded to 0.1",
        "catboost_params": PARAMS,
        "split": {
            "fit_years": f"{int(frame['Start_Year'].min())}-{FIT_END_YEAR}",
            "validation_years": f"{VALID_YEARS[0]}-{VALID_YEARS[-1]}",
            "train_years": f"{int(frame['Start_Year'].min())}-{TEST_START_YEAR - 1}",
            "test_years": f"{TEST_START_YEAR}-{int(frame['Start_Year'].max())}",
            "n_fit": int(masks["fit"].sum()),
            "n_valid": int(masks["valid"].sum()),
            "n_train": int(masks["train"].sum()),
            "n_test": int(masks["test"].sum()),
        },
        "variants": {
            name: {
                "file": MODEL_PATHS[name].name,
                "description": VARIANTS[name]["description"],
                "numeric_features": VARIANTS[name]["numeric"],
                "categorical_features": VARIANTS[name]["categorical"],
                **{k: v for k, v in result.items() if k not in ("test_pred", "model", "variant")},
            }
            for name, result in variants.items()
        },
        "baselines_temporal_test": baselines(frame),
        "brief_reproduction": brief_reproduction(frame),
        "data_checks": data_checks(frame),
        "categories": {
            "Continent_Region": sorted(frame["Continent_Region"].unique().tolist()),
            "Cause_Group": sorted({name for name, _ in CAUSE_RULES} | {"other"}),
        },
        # The API maps free-text causes with these, so it does not import this module.
        "cause_rules": [list(rule) for rule in CAUSE_RULES],
        "train_feature_ranges": {
            column: [_round(train[column].min()), _round(train[column].max())]
            for column in ["Log_Area", "Event_Duration_Days", "Footprint_Lat", "Footprint_Lon", "Start_Month", *WX_COLUMNS]
        },
        "limitations": LIMITATIONS,
    }
    return {
        "metrics": metrics,
        "models": {name: result["model"] for name, result in variants.items()},
        "predictions": predictions,
    }


def write_outputs(result: dict) -> None:
    IMPACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, model in result["models"].items():
        model.save_model(str(MODEL_PATHS[name]))
    result["predictions"].to_csv(PREDICTIONS_PATH, index=False)
    METRICS_PATH.write_text(json.dumps(result["metrics"], indent=2) + "\n")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rebuild", action="store_true",
                        help=f"rewrite {EVENT_CSV.relative_to(REPO)} from {SOURCE_CSV.relative_to(REPO)}")
    args = parser.parse_args(argv)
    if args.rebuild or not EVENT_CSV.exists():
        IMPACT_DIR.mkdir(parents=True, exist_ok=True)
        build_events(SOURCE_CSV).to_csv(EVENT_CSV, index=False)
    # Always fit from the table as read back, so --rebuild and a refit see the same floats.
    result = fit_all(read_events())
    write_outputs(result)
    card = result["metrics"]
    for name, variant in card["variants"].items():
        print(f"{name:8s} iterations {variant['iterations']:5d}  test {variant['temporal_test']}")
    for name, scores in card["baselines_temporal_test"].items():
        print(f"{name:30s} {scores['rmse']}")
    print("brief reproduction", card["brief_reproduction"]["test_rmse_best"])


if __name__ == "__main__":
    main()
