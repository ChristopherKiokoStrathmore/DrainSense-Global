#!/usr/bin/env python3
"""Rebuild the Ngao v3 seasonal model from open data and export it for the backend.

Reproduces the recommended configuration of ngao_elnino_model_v3 (REPORT_v3.md, one-pager):
  * issued end of September for October-December; Aug-Sep SST, Jul-Sep 850 hPa wind and SLP
  * 6 literature indices (WIO, EIO, DMI, Nino 3.4, central-IO u850, E-W IO SLP gradient) + 6 region dummies
  * standardised logistic regression, C chosen by 5-block year CV (Brier) on training years only,
    decision threshold = best F1 on out-of-fold training predictions
  * trained 1951-2015 (GPCC labels 1951-1980, CHIRPS 1981-2015), tested on unseen 2016-2025 (CHIRPS)
  * targets: y25 = OND > 125% of the ward's 1991-2020 mean; yter = above the 1991-2020 upper tercile
and adds the SST-only variant (WIO, EIO, DMI, Nino 3.4) used live while NCEP R1 is discontinued.

Differences from v3, stated in the report this writes:
  * v2's ward_regions.csv was not available. Regions are re-derived: k-means (k=6) on each ward's
    standardised 1981-2015 OND rainfall anomaly series (training years only, so no leakage).
  * Ward rainfall is the mean of CHIRPS 0.05 deg pixel centres inside the ward (the centroid pixel for
    wards too small to contain one).

  python3 data/models/seasonal_v3/download.py
  python3 data/models/seasonal_v3/train.py        # -> backend/seasonal/model/ngao_seasonal_v3.json
"""
import argparse
import datetime as dt
import gzip
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import xarray as xr
from shapely.geometry import shape
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, confusion_matrix, f1_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
from seasonal.indices import (ATMOS_MONTHS, BOXES, DESCRIPTIONS, SLP_BOXES, SST_BOXES, SST_MONTHS,  # noqa: E402
                              U850_BOXES, box_mean, derive)

warnings.filterwarnings("ignore")
TRAIN_END, TEST = 2015, (2016, 2025)
CLIM = (1991, 2020)
C_GRID = [0.003, 0.01, 0.03, 0.1, 1.0]
N_REGIONS = 6
VARIANTS = {
    "lit6": {"features": ["wio", "eio", "dmi", "nino34", "u850_ceio", "slp_grad"], "needs": ["ersst", "era5"],
             "role": "RECOMMENDED", "label": "v3: 6 literature indices"},
    "sst4": {"features": ["wio", "eio", "dmi", "nino34"], "needs": ["ersst"],
             "role": "VARIANT", "label": "SST-only (live while NCEP R1 is discontinued)"},
}
TARGETS = {
    "y25": "OND rainfall > 125% of the ward's 1991-2020 OND mean",
    "yter": "OND rainfall above the ward's 1991-2020 upper tercile (KMD 'above normal')",
}


# ----- predictors ------------------------------------------------------------------------------------


def yearly_box_means(da, months, boxes, years):
    """{year: {box: value}} from a (time, lat, lon) DataArray: mean over the months, then box means."""
    da = da.sel(time=da.time.dt.month.isin(months))
    out = {}
    for year, group in da.groupby("time.year"):
        if year not in years or group.sizes["time"] != len(months):
            continue
        field = group.mean("time").values
        out[int(year)] = {b: box_mean(field, da.lat.values, da.lon.values, BOXES[b]) for b in boxes}
    return out


def predictors(raw_dir, years):
    sst = xr.open_dataset(raw_dir / "ersst.nc").sst
    uwnd = xr.open_dataset(raw_dir / "uwnd.nc").uwnd.sel(level=850)
    slp = xr.open_dataset(raw_dir / "slp.nc").slp
    raw = {}
    for part in (yearly_box_means(sst, SST_MONTHS, SST_BOXES, years),
                 yearly_box_means(uwnd, ATMOS_MONTHS, U850_BOXES, years),
                 yearly_box_means(slp, ATMOS_MONTHS, SLP_BOXES, years)):
        for year, values in part.items():
            raw.setdefault(year, {}).update(values)
    table = pd.DataFrame({year: derive(values) for year, values in raw.items()}).T.sort_index()
    references = {}
    for box in U850_BOXES + SLP_BOXES:
        series = pd.Series({y: raw[y][box] for y in raw if box in raw[y] and CLIM[0] <= y <= CLIM[1]})
        references[box] = {"mean": round(float(series.mean()), 5), "std": round(float(series.std(ddof=1)), 5),
                           "period": list(CLIM), "source": "NCEP/NCAR Reanalysis 1 monthly, Jul-Sep"}
    return table, references, raw


