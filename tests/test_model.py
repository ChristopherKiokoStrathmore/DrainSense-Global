"""The committed metrics must be what model/train.py computes from the committed table."""
import json

import numpy as np
import pandas as pd

from model.train import (
    FEATURE_CSV,
    METRICS_PATH,
    SCORES_PATH,
    TEST_YEARS,
    TRAIN_YEARS,
    fit_frame,
)


def test_feature_years_do_not_overlap_and_lag_the_label():
    frame = pd.read_csv(FEATURE_CSV)
    labelled = frame[frame.flooded.notna()]
    assert (labelled.feature_year == labelled.label_year - 1).all()
    train = labelled[labelled.label_year.isin(TRAIN_YEARS)]
    test = labelled[labelled.label_year.isin(TEST_YEARS)]
    assert train.label_year.max() < test.label_year.min()
    assert set(train.feature_year).isdisjoint(set(test.feature_year))
    assert set(train.feature_year) == {2013, 2014, 2015, 2016, 2017}
    assert set(test.feature_year) == {2018, 2019, 2020, 2021, 2022}


def test_refit_matches_committed_metrics_and_scores():
    frame = pd.read_csv(FEATURE_CSV)
    result = fit_frame(frame)
    committed = json.loads(METRICS_PATH.read_text())
    fresh = result["metrics"]
    for key in ("roc_auc", "f1", "brier", "precision", "recall", "accuracy"):
        assert fresh["temporal_test"][key] == committed["temporal_test"][key]
    assert fresh["dummy_prior"]["brier"] == committed["dummy_prior"]["brier"]
    assert fresh["dummy_most_frequent"]["brier"] == committed["dummy_most_frequent"]["brier"]
    assert fresh["model"]["C"] == committed["model"]["C"]
    assert fresh["model"]["threshold"] == committed["model"]["threshold"]
    assert fresh["spatial_holdout"]["roc_auc"] == committed["spatial_holdout"]["roc_auc"]
    assert fresh["n_train_rows"] == committed["n_train_rows"]
    assert fresh["n_test_rows"] == committed["n_test_rows"]

    saved = {feat["properties"]["cell_id"]: feat["properties"]["probability"]
             for feat in json.loads(SCORES_PATH.read_text())["features"]}
    rebuilt = {feat["properties"]["cell_id"]: feat["properties"]["probability"]
               for feat in result["scores"]["features"]}
    assert saved.keys() == rebuilt.keys()
    for cell_id, probability in saved.items():
        assert abs(probability - rebuilt[cell_id]) < 1e-6


def test_shap_adds_up_to_the_log_odds():
    frame = pd.read_csv(FEATURE_CSV)
    result = fit_frame(frame)
    card = result["metrics"]["shap"]
    assert card["method"] == "shap.LinearExplainer"
    assert "max_abs_diff_vs_independent_formula_on_40_test_rows" in card
    # fit_frame already refuses to return if SHAP is not additive. A cell on the
    # map must carry the same property: expected log-odds plus the SHAP terms.
    feature = result["scores"]["features"][0]["properties"]
    total = feature["expected_log_odds"] + sum(feature["shap_log_odds"].values())
    decision = result["model"].decision_function(
        np.array([[feature["features"][name] for name in result["metrics"]["feature_columns"]]])
    )[0]
    assert abs(total - decision) < 1e-4


def test_readme_quotes_the_committed_temporal_metrics():
    metrics = json.loads(METRICS_PATH.read_text())
    readme = (FEATURE_CSV.parents[2] / "README.md").read_text()
    temporal = metrics["temporal_test"]
    dummy = metrics["dummy_prior"]
    for key in ("roc_auc", "f1", "brier"):
        assert f"{temporal[key]:.3f}" in readme
    assert f"{dummy['brier']:.3f}" in readme
    assert "Limitations" in readme
    assert metrics["limitations"][0][:80] in readme
