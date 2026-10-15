"""The trained v3 seasonal model as data: backend/seasonal/model/ngao_seasonal_v3.json.

Written by data/models/seasonal_v3/train.py. Scoring is a standardised logistic regression, so the
backend needs only numpy: p = sigmoid(coef . (x - mean) / scale + intercept), where x is the
variant's features followed by six region dummies.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from django.conf import settings


class ModelUnavailable(Exception):
    pass


@lru_cache(maxsize=4)
def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise ModelUnavailable(f"seasonal model artifact not found: {path}") from exc


def load_artifact():
    return _load(str(settings.NGAO_SEASONAL_ARTIFACT))


def region_dummies(region, count):
    return [1.0 if region == r else 0.0 for r in range(count)]


def predict(spec, features, region, region_count):
    """P(target) for one region given the feature dict (missing feature -> KeyError)."""
    names = spec["columns"][: len(spec["columns"]) - region_count]
    x = np.array([features[name] for name in names] + region_dummies(region, region_count))
    z = (x - np.array(spec["mean"])) / np.array(spec["scale"])
    logit = float(z @ np.array(spec["coef"]) + spec["intercept"])
    return 1.0 / (1.0 + math.exp(-logit))


class RegionLocator:
    """Region of the nearest training ward centroid (1,452 geoBoundaries ADM3 wards)."""

    def __init__(self, regions):
        table = np.array(regions["wards"], dtype=float)
        self.lat, self.lon, self.region = table[:, 0], table[:, 1], table[:, 2].astype(int)
        self.count = int(regions["count"])

    def __call__(self, lat, lon):
        dlat = np.deg2rad(self.lat - lat)
        dlon = np.deg2rad(self.lon - lon) * math.cos(math.radians(lat))
        return int(self.region[np.argmin(dlat * dlat + dlon * dlon)])