# ----- wards and labels ------------------------------------------------------------------------------


def load_wards(raw_dir):
    gj = json.loads((raw_dir / "KEN_ADM3.geojson").read_text())
    rows = []
    for feature in gj["features"]:
        geom = shape(feature["geometry"])
        point = geom.representative_point() if not geom.centroid.within(geom) else geom.centroid
        rows.append({"ward_id": feature["properties"]["shapeID"], "name": feature["properties"].get("shapeName"),
                     "lat": point.y, "lon": point.x, "geometry": feature["geometry"]})
    return pd.DataFrame(rows)


def chirps_ond(raw_dir, wards, years):
    """Ward-mean CHIRPS monthly rain (mm) for October-December of each year."""
    first = raw_dir / "chirps" / f"{years[0]}.10.tif.gz"
    with rasterio.open(f"/vsigzip/{first}") as src:
        window = rasterio.windows.from_bounds(33.5, -5.0, 42.5, 5.5, src.transform).round_offsets().round_lengths()
        transform = src.window_transform(window)
        shape_ = (int(window.height), int(window.width))
    labels = rasterio.features.rasterize(
        ((w.geometry, i + 1) for i, w in enumerate(wards.itertuples())), out_shape=shape_, transform=transform,
        fill=0, dtype="int32")
    counts = np.bincount(labels.ravel(), minlength=len(wards) + 1)
    # Wards too small to hold a pixel centre use the pixel under their centroid.
    tiny = {i: rasterio.transform.rowcol(transform, w.lon, w.lat) for i, w in enumerate(wards.itertuples())
            if counts[i + 1] == 0}
    records = []
    for year in years:
        for month in (10, 11, 12):
            path = raw_dir / "chirps" / f"{year}.{month:02d}.tif.gz"
            with rasterio.open(f"/vsigzip/{path}") as src:
                rain = src.read(1, window=window).astype(float)
            rain[rain < 0] = np.nan
            ok = np.isfinite(rain)
            sums = np.bincount(labels[ok], weights=rain[ok], minlength=len(wards) + 1)
            n = np.bincount(labels[ok], minlength=len(wards) + 1)
            with np.errstate(invalid="ignore", divide="ignore"):
                means = sums / n
            for i, (r, c) in tiny.items():
                means[i + 1] = rain[r, c]
            records.append(pd.DataFrame({"ward_id": wards.ward_id.values, "year": year, "month": month,
                                         "rain": means[1:]}))
    monthly = pd.concat(records)
    ond = monthly.groupby(["ward_id", "year"]).rain.agg(["sum", "count"]).reset_index()
    ond = ond[ond["count"] == 3].rename(columns={"sum": "tot"}).drop(columns="count")
    return ond, int((counts[1:] == 0).sum())


def gpcc_ond(raw_dir, wards):
    g = xr.open_dataset(raw_dir / "gpcc.nc").precip
    g = g.sel(time=g.time.dt.month.isin([10, 11, 12])).groupby("time.year").sum("time", min_count=3)
    cells = g.sel(lat=xr.DataArray(wards.lat.values, dims="w"), lon=xr.DataArray(wards.lon.values % 360, dims="w"),
                  method="nearest").to_pandas()
    cells.columns = wards.ward_id.values
    return cells.stack().rename("tot").reset_index().rename(columns={"level_1": "ward_id"})


def add_labels(df, ref_years):
    base = df[(df.year >= ref_years[0]) & (df.year <= ref_years[1])].groupby("ward_id").tot
    df = df.join(base.mean().rename("clim"), on="ward_id").join(base.quantile(2 / 3).rename("q67"), on="ward_id")
    df["yter"] = (df.tot > df.q67).astype(int)
    df["y25"] = (df.tot > 1.25 * df.clim).astype(int)
    return df.dropna(subset=["tot", "clim"])


