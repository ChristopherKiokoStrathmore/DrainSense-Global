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

## Flood impact model (CatBoost)

A second, separate model. `model/impact.py` trains the CatBoost regressor from `data/CatBoost_Model_Executive_Brief.pdf` (depth 7, learning rate 0.03, L2 5.0, MVS subsample 0.8, up to 1,200 trees) on the 5,503 records in `data/Enriched_Global_Flood_Records-v2.csv`. It does not feed the map. The notebook `model/notebooks/catboost_flood_impact.ipynb` walks through the same steps.

The target, `Flood impact index`, is the DFO magnitude: log10(days × severity × area_km2), rounded to 0.1, with days counting the start and end date. That formula matches 5,499 of 5,503 rows after rounding. The CSV's 14 weather columns came from an enrichment step that is not in this repo, and they look synthetic: 376 distinct points for 5,503 events, a median 541 km from the flood. The model does not use them. `model/weather.py` takes daily Open-Meteo reanalysis (ERA5 / ERA5-Land) at the centre of each event's footprint in the DFO GeoPackage instead: 14 days before the start, and up to 45 days of the event. Two footprints are stored a whole turn off in longitude (5161 Thailand, 5228 Cuba) and are wrapped back into ±180°. Fetch the weather with `python -m model.weather` (resumable, about 9,300 Open-Meteo calls for all events).

Source file: `model/artifacts/impact/impact_metrics.json`. Temporal test: start years 2019–2024, 792 events. The trees are counted on 2016–2018 and the model is refit on 1985–2018.

| | Inputs | Test RMSE | R² |
|---|---|---|---|
| DFO formula | days, severity, area | 0.029 | 0.999 |
| Linear regression | log area, log days | 0.114 | 0.991 |
| CatBoost `brief` | log area, duration, 17 observed weather values, continent | 0.173 | 0.980 |
| CatBoost `no_size` | weather before the event and in its first three days, location, month, cause, continent | 1.102 | 0.184 |
| Training mean | none | 1.233 | −0.022 |

The brief's own protocol, a random 80/20 split that picks the best iteration on the test rows, gives test RMSE 0.131 here against the 0.0985 the brief reports (`brief_reproduction`).

API:

| Endpoint | Returns |
|---|---|
| `GET /api/v1/impact/model` | Model card: parameters, metrics, baselines, data checks, required inputs per variant |
| `POST /api/v1/impact/predict?variant=brief\|no_size` | One estimate, SHAP contributions, and the exact formula value when area, dates and severity are sent |
| `POST /api/v1/impact/predict/batch?variant=…` | Up to 500 events in one call |
| `GET /api/v1/impact/test-events?country=&limit=` | Held-out 2019–2024 records with the index, the formula and both holdout predictions (`limit` up to 1000) |

Swagger UI is at `/docs` on a running API (ReDoc at `/redoc`, raw schema at `/openapi.json`). The same schema is committed as `api/openapi.json`; regenerate it with `python -m api.export_openapi` after changing a route, and `tests/test_impact_api.py` fails if it drifts. Request bodies carry two examples from the event table: the Kenya flood of Oct–Nov 2023 (event 5477) for `brief`, and the July 2021 floods in Germany and Belgium (event 5095) for `no_size`.

Postman: import `api/postman/DrainSense.postman_collection.json` and `api/postman/DrainSense.local.postman_environment.json`. 24 requests in three folders (impact model, risk map, drainage log) with 55 test assertions. Run the folders in order; the risk map folder stores `cell_id` and the drainage log chains report and inspection ids. From the command line:

```bash
npx newman run api/postman/DrainSense.postman_collection.json -e api/postman/DrainSense.local.postman_environment.json
```

Dashboard: the desk's **Impact** page (`web/app/impact/page.js`, http://localhost:3000/impact) runs entirely on the API above:

