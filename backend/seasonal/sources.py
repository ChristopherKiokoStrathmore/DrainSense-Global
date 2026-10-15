"""Live inputs for the seasonal outlook.

ERSSTv5 (always): one small netCDF per month from NOAA NCEI (about 170 kB), published a few days
after the month ends, so August-September is complete in early October.
    https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/netcdf/ersst.v5.YYYYMM.nc

ERA5 (optional, needs CDSAPI_KEY): monthly 850 hPa zonal wind and mean sea-level pressure for
July-September, from the Copernicus Climate Data Store. NCEP/NCAR Reanalysis 1, which the model was
trained on, stopped updating in March 2026, so ERA5 replaces it live. ERA5 box means are put on the
NCEP scale by matching their 1991-2020 mean and standard deviation (stored in the model artifact),
which also takes care of units (ERA5 SLP is in Pa, NCEP in hPa).
"""
import io
import json
import logging
import tempfile
from pathlib import Path

import numpy as np
import requests
from django.conf import settings

from seasonal.indices import ATMOS_MONTHS, BOXES, SLP_BOXES, SST_BOXES, SST_MONTHS, U850_BOXES, box_mean, months_mean

logger = logging.getLogger(__name__)

ERSST_URL = "https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/netcdf/ersst.v5.{year}{month:02d}.nc"
ERA5_REFERENCE_YEARS = (1991, 2020)
# North, West, South, East: covers every wind and pressure box.
ERA5_AREA = [10, 50, -10, 110]


class SourceUnavailable(Exception):
    """The data for this season is not published yet (or the source is not configured)."""