def regions(ond):
    """k-means on standardised 1981-2015 OND anomaly series per ward (training years only)."""
    train = ond[ond.year <= TRAIN_END].pivot(index="ward_id", columns="year", values="tot")
    train = train.dropna(axis=0)
    z = train.sub(train.mean(axis=1), axis=0).div(train.std(axis=1).replace(0, np.nan), axis=0).fillna(0)
    labels = KMeans(N_REGIONS, n_init=20, random_state=0).fit_predict(z.values)
    # Number regions by mean latitude order later (stable ids); return ward -> region.
    return pd.Series(labels, index=train.index, name="region")


# ----- fitting and evaluation (as v3.py) -------------------------------------------------------------


def make_lr(C):
    return make_pipeline(StandardScaler(), LogisticRegression(C=C, max_iter=5000))


def oof(C, X, y, years):
    out = np.zeros(len(y))
    for block in np.array_split(sorted(set(years)), 5):
        test = np.isin(years, block)
        out[test] = make_lr(C).fit(X[~test], y[~test]).predict_proba(X[test])[:, 1]
    return out


def best_threshold(y, p):
    grid = np.linspace(0.05, 0.9, 86)
    return float(grid[np.argmax([f1_score(y, p >= t, zero_division=0) for t in grid])])


def evaluate(y, p, years, threshold):
    pred = p >= threshold
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    rng = np.random.default_rng(0)
    unique = np.unique(years)
    groups = {u: np.flatnonzero(years == u) for u in unique}
    aucs = []
    for _ in range(1000):
        idx = np.concatenate([groups[u] for u in rng.choice(unique, len(unique))])
        if len(set(y[idx])) > 1:
            aucs.append(roc_auc_score(y[idx], p[idx]))
    loyo = [f1_score(y[years != u], pred[years != u], zero_division=0) for u in unique]
    per_year = pd.DataFrame({"year": years, "y": y, "p": p}).groupby("year").mean()
    r = lambda v: round(float(v), 4)  # noqa: E731
    return {
        "accuracy": r((tp + tn) / len(y)), "precision": r(tp / (tp + fp)) if tp + fp else 0.0,
        "recall": r(tp / (tp + fn)) if tp + fn else 0.0, "f1": r(f1_score(y, pred, zero_division=0)),
        "auc": r(roc_auc_score(y, p)), "brier": r(brier_score_loss(y, p)),
        "auc_ci90": [r(np.percentile(aucs, 5)), r(np.percentile(aucs, 95))],
        "loyo_f1": [r(min(loyo)), r(max(loyo))],
        "year_corr": r(np.corrcoef(per_year.y, per_year.p)[0, 1]) if per_year.p.std() > 0 else None,
        "n_test": int(len(y)), "n_test_years": int(len(unique)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }, {str(int(k)): [round(float(v.y), 4), round(float(v.p), 4)] for k, v in per_year.iterrows()}


def fit_target(frame, features, target):
    columns = features + [f"r{i}" for i in range(N_REGIONS)]
    train = frame[frame.year <= TRAIN_END]
    test = frame[(frame.year >= TEST[0]) & (frame.year <= TEST[1]) & (frame.src == "CHIRPS")]
    Xt, yt, yrs_t = train[columns].values, train[target].values, train.year.values
    C = min(C_GRID, key=lambda c: brier_score_loss(yt, oof(c, Xt, yt, yrs_t)))
    out_of_fold = oof(C, Xt, yt, yrs_t)
    threshold = best_threshold(yt, out_of_fold)
    model = make_lr(C).fit(Xt, yt)
    p_test = model.predict_proba(test[columns].values)[:, 1]
    metrics, backtest = evaluate(test[target].values, p_test, test.year.values, threshold)
    metrics["cv_auc"] = round(float(roc_auc_score(yt, out_of_fold)), 4)
    scaler, lr = model.named_steps["standardscaler"], model.named_steps["logisticregression"]
    spec = {
        "columns": columns,
        "mean": [round(float(v), 8) for v in scaler.mean_],
        "scale": [round(float(v), 8) for v in scaler.scale_],
        "coef": [round(float(v), 8) for v in lr.coef_[0]],
        "intercept": round(float(lr.intercept_[0]), 8),
        "C": C, "threshold": round(threshold, 4), "target_desc": TARGETS[target],
        "train_period": f"1951-{TRAIN_END} (GPCC labels 1951-1980, CHIRPS 1981-{TRAIN_END})",
        "test_period": f"{TEST[0]}-{TEST[1]} (CHIRPS, unseen)",
        "metrics": metrics, "backtest": backtest,
    }
    return spec, model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=REPO / "data" / "raw" / "seasonal")
    parser.add_argument("--out", type=Path, default=REPO / "backend" / "seasonal" / "model" / "ngao_seasonal_v3.json")
    parser.add_argument("--report", type=Path, default=Path(__file__).with_name("REPORT.md"))
    args = parser.parse_args(argv)

    years = list(range(1951, TEST[1] + 2))
    print("predictors ...", flush=True)
    table, references, raw = predictors(args.raw, years)

    print("wards and CHIRPS labels ...", flush=True)
    wards = load_wards(args.raw)
    ond, tiny = chirps_ond(args.raw, wards, list(range(1981, TEST[1] + 1)))
    ond = add_labels(ond, CLIM).assign(src="CHIRPS")

    print("GPCC labels 1951-1980 ...", flush=True)
    gpcc = gpcc_ond(args.raw, wards)
    overlap = gpcc[(gpcc.year >= 1981) & (gpcc.year <= 2015)].merge(ond[["ward_id", "year", "tot"]],
                                                                      on=["ward_id", "year"], suffixes=("_g", "_c"))
    gpcc = add_labels(gpcc, (1981, 2015))
    consistency = {
        "kenya_mean_corr": round(float(overlap.groupby("year").tot_g.mean().corr(overlap.groupby("year").tot_c.mean())), 3),
        "ward_total_corr": round(float(overlap.tot_g.corr(overlap.tot_c)), 3),
    }
    ext = gpcc[(gpcc.year >= 1951) & (gpcc.year <= 1980)].assign(src="GPCC")

    region = regions(ond)
    # Stable region ids: 0 = southernmost mean latitude ... 5 = northernmost.
    order = wards.set_index("ward_id").lat.groupby(region).mean().sort_values().index
    region = region.map({old: new for new, old in enumerate(order)})
    wards["region"] = wards.ward_id.map(region)

    labels = pd.concat([ond, ext])[["ward_id", "year", "y25", "yter", "src"]]
    frame = labels.merge(table, left_on="year", right_index=True).merge(
        wards[["ward_id", "region"]].dropna(), on="ward_id")
    for i in range(N_REGIONS):
        frame[f"r{i}"] = (frame.region == i).astype(float)

    artifact = {
        "name": "ngao-seasonal-v3",
        "built_at": dt.date.today().isoformat(),
        "season": "OND",
        "issue_point": "end of September (Aug-Sep SST, Jul-Sep wind and pressure)",
        "features": {name: DESCRIPTIONS[name] for name in DESCRIPTIONS},
        "references": references,
        "regions": {
            "count": N_REGIONS,
            "method": "k-means (k=6) on standardised 1981-2015 OND CHIRPS anomaly series per ward; "
                      "ids ordered south to north; a ward takes the region of the nearest training ward centroid",
            "wards": [[round(w.lat, 3), round(w.lon, 3), int(w.region)] for w in wards.dropna(subset=["region"]).itertuples()],
        },
        "data": {
            "sst": "NOAA ERSSTv5 monthly (PSL sst.mnmean.nc for training; NCEI ersst.v5.YYYYMM.nc live)",
            "atmosphere": "NCEP/NCAR Reanalysis 1 monthly u850 and SLP (training; discontinued March 2026); ERA5 live",
            "labels": "CHIRPS v2.0 Africa monthly 1981-2025; GPCC Full Data v2020 1951-1980",
            "wards": f"geoBoundaries KEN ADM3, {len(wards)} wards ({tiny} use their centroid pixel)",
            "gpcc_chirps_overlap_1981_2015": consistency,
        },
        "caveats": (
            "All wards in a season move together, so the 2016-2025 test is effectively 10 seasons: the AUC "
            "confidence interval is wide and gains over simpler models are directional, not proven. "
            "June-September ocean conditions did not explain the wet seasons of 2020, 2022, 2024 and 2025. "
            "Decision support next to the official KMD / ICPAC outlook, not a replacement."
        ),
        "variants": {},
    }
    print("fitting ...", flush=True)
    latest = {}
    for key, variant in VARIANTS.items():
        usable = frame.dropna(subset=variant["features"])
        targets = {}
        for target in TARGETS:
            spec, model = fit_target(usable, variant["features"], target)
            targets[target] = spec
            m = spec["metrics"]
            print(f"  {key:5s} {target:4s} C={spec['C']:<6} thr={spec['threshold']:.2f} AUC {m['auc']:.3f} "
                  f"{m['auc_ci90']} F1 {m['f1']:.3f} Brier {m['brier']:.3f}", flush=True)
            # Latest issue year with every input, per region (for the report).
            have = table.dropna(subset=variant["features"])
            year = int(have.index.max())
            rows = np.array([[*have.loc[year, variant["features"]].values, *np.eye(N_REGIONS)[r]] for r in range(N_REGIONS)])
            latest[(key, target)] = (year, [round(float(p), 3) for p in model.predict_proba(rows)[:, 1]])
        artifact["variants"][key] = {
            "model_version": f"ngao-seasonal-v3-{key}", "role": variant["role"], "label": variant["label"],
            "features": variant["features"], "needs": variant["needs"], "targets": targets,
        }
    artifact["indices"] = {str(y): {k: round(float(v), 4) for k, v in row.items() if pd.notna(v)}
                           for y, row in table.loc[2016:].iterrows()}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=1) + "\n")
    write_report(args.report, artifact, latest, consistency, tiny, len(wards))
    print(f"wrote {args.out} and {args.report}")