| Section | Endpoint |
|---|---|
| Status strip (API up, model build date, links to Swagger UI and the schema) | `GET /api/v1/health`, `GET /api/v1/impact/model` |
| Headline errors, error by model, SHAP drivers per variant, data checks, limitations | `GET /api/v1/impact/model` |
| Predicted against recorded for all 792 held-out events, and the held-out table | `GET /api/v1/impact/test-events?limit=1000` |
| Score an event (form starts from the schema's examples) | `POST /api/v1/impact/predict`, `GET /openapi.json` |
| Score a batch: CSV template, upload or paste, 500 rows per call, errors mapped to CSV rows, results as CSV | `POST /api/v1/impact/predict/batch` |

Each section loads on its own, so one failing call does not blank the page.

Retrain from the committed event table, or rebuild that table from `data/` and the weather cache first:

```bash
python3 -m model.impact
python3 -m model.weather && python3 -m model.impact --rebuild
```

## Production

**https://drainsense.158.180.57.75.sslip.io** (Swagger UI at `/docs`), on the Oracle VM next to NGAO. Pushes to `main` build the images, publish them to GHCR, and deploy them once the `ORACLE_VM_*` secrets are set. [`docs/DEPLOY_DRAINSENSE.md`](docs/DEPLOY_DRAINSENSE.md) has the setup, the manual deploy, rollback, and logs.

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

Open http://localhost:3000. Every page reads the API:

| Page | What it shows |
|---|---|
| `/` Overview | The problem each part answers, live figures from every part, the maintenance loop |
| `/map` Risk map | Flood score per cell, SHAP reasons, flood record, open drain reports, the cell's ledger and sector flags |
| `/operations` Ledger | Report, inspection, repair and incident forms; a report moves to inspection scheduled, then repaired; incidents before and after each repair |
| `/response` Dispatch | The ranking, with each cell's points split into model, open reports and repeat incidents |
| `/sectors` Sectors | The rule behind each sector flag, a flag matrix for the 30 highest-scoring cells, and each flag's inputs |
| `/impact`, `/model` | The impact dashboard and the risk model card |
| `/endpoints` API | Every endpoint, read live from `/openapi.json`, with links into Swagger UI |

The only key the desk can take is an optional CARTO basemaps key, `NEXT_PUBLIC_CARTO_KEY` (free from https://carto.com/basemaps/apikey). The map uses CARTO's vector Dark Matter style, which loads without one today; CARTO's raster tiles have needed a key since 2026-09-25. The API itself has no key or login.

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
| `model/artifacts/` | Feature table, `metrics.json`, `scores.geojson`, `cell_places.json` |
| `model/geocode_cells.py` | Reverse-geocodes each 5° cell to a region and country name (OSM Nominatim, resumable). The API serves it as `place_name`. Rerun after `model.train` adds cells. |
| `model/impact.py` | CatBoost flood-impact regressor from the executive brief, baselines, data checks |
| `model/artifacts/impact/` | Event table, both `.cbm` models, `impact_metrics.json`, held-out predictions |
| `model/notebooks/` | Walkthrough notebook for the impact model |
| `api/openapi.json`, `api/postman/` | Committed Swagger schema and Postman collection |
| `deploy/oracle-vm/`, `web/Dockerfile` | Production Compose file, Caddy site, deploy script, desk image |
| `api/` | FastAPI. Scores come from the artifacts. SQLite holds the drainage log. |
| `web/` | Next.js desk, MapLibre, CARTO Dark Matter vector basemap |
| `backend/scoring/heuristics.py` | NGAO heavy-rain constant used by the flood model |
| `backend/`, `ussd/` | NGAO services, kept. Not the DrainSense runtime. |
| `legacy/` | APEX export, Oracle schema, VM deploy, APEX workflows |

## Limitations

- The target is the DFO magnitude, log10(days x severity x area_km2), rounded to 0.1. Given those three numbers the formula is exact. The brief model is given two of them and learns the rest; it is not forecasting impact. The weather columns it trains on look synthetic. The rest of `impact_metrics.json` `limitations` covers that model.
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
