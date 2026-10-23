# Ngao seasonal model v3, rebuilt from open data

Built 2026-10-15 by `data/models/seasonal_v3/train.py`. Exported to `backend/seasonal/model/ngao_seasonal_v3.json`, which the backend's seasonal outlook loads.

## Test scores, unseen seasons 2016-2025 (ward-seasons)

| Variant | Target | C | Threshold | ROC-AUC (90% CI) | F1 | Brier | Year corr | One-pager v3 |
|---|---|---|---|---|---|---|---|---|
| v3: 6 literature indices | y25 | 0.003 | 0.24 | 0.774 (0.43-0.90) | 0.660 | 0.122 | 0.8757 | AUC 0.775, F1 0.662, Brier 0.122 |
| v3: 6 literature indices | yter | 0.003 | 0.38 | 0.737 (0.52-0.87) | 0.642 | 0.200 | 0.8234 | AUC 0.736, F1 0.642, Brier 0.200 |
| SST-only (live while NCEP R1 is discontinued) | y25 | 0.003 | 0.33 | 0.773 (0.39-0.91) | 0.666 | 0.130 | 0.8272 | - |
| SST-only (live while NCEP R1 is discontinued) | yter | 0.003 | 0.29 | 0.741 (0.52-0.88) | 0.669 | 0.200 | 0.777 | - |

## Backtest: observed vs predicted share of wet wards (y25)

| Year | Observed | v3 lit6 | SST-only |
|---|---|---|---|
| 2016 | 0.00 | 0.01 | 0.02 |
| 2017 | 0.04 | 0.11 | 0.28 |
| 2018 | 0.09 | 0.29 | 0.36 |
| 2019 | 1.00 | 0.80 | 0.82 |
| 2020 | 0.39 | 0.05 | 0.05 |
| 2021 | 0.01 | 0.04 | 0.06 |
| 2022 | 0.20 | 0.01 | 0.01 |
| 2023 | 0.71 | 0.80 | 0.76 |
| 2024 | 0.17 | 0.11 | 0.17 |
| 2025 | 0.09 | 0.01 | 0.03 |

## Latest issue the data allows (P(y25) by region, 0 = south ... 5 = north)

- v3: 6 literature indices: OND 2025: [0.01, 0.008, 0.008, 0.005, 0.005, 0.007]
- SST-only (live while NCEP R1 is discontinued): OND 2026: [0.713, 0.659, 0.659, 0.556, 0.563, 0.63]

## Differences from the original v3 run

- Regions: v2's `ward_regions.csv` was not available, so the six regions are re-derived with k-means on each ward's standardised 1981-2015 OND anomaly series (training years only).
- Ward rainfall: mean of CHIRPS 0.05 deg pixel centres inside each of the 1452 geoBoundaries wards; 188 small wards use the pixel under their centroid.
- GPCC vs CHIRPS 1981-2015: Kenya-mean OND correlation 0.989, ward totals 0.8 (one-pager: 0.989 Kenya-mean).
- SST-only variant: same protocol with WIO, EIO, DMI and Nino 3.4 only. It runs live because NCEP/NCAR Reanalysis 1 stopped updating in March 2026, so July-September 2026 wind and pressure exist only in ERA5 (needs a Copernicus CDS key).

## Caveats

All wards in a season move together, so the 2016-2025 test is effectively 10 seasons: the AUC confidence interval is wide and gains over simpler models are directional, not proven. June-September ocean conditions did not explain the wet seasons of 2020, 2022, 2024 and 2025. Decision support next to the official KMD / ICPAC outlook, not a replacement.
