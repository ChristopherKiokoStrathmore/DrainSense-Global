# Ngao v3: Kenya OND seasonal model with ocean/atmosphere fields (honest evaluation)

## Protocol
- Issued end-September.
- Features use Aug–Sep SST and Jul–Sep wind/SLP only, never OND.
- Train ≤2015, test 2016–2025 on CHIRPS wards (14,520 ward-seasons, about 10 effective seasons).
- Targets: yter = above the ward's 1991–2020 upper tercile (KMD "above normal"); y25 = >+25% vs the 1991–2020 mean. The 1991–2020 reference overlaps the test years, same as v1/v2.
- Logistic C is chosen on train-only 5-block year CV (Brier). Decision thresholds maximise F1 on train OOF predictions.
- PCA/EOFs are fit on train years only.
- All features are year-level, plus 6 region dummies from v2.
- Uncertainty: AUC 90% CI from 1,000 test-year resamples; LOYO = test F1 range when one test year is dropped.

## Data (all downloaded 14–15 Oct 2026)
- NOAA ERSSTv5 monthly SST, 2°, 1854–Sep 2026: https://downloads.psl.noaa.gov/Datasets/noaa.ersst.v5/sst.mnmean.nc
- NCEP/NCAR Reanalysis 1 monthly u850 and SLP, 2.5°, 1948–Feb 2026 (that file stops at Feb 2026, so no 2026 forecast is possible from it): https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.derived/pressure/uwnd.mon.mean.nc and .../surface/slp.mon.mean.nc
- GPCC Full Data Monthly v2020, 1°, 1891–2019: https://downloads.psl.noaa.gov/Datasets/gpcc/full_v2020/precip.mon.total.1x1.v2020.nc
- ERA5 not used: no CDS credentials (~/.cdsapirc) on the box.
- Dynamical forecasts skipped. C3S seasonal hindcasts need a Copernicus CDS account plus API key. The IRI Data Library NMME pages (https://iridl.ldeo.columbia.edu/SOURCES/.Models/.NMME/) now redirect to a login page (checked 15 Oct 2026). Neither was freely obtainable without the user's credentials.

## Predictors
**Literature indices:**
- Western IO SST (50–70°E, 10°S–10°N) and eastern IO SST (90–110°E, 10°S–0), kept separately, plus their difference (DMI). Saji et al. 1999, Nature 401:360.
- Niño 3.4 SST.
- Central equatorial Indian Ocean u850 (60–90°E, 5°S–5°N). Hastenrath, Polzin & Mutai 2011, J. Climate 24:2817; Hastenrath & Polzin 2004.
- E−W Indian Ocean SLP gradient. Black, Slingo & Sperber 2003, MWR 131:74; Behera et al. 2005, J. Climate 18:4514; Nicholson 2017, Rev. Geophys. 55:590.

Correlations of the Kenya-wide share of above-normal wards with these indices, 1981–2015: DMI +0.58, Niño 3.4 +0.50, u850_ceio −0.47 (weaker westerlies → wetter), SLP east +0.48, WIO +0.40, EIO −0.33.

**EOFs:**
- SST 30°S–30°N, 30°E–70°W: 5 PCs; leading EOF explains 42%.
- u850 20°S–20°N, 30–150°E: 3 PCs.
- SLP: 3 PCs.

**GPCC extension:** OND sampled at the ward centroid (nearest 1° cell). Labels use GPCC's own 1981–2015 terciles/means and are used only for 1951–1980.
CHIRPS–GPCC overlap, 1981–2015:
- Kenya-mean OND correlation 0.989.
- Median per-ward correlation 0.918.
- Tercile label agreement 86.7%; >+25% agreement 90.7%.

## Results (test 2016–2025)
| target   | train                 | features                         | model       |   cv_auc |   acc |   prec |   rec |    f1 |   auc |   brier | auc_ci90       | loyo_f1        |   yearcorr |
|:---------|:----------------------|:---------------------------------|:------------|---------:|------:|-------:|------:|------:|------:|--------:|:---------------|:---------------|-----------:|
| yter     | 1981-2015 CHIRPS      | ONI JAS>=0.5 rule                | rule        |  nan     | 0.641 |  0.857 | 0.199 | 0.323 | 0.587 |   0.359 | [0.5, 0.702]   | [0.0, 0.398]   |      0.459 |
| yter     | 1981-2015 CHIRPS      | v2_6idx                          | LR(C=0.003) |    0.735 | 0.738 |  0.781 | 0.544 | 0.642 | 0.7   |   0.212 | [0.47, 0.832]  | [0.507, 0.703] |      0.753 |
| yter     | 1981-2015 CHIRPS      | v2_6idx                          | GBM         |    0.735 | 0.738 |  0.781 | 0.544 | 0.642 | 0.647 |   0.245 | [0.434, 0.774] | [0.507, 0.703] |      0.574 |
| yter     | 1981-2015 CHIRPS      | lit                              | LR(C=0.003) |    0.716 | 0.727 |  0.67  | 0.719 | 0.694 | 0.734 |   0.2   | [0.517, 0.867] | [0.606, 0.731] |      0.837 |
| yter     | 1981-2015 CHIRPS      | lit                              | GBM         |    0.657 | 0.439 |  0.419 | 0.778 | 0.544 | 0.689 |   0.204 | [0.447, 0.855] | [0.456, 0.591] |      0.706 |
| yter     | 1981-2015 CHIRPS      | pcs                              | LR(C=0.003) |    0.759 | 0.738 |  0.781 | 0.544 | 0.642 | 0.638 |   0.27  | [0.389, 0.802] | [0.507, 0.703] |      0.653 |
| yter     | 1981-2015 CHIRPS      | pcs                              | GBM         |    0.685 | 0.741 |  0.929 | 0.431 | 0.589 | 0.694 |   0.211 | [0.436, 0.855] | [0.398, 0.655] |      0.804 |
| yter     | 1981-2015 CHIRPS      | lit+pcs                          | LR(C=0.003) |    0.746 | 0.738 |  0.781 | 0.544 | 0.642 | 0.678 |   0.259 | [0.421, 0.82]  | [0.507, 0.703] |      0.718 |
| yter     | 1981-2015 CHIRPS      | lit+pcs                          | GBM         |    0.611 | 0.445 |  0.434 | 0.955 | 0.597 | 0.698 |   0.205 | [0.45, 0.873]  | [0.528, 0.642] |      0.82  |
| yter     | 1981-2015 CHIRPS      | ensemble: lit+pcs LR+GBM + v2 LR | mean        |    0.736 | 0.738 |  0.781 | 0.544 | 0.642 | 0.694 |   0.216 | [0.44, 0.842]  | [0.507, 0.703] |      0.779 |
| yter     | 1981-2015 CHIRPS      | ensemble: lit LR+GBM + v2 LR     | mean        |    0.732 | 0.738 |  0.781 | 0.544 | 0.642 | 0.718 |   0.198 | [0.49, 0.848]  | [0.507, 0.703] |      0.791 |
| yter     | 1951-2015 GPCC+CHIRPS | v2_6idx                          | LR(C=0.003) |    0.789 | 0.728 |  0.736 | 0.574 | 0.645 | 0.739 |   0.197 | [0.513, 0.869] | [0.52, 0.703]  |      0.791 |
| yter     | 1951-2015 GPCC+CHIRPS | v2_6idx                          | GBM         |    0.738 | 0.502 |  0.458 | 0.851 | 0.595 | 0.734 |   0.236 | [0.582, 0.846] | [0.517, 0.647] |      0.638 |
| yter     | 1951-2015 GPCC+CHIRPS | lit                              | LR(C=0.003) |    0.816 | 0.738 |  0.781 | 0.544 | 0.642 | 0.736 |   0.2   | [0.519, 0.869] | [0.507, 0.703] |      0.824 |
| yter     | 1951-2015 GPCC+CHIRPS | lit                              | GBM         |    0.752 | 0.738 |  0.781 | 0.544 | 0.642 | 0.746 |   0.196 | [0.544, 0.881] | [0.507, 0.703] |      0.787 |
| yter     | 1951-2015 GPCC+CHIRPS | pcs                              | LR(C=0.003) |    0.746 | 0.738 |  0.781 | 0.544 | 0.642 | 0.64  |   0.242 | [0.381, 0.801] | [0.507, 0.703] |      0.726 |
| yter     | 1951-2015 GPCC+CHIRPS | pcs                              | GBM         |    0.736 | 0.738 |  0.781 | 0.544 | 0.642 | 0.669 |   0.211 | [0.43, 0.851]  | [0.507, 0.703] |      0.737 |
| yter     | 1951-2015 GPCC+CHIRPS | lit+pcs                          | LR(C=0.003) |    0.774 | 0.738 |  0.781 | 0.544 | 0.642 | 0.703 |   0.225 | [0.448, 0.864] | [0.507, 0.703] |      0.781 |
| yter     | 1951-2015 GPCC+CHIRPS | lit+pcs                          | GBM         |    0.759 | 0.667 |  0.622 | 0.577 | 0.599 | 0.709 |   0.207 | [0.462, 0.879] | [0.471, 0.654] |      0.718 |
| yter     | 1951-2015 GPCC+CHIRPS | ensemble: lit+pcs LR+GBM + v2 LR | mean        |    0.799 | 0.738 |  0.781 | 0.544 | 0.642 | 0.735 |   0.205 | [0.5, 0.879]   | [0.507, 0.703] |      0.771 |
| yter     | 1951-2015 GPCC+CHIRPS | ensemble: lit LR+GBM + v2 LR     | mean        |    0.807 | 0.738 |  0.781 | 0.544 | 0.642 | 0.738 |   0.195 | [0.512, 0.874] | [0.507, 0.703] |      0.81  |
| y25      | 1981-2015 CHIRPS      | v2_6idx                          | LR(C=0.003) |    0.814 | 0.791 |  0.603 | 0.669 | 0.634 | 0.726 |   0.163 | [0.361, 0.871] | [0.437, 0.745] |      0.7   |
| y25      | 1981-2015 CHIRPS      | v2_6idx                          | GBM         |    0.796 | 0.791 |  0.603 | 0.669 | 0.634 | 0.692 |   0.185 | [0.339, 0.856] | [0.437, 0.745] |      0.524 |
| y25      | 1981-2015 CHIRPS      | lit                              | LR(C=0.003) |    0.816 | 0.791 |  0.603 | 0.669 | 0.634 | 0.777 |   0.123 | [0.441, 0.895] | [0.437, 0.745] |      0.88  |
| y25      | 1981-2015 CHIRPS      | lit                              | GBM         |    0.772 | 0.7   |  0.463 | 0.685 | 0.552 | 0.811 |   0.125 | [0.492, 0.928] | [0.362, 0.639] |      0.853 |
| y25      | 1981-2015 CHIRPS      | pcs                              | LR(C=0.003) |    0.834 | 0.763 |  0.585 | 0.426 | 0.493 | 0.744 |   0.182 | [0.442, 0.878] | [0.294, 0.539] |      0.669 |
| y25      | 1981-2015 CHIRPS      | pcs                              | GBM         |    0.772 | 0.791 |  0.603 | 0.669 | 0.634 | 0.838 |   0.141 | [0.606, 0.913] | [0.437, 0.745] |      0.859 |
| y25      | 1981-2015 CHIRPS      | lit+pcs                          | LR(C=0.003) |    0.817 | 0.791 |  0.603 | 0.669 | 0.634 | 0.724 |   0.175 | [0.34, 0.879]  | [0.437, 0.745] |      0.675 |
| y25      | 1981-2015 CHIRPS      | lit+pcs                          | GBM         |    0.686 | 0.7   |  0.463 | 0.685 | 0.552 | 0.783 |   0.141 | [0.464, 0.89]  | [0.362, 0.639] |      0.806 |
| y25      | 1981-2015 CHIRPS      | ensemble: lit+pcs LR+GBM + v2 LR | mean        |    0.81  | 0.791 |  0.603 | 0.669 | 0.634 | 0.738 |   0.151 | [0.363, 0.878] | [0.437, 0.745] |      0.736 |
| y25      | 1981-2015 CHIRPS      | ensemble: lit LR+GBM + v2 LR     | mean        |    0.829 | 0.791 |  0.603 | 0.669 | 0.634 | 0.779 |   0.13  | [0.447, 0.895] | [0.437, 0.745] |      0.827 |
| y25      | 1951-2015 GPCC+CHIRPS | v2_6idx                          | LR(C=0.003) |    0.81  | 0.785 |  0.589 | 0.672 | 0.628 | 0.767 |   0.147 | [0.399, 0.903] | [0.431, 0.735] |      0.729 |
| y25      | 1951-2015 GPCC+CHIRPS | v2_6idx                          | GBM         |    0.793 | 0.791 |  0.603 | 0.669 | 0.634 | 0.708 |   0.17  | [0.395, 0.857] | [0.437, 0.745] |      0.607 |
| y25      | 1951-2015 GPCC+CHIRPS | lit                              | LR(C=0.003) |    0.832 | 0.816 |  0.656 | 0.668 | 0.662 | 0.775 |   0.122 | [0.424, 0.899] | [0.466, 0.745] |      0.876 |
| y25      | 1951-2015 GPCC+CHIRPS | lit                              | GBM         |    0.834 | 0.791 |  0.603 | 0.669 | 0.634 | 0.799 |   0.116 | [0.487, 0.931] | [0.437, 0.745] |      0.897 |
| y25      | 1951-2015 GPCC+CHIRPS | pcs                              | LR(C=0.003) |    0.772 | 0.791 |  0.603 | 0.669 | 0.634 | 0.724 |   0.15  | [0.289, 0.884] | [0.437, 0.745] |      0.766 |
| y25      | 1951-2015 GPCC+CHIRPS | pcs                              | GBM         |    0.738 | 0.791 |  0.603 | 0.669 | 0.634 | 0.783 |   0.133 | [0.421, 0.925] | [0.437, 0.745] |      0.869 |
| y25      | 1951-2015 GPCC+CHIRPS | lit+pcs                          | LR(C=0.003) |    0.787 | 0.791 |  0.603 | 0.669 | 0.634 | 0.739 |   0.132 | [0.305, 0.907] | [0.437, 0.745] |      0.84  |
| y25      | 1951-2015 GPCC+CHIRPS | lit+pcs                          | GBM         |    0.768 | 0.791 |  0.603 | 0.669 | 0.634 | 0.8   |   0.132 | [0.479, 0.926] | [0.437, 0.745] |      0.807 |
| y25      | 1951-2015 GPCC+CHIRPS | ensemble: lit+pcs LR+GBM + v2 LR | mean        |    0.813 | 0.791 |  0.603 | 0.669 | 0.634 | 0.79  |   0.135 | [0.443, 0.921] | [0.437, 0.745] |      0.796 |
| y25      | 1951-2015 GPCC+CHIRPS | ensemble: lit LR+GBM + v2 LR     | mean        |    0.839 | 0.791 |  0.603 | 0.669 | 0.634 | 0.782 |   0.125 | [0.418, 0.918] | [0.437, 0.745] |      0.85  |

**Baselines.** "ONI JAS ≥ 0.5 rule" is scored here on its binary output, so AUC = 0.587. Scored on raw ONI values, v2's figure was 0.746. For y25 the v2 ONI rule had F1 0.388, AUC 0.720. v2 best = v2_6idx LR, 1981–2015 (rows 1 and 21).

Many models share F1 0.642 / 0.634. This is because predictions are year-level: different models flag the same years. **The strongest-regularisation C (0.003, the edge of the grid) was always chosen**, i.e. the data supports very little complexity.

## What genuinely beats v2, and what doesn't
- **Literature indices + logistic (lit LR)** is the most consistent improvement.
  - yter (1981 training): F1 0.694 vs 0.642, AUC 0.734 vs 0.700, Brier 0.200 vs 0.212, year-corr 0.84 vs 0.75.
  - y25: AUC 0.777 vs 0.726, Brier 0.123 vs 0.163.
  - Per year, the gain comes mainly from **removing the 2018 false alarm** (y25 mean probability 0.69 → 0.27; observed 0.09) and a stronger 2019 call (0.72 → 0.80). Nearly a single-season effect.
  - CIs overlap heavily (yter AUC 0.52–0.87 vs 0.47–0.83). **Directionally better and physically motivated, not statistically proven.**
- **GPCC extension to 1951** (65 training seasons): small, consistent gains in Brier/AUC for most feature sets (e.g. y25 lit LR F1 0.662, Brier 0.122; yter v2_6idx AUC 0.739 vs 0.700). Within uncertainty.
- **EOF/PCs:** no robust gain. yter LR on PCs gives AUC 0.638, worse than v2. y25 GBM on PCs has the best AUC of all (0.838, CI 0.61–0.91), but other PC models don't replicate it, and LR on PCs gets F1 0.49. **Likely chance; not trusted.**
- **GBM:** unstable thresholds (precision/recall swings), no consistent gain.
- **Ensembles:** improve Brier modestly (y25 0.125–0.130) but not F1.
- **Still missed by every model:** 2020, 2022, 2024 and 2025 wet signals (observed above-normal share 0.46/0.33/0.63/0.32, predicted ≤0.34). These seasons are not explained by Jun–Sep ocean state.

## Recommended configuration
Logistic regression (C = 0.003, standardized) on 6 literature indices (WIO, EIO, DMI, Niño 3.4, central-IO u850, IO SLP gradient) plus region dummies, trained 1951–2015 with GPCC pre-1981 labels.
- yter: F1 0.642, AUC 0.736, Brier 0.200.
- y25: F1 0.662, AUC 0.775, Brier 0.122.
- Expect a real-world AUC of roughly 0.5–0.9 given only ~10 test seasons.

For operations, compare with or defer to the ICPAC GHACOF / KMD official OND outlook, which uses dynamical models we couldn't access.

For 2026: ERSST is available through Sep 2026, but the NCEP R1 file ends Feb 2026, so the u850/SLP features are unavailable. A 2026 run would need the R1 operational files or ERA5 credentials.
