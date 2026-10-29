"""The committed impact artifacts must be what model/impact.py computes from the committed event table."""
import json

import numpy as np
import pandas as pd
import pytest

from model import weather
from model.impact import (
    METRICS_PATH,
    PREDICTIONS_PATH,
    SOURCE_CSV,
    TARGET,
    VARIANTS,
    WX_EVENT_WINDOW,
    build_events,
    dfo_magnitude,
    fit_variant,
    read_events,
    split_masks,
)


@pytest.fixture(scope="module")
def frame():
    return read_events()


@pytest.fixture(scope="module")
def committed():
    return json.loads(METRICS_PATH.read_text())


def test_target_is_the_dfo_magnitude_formula(frame):
    formula = dfo_magnitude(frame["Days_Inclusive"], frame["Severity"], frame["Area_km2"])
    gap = np.abs(formula - frame[TARGET])
    # Exact after rounding except three ties on a .x5 boundary and event 5183,
    # where the formula gives log10(2) = 0.301 and the table says 0.2.
    assert gap.max() < 0.102
    assert (np.abs(np.round(formula, 1) - frame[TARGET]) < 0.05).sum() >= 5499


def test_split_years_are_ordered_and_disjoint(frame):
    masks = split_masks(frame)
    years = frame["Start_Year"]
    assert years[masks["fit"]].max() < years[masks["valid"]].min()
    assert years[masks["train"]].max() < years[masks["test"]].min()
    assert not (masks["train"] & masks["test"]).any()
    assert (masks["train"] | masks["test"]).all()


def test_refit_matches_committed_brief_metrics(frame, committed):
    fresh = fit_variant(frame, "brief", final=False)
    card = committed["variants"]["brief"]
    assert fresh["iterations"] == card["iterations"]
    assert abs(fresh["temporal_test"]["rmse"] - card["temporal_test"]["rmse"]) < 1e-3
    saved = pd.read_csv(PREDICTIONS_PATH)
    assert np.abs(fresh["test_pred"] - saved["pred_brief"].to_numpy()).max() < 1e-3


def test_committed_predictions_reproduce_the_committed_scores(committed):
    saved = pd.read_csv(PREDICTIONS_PATH)
    assert len(saved) == committed["split"]["n_test"]
    for name, card in committed["variants"].items():
        rmse = np.sqrt(((saved[f"pred_{name}"] - saved[TARGET]) ** 2).mean())
        assert abs(rmse - card["temporal_test"]["rmse"]) < 1e-3
    formula_rmse = np.sqrt(((saved["dfo_formula"] - saved[TARGET]) ** 2).mean())
    assert abs(formula_rmse - committed["baselines_temporal_test"]["dfo_formula"]["rmse"]) < 1e-3


def test_brief_model_does_not_beat_the_simple_references(committed):
    brief = committed["variants"]["brief"]["temporal_test"]["rmse"]
    refs = committed["baselines_temporal_test"]
    assert refs["dfo_formula"]["rmse"] < brief
    assert refs["linear_log_area_log_days"]["rmse"] < brief
    assert committed["variants"]["no_size"]["temporal_test"]["rmse"] < refs["train_mean"]["rmse"]


def _weather_cached(frame) -> bool:
    return SOURCE_CSV.exists() and weather.GPKG.exists() and all(
        weather.cached(i) is not None for i in frame["Event_ID"])


def test_event_table_is_built_from_the_source_csv_and_cached_weather(frame):
    if not _weather_cached(frame):
        pytest.skip("data/ and model/.cache/ are not committed")
    rebuilt = build_events(SOURCE_CSV)
    pd.testing.assert_frame_equal(rebuilt, frame, check_dtype=False)


def test_no_size_sees_nothing_summed_over_the_event():
    no_size = set(VARIANTS["no_size"]["numeric"])
    assert no_size.isdisjoint(WX_EVENT_WINDOW)
    assert no_size.isdisjoint({"Log_Area", "Event_Duration_Days", "Severity"})


def test_weather_windows_split_before_and_during_the_event():
    days = pd.date_range("2020-01-01", periods=20).strftime("%Y-%m-%d").tolist()
    rain = [1.0] * 14 + [10.0, 20.0, 5.0, 40.0, 0.0, 0.0]  # event starts on day 15
    payload = {"elevation": 12.0, "daily": {"time": days, **{name: [0.0] * 20 for name in weather.DAILY}}}
    payload["daily"]["precipitation_sum"] = rain
    payload["daily"]["soil_moisture_0_to_7cm_mean"] = [0.2] * 13 + [0.31] + [0.4] * 6
    out = weather.summarise(payload, "2020-01-15", "2020-01-20")
    assert out["Wx_Precip_Antecedent_7d_mm"] == 7.0
    assert out["Wx_Precip_Antecedent_14d_mm"] == 14.0
    assert out["Wx_Soil_Moisture_Before_m3m3"] == 0.31
    assert out["Wx_Precip_First3d_mm"] == 35.0
    assert out["Wx_Precip_Max_1d_First3d_mm"] == 20.0
    assert out["Wx_Precip_Event_mm"] == 75.0
    assert out["Wx_Precip_Max_1d_mm"] == 40.0
    assert list(out) == weather.WX_COLUMNS
    assert weather.window("2020-01-15", "2020-12-31") == ("2020-01-01", "2020-02-28")


def test_readme_quotes_the_committed_impact_metrics(committed):
    readme = (METRICS_PATH.parents[3] / "README.md").read_text()
    assert f"{committed['variants']['brief']['temporal_test']['rmse']:.3f}" in readme
    assert f"{committed['variants']['no_size']['temporal_test']['rmse']:.3f}" in readme
    assert f"{committed['baselines_temporal_test']['dfo_formula']['rmse']:.3f}" in readme
    assert committed["limitations"][0][:60] in readme
