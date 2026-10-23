"""Ocean and atmosphere indices of the v3 seasonal model (ngao_elnino_model_v3/REPORT_v3.md).

Shared by the training script (data/models/seasonal_v3/train.py) and the live outlook, so the
features the model was fitted on and the ones it is fed in October are computed by the same code.

Issued at the end of September for the October-December short rains, from observations only:
  SST   August-September mean (NOAA ERSSTv5)
  u850, SLP   July-September mean (NCEP/NCAR Reanalysis 1 for training; ERA5 live, see sources.py)
"""
import numpy as np

SST_MONTHS = (8, 9)
ATMOS_MONTHS = (7, 8, 9)

# (lat_min, lat_max, lon_min, lon_max), degrees north / degrees east in 0..360, edges inclusive.
BOXES = {
    "wio": (-10, 10, 50, 70),        # western Indian Ocean SST (Saji et al. 1999)
    "eio": (-10, 0, 90, 110),        # eastern Indian Ocean SST
    "nino34": (-5, 5, 190, 240),     # Nino 3.4 SST
    "u850_ceio": (-5, 5, 60, 90),    # central equatorial Indian Ocean 850 hPa zonal wind (Hastenrath 2011)
    "slp_wio": (-10, 10, 50, 70),    # sea-level pressure, west and east (Black 2003; Behera 2005)
    "slp_eio": (-10, 0, 90, 110),
}

SST_BOXES = ("wio", "eio", "nino34")
U850_BOXES = ("u850_ceio",)
SLP_BOXES = ("slp_wio", "slp_eio")

# Model features, in artifact order. Every variant uses a prefix-compatible subset of these.
FEATURES = ("wio", "eio", "dmi", "nino34", "u850_ceio", "slp_grad")
DESCRIPTIONS = {
    "wio": "Western Indian Ocean SST, Aug-Sep (degC)",
    "eio": "Eastern Indian Ocean SST, Aug-Sep (degC)",
    "dmi": "Indian Ocean Dipole: WIO minus EIO SST (degC)",
    "nino34": "Nino 3.4 SST, Aug-Sep (degC)",
    "u850_ceio": "Central equatorial Indian Ocean 850 hPa zonal wind, Jul-Sep (m/s)",
    "slp_grad": "East minus west Indian Ocean sea-level pressure, Jul-Sep (hPa)",
}


def box_mean(field, lats, lons, box):
    """cos(latitude)-weighted mean of a 2-D (lat, lon) field over a box, ignoring NaN cells (land)."""
    field = np.asarray(field, dtype=float)
    lats = np.asarray(lats, dtype=float)
    lons = np.mod(np.asarray(lons, dtype=float), 360.0)
    lat0, lat1, lon0, lon1 = box
    in_lat = (lats >= lat0) & (lats <= lat1)
    in_lon = (lons >= lon0) & (lons <= lon1)
    if not in_lat.any() or not in_lon.any():
        raise ValueError(f"box {box} has no grid cells")
    sub = field[np.ix_(in_lat, in_lon)]
    weights = np.broadcast_to(np.cos(np.deg2rad(lats[in_lat]))[:, None], sub.shape)
    ok = np.isfinite(sub)
    if not ok.any():
        raise ValueError(f"box {box} is all missing")
    return float((sub[ok] * weights[ok]).sum() / weights[ok].sum())


def months_mean(fields):
    """Mean of several (lat, lon) fields cell by cell, NaN where any month is missing."""
    return np.mean(np.stack([np.asarray(f, dtype=float) for f in fields]), axis=0)


def derive(raw):
    """Box means -> model features: dmi = wio - eio, slp_grad = slp_eio - slp_wio."""
    features = {}
    for key in ("wio", "eio", "nino34", "u850_ceio"):
        if raw.get(key) is not None:
            features[key] = raw[key]
    if raw.get("wio") is not None and raw.get("eio") is not None:
        features["dmi"] = raw["wio"] - raw["eio"]
    if raw.get("slp_wio") is not None and raw.get("slp_eio") is not None:
        features["slp_grad"] = raw["slp_eio"] - raw["slp_wio"]
    return features