def write_report(path, artifact, latest, consistency, tiny, n_wards):
    lines = [
        "# Ngao seasonal model v3, rebuilt from open data",
        "",
        f"Built {artifact['built_at']} by `data/models/seasonal_v3/train.py`. Exported to "
        "`backend/seasonal/model/ngao_seasonal_v3.json`, which the backend's seasonal outlook loads.",
        "",
        "## Test scores, unseen seasons 2016-2025 (ward-seasons)",
        "",
        "| Variant | Target | C | Threshold | ROC-AUC (90% CI) | F1 | Brier | Year corr | One-pager v3 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    onepager = {("lit6", "y25"): "AUC 0.775, F1 0.662, Brier 0.122", ("lit6", "yter"): "AUC 0.736, F1 0.642, Brier 0.200"}
    for key, variant in artifact["variants"].items():
        for target, spec in variant["targets"].items():
            m = spec["metrics"]
            lines.append(f"| {variant['label']} | {target} | {spec['C']} | {spec['threshold']:.2f} | "
                         f"{m['auc']:.3f} ({m['auc_ci90'][0]:.2f}-{m['auc_ci90'][1]:.2f}) | {m['f1']:.3f} | "
                         f"{m['brier']:.3f} | {m['year_corr']} | {onepager.get((key, target), '-')} |")
    lines += ["", "## Backtest: observed vs predicted share of wet wards (y25)", "",
              "| Year | Observed | v3 lit6 | SST-only |", "|---|---|---|---|"]
    bt6 = artifact["variants"]["lit6"]["targets"]["y25"]["backtest"]
    bt4 = artifact["variants"]["sst4"]["targets"]["y25"]["backtest"]
    for year in sorted(bt6):
        lines.append(f"| {year} | {bt6[year][0]:.2f} | {bt6[year][1]:.2f} | {bt4.get(year, [None, float('nan')])[1]:.2f} |")
    lines += ["", "## Latest issue the data allows (P(y25) by region, 0 = south ... 5 = north)", ""]
    for (key, target), (year, probs) in latest.items():
        if target == "y25":
            lines.append(f"- {artifact['variants'][key]['label']}: OND {year}: {probs}")
    lines += [
        "",
        "## Differences from the original v3 run",
        "",
        "- Regions: v2's `ward_regions.csv` was not available, so the six regions are re-derived with k-means "
        "on each ward's standardised 1981-2015 OND anomaly series (training years only).",
        f"- Ward rainfall: mean of CHIRPS 0.05 deg pixel centres inside each of the {n_wards} geoBoundaries wards; "
        f"{tiny} small wards use the pixel under their centroid.",
        f"- GPCC vs CHIRPS 1981-2015: Kenya-mean OND correlation {consistency['kenya_mean_corr']}, "
        f"ward totals {consistency['ward_total_corr']} (one-pager: 0.989 Kenya-mean).",
        "- SST-only variant: same protocol with WIO, EIO, DMI and Nino 3.4 only. It runs live because "
        "NCEP/NCAR Reanalysis 1 stopped updating in March 2026, so July-September 2026 wind and pressure "
        "exist only in ERA5 (needs a Copernicus CDS key).",
        "",
        "## Caveats",
        "",
        artifact["caveats"],
        "",
    ]
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
