# DrainSense

Global flood desk. A Python model scores 5-degree land cells for a large-flood year, a FastAPI service serves those scores plus a drainage log, and a Next.js map is the desk. Oracle APEX is not on this path. The old APEX app, schema, and deploy workflows are under [`legacy/`](legacy/README.md).

The map score is a **model probability for 2024**, built from **2023 rainfall and elevation**. It is not a verified flood, and it is not a street-level forecast.

## What is real, and what is synthetic

| Piece | Origin |
|---|---|
| Flood labels | [Global Flood Records](https://zenodo.org/records/19288171) v0.9.0, DFO Flood Observatory, Zenodo record 19288171, CC BY 4.0. Accessed 2026-10-23. A cell-year is positive when the **bounding-box center** of a kept event falls in the cell. |
| Rainfall | Open-Meteo historical archive, daily `precipitation_sum`, at the cell center. Open-Meteo documents this as reanalysis (ERA5 / ERA5-Land). CC BY 4.0. This repo did not download CHIRPS or ERA5 files itself. |
| Elevation | Open-Meteo elevation API (documented 90 m DEM). Cells at elevation 0 were dropped as ocean. |
| Heavy-day threshold (20 mm) | Reused from NGAO: `HEAVY_RAIN_MM` in `backend/scoring/heuristics.py`. |
| Model, metrics, SHAP, map scores | Produced by `model/train.py` and stored in `model/artifacts/`. |
| Drainage reports shipped with the app | **Synthetic drill rows** from `api/app/db.py` (`origin = synthetic`). |
| Reports filed in the desk | Stored as `origin = community`. They are notes, not flood observations. |
| Sector panels | **Derived indicators**: fixed rules on the score, elevation, and previous-year rain. Not crop, disease, or loss models. |

Event filter from the run that built the table (`model/artifacts/flood_filters.json`, also copied into `metrics.json`): 5503 events in the GeoPackage, 5316 kept, 102 dropped for cause, 83 dropped because the box was wider or taller than 15 degrees, 2 dropped outside the grid.

## Metrics

Source file: `model/artifacts/metrics.json`, written by `model/train.py` on the committed table `model/artifacts/cell_year_features.csv`. The test `tests/test_model.py` refits that table and checks these figures.

Temporal test, label years 2019–2023, features from the previous calendar year only. 571 cells, 2855 train rows (2014–2018), 2855 test rows. Train positive rate 0.118, test positive rate 0.147. Logistic regression, C = 0.3, threshold 0.13 chosen by F1 on year-blocked out-of-fold training predictions.

| | ROC-AUC | F1 | Precision | Recall | Accuracy | Brier |
|---|---|---|---|---|---|---|
| Logistic regression | 0.695 | 0.375 | 0.285 | 0.548 | 0.731 | 0.120 |
| `DummyClassifier(strategy="prior")` | 0.500 | 0.000 | 0.000 | 0.000 | 0.853 | 0.126 |
| `DummyClassifier(strategy="most_frequent")` | 0.500 | 0.000 | 0.000 | 0.000 | 0.853 | 0.147 |

Year-clustered bootstrap 90% interval for the temporal ROC-AUC: 0.689–0.702 (`temporal_test.roc_auc_year_bootstrap_90ci`). Out-of-fold training ROC-AUC: 0.702.

A second model held out cells with southwest longitude in [30, 90) and scored their 2019–2023 labels. ROC-AUC 0.617, F1 0.393, Brier 0.152 (685 test rows, 127 positives). The map uses the model trained on every cell, not this holdout model.

The logistic regression ranks floods better than chance and its Brier score is slightly better than the prior dummy (0.120 vs 0.126). Predicting "no flood" every time is more accurate (0.853 vs 0.731) because most cell-years are not in the catalogue. That is why accuracy is the wrong headline.

SHAP values on the map are `shap.LinearExplainer` on the log-odds. Mean absolute SHAP on the test set is largest for `annual_precip_mm` (0.419) and `p95_daily_mm` (0.392). The independent formula `z_j * coef_j` differs by up to 0.036 on 40 test rows because the rainfall features are correlated; both attributions add up to the model log-odds.

## Run it

API, from the repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r api/requirements.txt
PYTHONPATH=. DRAINSENSE_DB=/tmp/drainsense.sqlite .venv/bin/uvicorn api.app.main:app --host 0.0.0.0 --port 8000
```

Docker does the same:

```bash
docker compose up --build api
```

Desk, against that API:

```bash
cd web
npm ci
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

Open http://localhost:3000. The map, ledger, dispatch list, sector flags, and model card all read the API.

Tests:

```bash
pip install -r api/requirements.txt -r model/requirements.txt pytest==8.3.3
pytest
```

Rebuild the feature table (network, Open-Meteo and Zenodo) and refit:

```bash
pip install -r model/requirements.txt
python3 -m model.train --download
```

Refit from the committed CSV only, no network:

```bash
python3 -m model.train --refit
```

## Layout

| Path | Role |
|---|---|
| `model/train.py` | Download, labels, leak checks, logistic regression, dummy baselines, SHAP |
| `model/artifacts/` | Feature table, `metrics.json`, `scores.geojson` |
| `api/` | FastAPI. Scores come from the artifacts. SQLite holds the drainage log. |
| `web/` | Next.js desk, MapLibre, CARTO dark basemap |
| `backend/scoring/heuristics.py` | NGAO heavy-rain constant used by the flood model |
| `backend/`, `ussd/` | NGAO services, kept. Not the DrainSense runtime. |
| `legacy/` | APEX export, Oracle schema, VM deploy, APEX workflows |

## Limitations

- The label is a Dartmouth Flood Observatory large-flood catalogue event, assigned to a 5-degree cell by the center of the event bounding box. It is not a mapped inundation footprint, a gauge exceedance, or a household survey.
- Cells are 5 degrees on a side, about 500 km at the equator. A score is for that whole cell, not a street, a drain, or a ward.
- Rainfall features are the previous calendar year's Open-Meteo daily precipitation at the cell center, plus elevation. The model does not use a weather forecast, river discharge, soil moisture, drainage infrastructure, or flood defenses.
- Training label years are 2014-2018 and test label years are 2019-2023. Feature years do not overlap. The same cells appear in both splits, so the temporal test measures a later period at seen locations. The longitude-block holdout is the check on unseen regions.
- DFO records large events drawn from news, agencies, and satellites. Absence of a record is not proof that no flood occurred. Small and unreported floods are missing, and reporting effort differs by country.
- Events whose bounding box is wider or taller than 15 degrees are dropped so a continent-scale outline does not stamp a single cell. Cells the elevation API treats as ocean (elevation 0) are dropped, which removes some coastal box centers.
- The 2024 outlook uses 2023 rainfall and has no DFO label in this run. It is a model score, not a verified outcome.
- Sector panels are deterministic relabellings of the score, elevation, and rainfall. They are not crop, epidemiological, or insurance-loss models.
- Drainage reports, inspections, repairs, and incidents shipped with the prototype are synthetic drill rows. Rows created through the API are stored as community reports and are not flood observations.
- DrainSense is not a warning service and does not replace national hydrological agencies.

## NGAO history

`main` of [NGAO](https://github.com/ChristopherKiokoStrathmore/NGAO) is an ancestor of this branch, merged without rewriting those commits. Their authors are unchanged. New DrainSense commits are Christopher Nguu only, with no co-author trailer.
