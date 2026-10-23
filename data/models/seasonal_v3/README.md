# Seasonal model v3: rebuild and export

`ngao_elnino_model_v3/v3.py` evaluated the model but saved nothing a service could load, and its inputs lived on another machine. These two scripts rebuild the recommended configuration from the same open data and export it as JSON for the backend.

```bash
python3 -m venv .venv-seasonal && .venv-seasonal/bin/pip install -r data/models/seasonal_v3/requirements.txt
.venv-seasonal/bin/python data/models/seasonal_v3/download.py   # ~1.3 GB into data/raw/seasonal/ (git-ignored), resumable
.venv-seasonal/bin/python data/models/seasonal_v3/train.py      # ~2 min
```

`train.py` writes:

- `backend/seasonal/model/ngao_seasonal_v3.json`: scaler, coefficients, thresholds, test metrics, the per-year backtest, the NCEP reference statistics used to put ERA5 on the training scale, and the ward-centroid → region table. The backend loads it, and nothing else is needed at run time.
- `REPORT.md`: test scores next to the one-pager, the backtest, and every difference from the original run.

The index definitions (boxes and months) are imported from `backend/seasonal/indices.py`, so the features the model is fitted on and the ones it receives in October come from the same code.

Result (test 2016–2025): the 6-index model gives AUC 0.774 / F1 0.660 / Brier 0.122 for y25 and 0.737 / 0.642 / 0.200 for yter. The one-pager reports 0.775 / 0.662 / 0.122 and 0.736 / 0.642 / 0.200. See [REPORT.md](REPORT.md), and [docs/SEASONAL_MODEL.md](../../../docs/SEASONAL_MODEL.md) for how the backend uses it.

Retrain when the training data changes (for example a new CHIRPS year), then commit the new JSON. The model card in NgaoDB (`SEASONAL_MODEL_RUNS`) gets new rows on the next outlook only when the `model_version` changes.