def cache_dir():
    path = Path(settings.NGAO_SEASONAL_CACHE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_var(handle, name):
    """A netCDF4 variable via h5py, with _FillValue / missing_value -> NaN and scale/offset applied."""
    var = handle[name]
    data = var[()].astype(float)
    for attr in ("_FillValue", "missing_value"):
        if attr in var.attrs:
            data[data == float(np.ravel(var.attrs[attr])[0])] = np.nan
    scale = float(np.ravel(var.attrs.get("scale_factor", [1.0]))[0])
    offset = float(np.ravel(var.attrs.get("add_offset", [0.0]))[0])
    return data * scale + offset


# ----- ERSSTv5 ---------------------------------------------------------------------------------------


def ersst_month(year, month, session=None):
    """(sst[lat, lon] degC with NaN over land, lats, lons) for one month. Cached on disk."""
    import h5py

    path = cache_dir() / f"ersst.v5.{year}{month:02d}.nc"
    if not path.exists():
        response = (session or requests).get(ERSST_URL.format(year=year, month=month), timeout=60)
        if response.status_code == 404:
            raise SourceUnavailable(f"ERSSTv5 {year}-{month:02d} is not published yet")
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(response.content)
        Path(tmp.name).replace(path)
    with h5py.File(io.BytesIO(path.read_bytes()), "r") as handle:
        sst = _read_var(handle, "sst")
        lats, lons = handle["lat"][()], handle["lon"][()]
    return sst.reshape(sst.shape[-2:]), lats, lons


def sst_box_means(year, session=None):
    """{"wio", "eio", "nino34"}: August-September box means for the issue year."""
    months = [ersst_month(year, m, session=session) for m in SST_MONTHS]
    field = months_mean([m[0] for m in months])
    lats, lons = months[0][1], months[0][2]
    return {key: box_mean(field, lats, lons, BOXES[key]) for key in SST_BOXES}


# ----- ERA5 --------------------------------------------------------------------------------------------


def era5_configured():
    if not settings.NGAO_CDSAPI_KEY:
        return False
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        return False
    return True


def _era5_retrieve(dataset, variable, years, target, extra=None):
    import cdsapi

    client = cdsapi.Client(url=settings.NGAO_CDSAPI_URL, key=settings.NGAO_CDSAPI_KEY, quiet=True, progress=False)
    request = {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": [variable],
        "year": [str(y) for y in years],
        "month": [f"{m:02d}" for m in ATMOS_MONTHS],
        "time": ["00:00"],
        "area": ERA5_AREA,
        "grid": ["1.0", "1.0"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        **(extra or {}),
    }
    client.retrieve(dataset, request, str(target))


def era5_box_series(path, short_name, boxes):
    """{year: {box: Jul-Sep mean}} from an ERA5 monthly netCDF (valid_time, [level,] latitude, longitude)."""
    import h5py

    with h5py.File(path, "r") as handle:
        values = _read_var(handle, short_name)
        lats, lons = handle["latitude"][()], handle["longitude"][()]
        times = handle["valid_time"][()] if "valid_time" in handle else handle["time"][()]
    if values.ndim == 4:  # (time, pressure_level, lat, lon) with a single level
        values = values[:, 0]
    stamps = np.asarray(times, dtype="int64").astype("datetime64[s]")
    years = stamps.astype("datetime64[Y]").astype(int) + 1970
    months = stamps.astype("datetime64[M]").astype(int) % 12 + 1
    out = {}
    for year in sorted(set(years.tolist())):
        idx = [i for i in range(len(stamps)) if years[i] == year and months[i] in ATMOS_MONTHS]
        if len(idx) != len(ATMOS_MONTHS):
            continue  # an incomplete season is not a value
        field = months_mean([values[i] for i in idx])
        out[int(year)] = {box: box_mean(field, lats, lons, BOXES[box]) for box in boxes}
    return out


ERA5_VARIABLES = (
    # dataset, CDS variable, netCDF short name, boxes, extra request keys
    ("reanalysis-era5-pressure-levels-monthly-means", "u_component_of_wind", "u", U850_BOXES,
     {"pressure_level": ["850"]}),
    ("reanalysis-era5-single-levels-monthly-means", "mean_sea_level_pressure", "msl", SLP_BOXES, None),
)


def _era5_series(years, label):
    """Box means for the given years, cached per label (reference period or issue year)."""
    cached = cache_dir() / f"era5_{label}.json"
    if cached.exists():
        return {int(k): v for k, v in json.loads(cached.read_text()).items()}
    merged = {}
    for dataset, variable, short_name, boxes, extra in ERA5_VARIABLES:
        target = cache_dir() / f"era5_{label}_{short_name}.nc"
        _era5_retrieve(dataset, variable, years, target, extra)
        for year, values in era5_box_series(target, short_name, boxes).items():
            merged.setdefault(year, {}).update(values)
        target.unlink(missing_ok=True)
    cached.write_text(json.dumps(merged))
    return merged


def to_reference_scale(value, era5_reference, model_reference):
    """Match ERA5's 1991-2020 mean and spread to the training data's (NCEP) for the same box."""
    era5 = np.asarray(era5_reference, dtype=float)
    z = (value - era5.mean()) / era5.std(ddof=1)
    return float(model_reference["mean"] + z * model_reference["std"])


def atmos_box_means(year, references):
    """{"u850_ceio", "slp_wio", "slp_eio"} for the issue year on the training (NCEP) scale."""
    if not era5_configured():
        raise SourceUnavailable("ERA5 needs CDSAPI_KEY (Copernicus CDS) and the cdsapi package")
    first, last = ERA5_REFERENCE_YEARS
    reference = _era5_series(list(range(first, last + 1)), f"ref_{first}_{last}")
    current = _era5_series([year], f"{year}")
    if year not in current:
        raise SourceUnavailable(f"ERA5 July-September {year} is not complete yet")
    out = {}
    for box in U850_BOXES + SLP_BOXES:
        series = [reference[y][box] for y in sorted(reference) if box in reference[y]]
        if len(series) < 20:
            raise SourceUnavailable(f"ERA5 reference for {box} has only {len(series)} years")
        out[box] = to_reference_scale(current[year][box], series, references[box])
    return out
