"""Two-week flood watch: the risk model's outlook score combined with Open-Meteo's daily rainfall forecast.

The risk model (model/train.py) scores a whole year from the previous year's rainfall and elevation. It cannot see
next week's rain. This module adds the rain: for every cell it pulls the daily precipitation forecast at the cell
centre and compares the wettest 7 days in the chosen window with the wettest 7 days the cell had in the model's
feature year. A fixed, published rule turns the model score and that comparison into a level per cell.

It is a rule, not a trained or validated model: nobody has checked it against floods that followed a forecast.
A 5-degree cell is about 500 km across and the forecast is read at one point, its centre.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import httpx

from api.app import scoring

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# The furthest a user may look ahead, in days from today (UTC). Open-Meteo's daily forecast reaches 16 days.
MAX_DAYS = 14
# Same cut as the model's heavy_day_count feature.
HEAVY_MM = 20.0
# Locations per Open-Meteo request; each location counts as one call against its free limit (600 a minute).
BATCH = 100
# A forecast is reused for this long, so 571 cells are fetched at most a few times a day.
CACHE_SECONDS = 3 * 3600

LEVELS = ("likely", "watch", "quiet")
RULE = (
    "Likely: model score at or above the threshold, and the wettest 7 days forecast in the window at least as wet "
    f"as the cell's wettest 7 days in the feature year, or {int(HEAVY_MM)} mm or more on 2+ days. "
    "Watch: model score at or above the threshold with at least half that 7-day total or one heavy day, "
    "or a score below the threshold with a forecast at least as wet as that 7-day total. Quiet: everything else."
)


class ForecastUnavailable(RuntimeError):
    """Open-Meteo could not be reached or answered with an error."""


_cache: dict = {"at": 0.0, "issued": None, "rows": None}


def today() -> date:
    return datetime.now(timezone.utc).date()


def _fetch_rows(cells: list[dict]) -> dict[str, dict]:
    """Daily precipitation for the next 16 days at every cell centre: {cell_id: {"dates": [...], "rain": [...]}}."""
    rows: dict[str, dict] = {}
    with httpx.Client(timeout=60, headers={"User-Agent": "DrainSense-desk/0.1"}) as client:
        for start in range(0, len(cells), BATCH):
            chunk = cells[start:start + BATCH]
            params = {
                "latitude": ",".join(f"{c['center_lat']:g}" for c in chunk),
                "longitude": ",".join(f"{c['center_lon']:g}" for c in chunk),
                "daily": "precipitation_sum",
                "forecast_days": 16,
                "timezone": "UTC",
            }
            try:
                response = client.get(FORECAST_URL, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ForecastUnavailable(f"The Open-Meteo rainfall forecast could not be loaded ({exc}).") from exc
            body = response.json()
            answers = body if isinstance(body, list) else [body]
            for cell, answer in zip(chunk, answers):
                daily = answer.get("daily") or {}
                rows[cell["cell_id"]] = {
                    "dates": daily.get("time") or [],
                    "rain": [0.0 if v is None else float(v) for v in daily.get("precipitation_sum") or []],
                }
    return rows


def forecast_rows() -> tuple[date, dict[str, dict]]:
    """The cached forecast for every cell, refetched after CACHE_SECONDS or when the UTC day changes."""
    now = time.time()
    issued = today()
    if _cache["rows"] is None or _cache["issued"] != issued or now - _cache["at"] > CACHE_SECONDS:
        _cache.update(rows=_fetch_rows(scoring.cells()), issued=issued, at=now)
    return _cache["issued"], _cache["rows"]


def _wettest_run(values: list[float], days: int = 7) -> float:
    if len(values) <= days:
        return sum(values)
    return max(sum(values[i:i + days]) for i in range(len(values) - days + 1))


def _level(probability: float, threshold: float, ratio: float, heavy_days: int) -> str:
    if probability >= threshold and (ratio >= 1 or heavy_days >= 2):
        return "likely"
    if (probability >= threshold and (ratio >= 0.5 or heavy_days >= 1)) or ratio >= 1:
        return "watch"
    return "quiet"


def watch(until: date) -> dict:
    """Every cell's level for the window from today to `until` (inclusive), most at risk first."""
    issued, rows = forecast_rows()
    threshold = scoring.metrics()["model"]["threshold"]
    first, last = issued.isoformat(), until.isoformat()
    cells = []
    for row in scoring.cells():
        series = rows.get(row["cell_id"]) or {"dates": [], "rain": []}
        daily = [{"date": d, "mm": round(mm, 1)} for d, mm in zip(series["dates"], series["rain"]) if first <= d <= last]
        rain = [d["mm"] for d in daily]
        history = max(float(row["features"].get("max_7day_mm") or 0), 1.0)
        wettest7 = _wettest_run(rain)
        ratio = wettest7 / history
        heavy = sum(1 for mm in rain if mm >= HEAVY_MM)
        peak = max(daily, key=lambda d: d["mm"]) if daily else None
        probability = row["probability"]
        cells.append({
            "cell_id": row["cell_id"],
            "place_name": scoring.place_name(row["cell_id"]),
            "center_lat": row["center_lat"],
            "center_lon": row["center_lon"],
            "probability": probability,
            "level": _level(probability, threshold, ratio, heavy),
            "index": round(probability * min(ratio, 3), 4),
            "window_total_mm": round(sum(rain), 1),
            "wettest_7day_mm": round(wettest7, 1),
            "history_max_7day_mm": round(history, 1),
            "rain_ratio": round(ratio, 2),
            "heavy_days": heavy,
            "wettest_day": peak["date"] if peak else None,
            "wettest_day_mm": peak["mm"] if peak else None,
            "daily": daily,
        })
    cells.sort(key=lambda c: (LEVELS.index(c["level"]), -c["index"]))
    counts = {level: sum(1 for c in cells if c["level"] == level) for level in LEVELS}
    return {
        "kind": "rule on the model score and a rainfall forecast",
        "issued": first,
        "until": last,
        "days": (until - issued).days + 1,
        "latest_until": (issued + timedelta(days=MAX_DAYS)).isoformat(),
        "max_days_ahead": MAX_DAYS,
        "threshold": threshold,
        "heavy_mm": HEAVY_MM,
        "feature_year": scoring.cells()[0]["feature_year"],
        "rule": RULE,
        "source": "Open-Meteo daily precipitation forecast at each cell centre (open-meteo.com, CC BY 4.0)",
        "disclaimer": (
            "Not a warning and not validated against floods that followed a forecast. Cells are 5° across and the "
            "forecast is read at the cell centre."
        ),
        "counts": counts,
        "cells": cells,
    }
