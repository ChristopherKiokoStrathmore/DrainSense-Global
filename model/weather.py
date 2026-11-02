#!/usr/bin/env python3
"""Open-Meteo reanalysis for every DFO event, at the centre of its mapped footprint.

Location
    The bounding-box centre of the event's polygon in the DFO GeoPackage
    (Zenodo 19288171, the same file model/train.py uses), joined on the report
    number. The enriched CSV's coordinates are not used: they snap every event
    to one of 376 points, a median 540 km from the footprint centre.

Window
    14 days before the start date through the end date, with the event part
    capped at 45 days. One request per event, 10 daily variables, so a typical
    request counts as 1-2 Open-Meteo calls and the full set as about 9,300.

Source
    Open-Meteo historical archive (ERA5 / ERA5-Land reanalysis), CC BY 4.0.

    python -m model.weather        # fetch what is missing into model/.cache/weather/
                                   # (resumable; stops cleanly at the daily limit)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "model" / ".cache"
GPKG = CACHE / "Global_Flood_Records.gpkg"
WEATHER_CACHE = CACHE / "weather"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY = [
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "temperature_2m_mean",
    "temperature_2m_max",
    "wind_gusts_10m_max",
    "et0_fao_evapotranspiration",
    "relative_humidity_2m_mean",
    "pressure_msl_min",
    "soil_moisture_0_to_7cm_mean",
]
ANTECEDENT_DAYS = 14
MAX_EVENT_DAYS = 45
EARLY_DAYS = 3
# Request starts are spaced so ~1.7-call requests stay under Open-Meteo's 5,000 calls per hour.
MIN_INTERVAL_S = 1.3
WORKERS = 3


class DailyLimitReached(RuntimeError):
    """Open-Meteo's free tier allows 10,000 calls a day. Run again tomorrow to resume."""


def footprints(gpkg: Path = GPKG) -> pd.DataFrame:
    """Bounding-box centre and span of every event polygon, keyed by DFO report number."""
    con = sqlite3.connect(gpkg)
    rows = con.execute(
        """
        select f.ReportNumber, r.minx, r.maxx, r.miny, r.maxy
        from combined_floods f
        join rtree_combined_floods_geom r on r.id = f.fid
        """
    ).fetchall()
    con.close()
    frame = pd.DataFrame(rows, columns=["Event_ID", "minx", "maxx", "miny", "maxy"])
    frame["Event_ID"] = frame["Event_ID"].astype(int)
    # Two polygons are stored a whole turn off (5161 Thailand at 459°, 5228 Cuba at -442°). Wrap into [-180, 180).
    centre_lon = (frame["minx"] + frame["maxx"]) / 2
    return pd.DataFrame({
        "Event_ID": frame["Event_ID"],
        "Footprint_Lat": ((frame["miny"] + frame["maxy"]) / 2).round(4),
        "Footprint_Lon": (((centre_lon + 180) % 360) - 180).round(4),
        "Footprint_Span_Deg": np.maximum(frame["maxx"] - frame["minx"], frame["maxy"] - frame["miny"]).round(3),
    })


def window(start: str, end: str) -> tuple[str, str]:
    first = date.fromisoformat(start)
    last = min(date.fromisoformat(end), first + timedelta(days=MAX_EVENT_DAYS - 1))
    return (first - timedelta(days=ANTECEDENT_DAYS)).isoformat(), last.isoformat()


def cache_path(event_id: int) -> Path:
    return WEATHER_CACHE / f"{int(event_id)}.json"


def cached(event_id: int) -> dict | None:
    """The cached payload, or None if missing or cut off mid-write."""
    path = cache_path(event_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return payload if "daily" in payload else None


class _Pacer:
    """Spaces request starts across threads."""

    def __init__(self, interval: float):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        time.sleep(max(0.0, start - now))


def _request(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "DrainSense-research/0.1"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429:
                if "Daily" in body:
                    raise DailyLimitReached(body) from exc
                wait = 600 if "Hourly" in body else 65
                print(f"rate limited, waiting {wait}s: {body}", flush=True)
                time.sleep(wait)
                continue
            if exc.code not in (500, 502, 503, 504):
                raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after retries: {url}")


def fetch_event(event_id: int, lat: float, lon: float, start: str, end: str, pacer: _Pacer | None = None) -> dict:
    payload = cached(event_id)
    if payload is not None:
        return payload
    if pacer is not None:
        pacer.wait()
    first, last = window(start, end)
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": first,
        "end_date": last,
        "daily": ",".join(DAILY),
        "timezone": "UTC",
    })
    payload = _request(f"{ARCHIVE_URL}?{query}")
    WEATHER_CACHE.mkdir(parents=True, exist_ok=True)
    # Write then rename, so an interrupted run never leaves half a file under the real name.
    tmp = cache_path(event_id).with_suffix(".part")
    tmp.write_text(json.dumps(payload))
    tmp.replace(cache_path(event_id))
    return payload


def fetch_all(events: pd.DataFrame) -> int:
    """Fetch every event not yet cached. Returns how many are still missing."""
    todo = events[[cached(i) is None for i in events["Event_ID"]]]
    print(f"{len(events) - len(todo)} cached, {len(todo)} to fetch, {WORKERS} workers", flush=True)
    pacer = _Pacer(MIN_INTERVAL_S)
    stop = threading.Event()
    done = 0
    failed: list[int] = []
    count_lock = threading.Lock()

    def one(row) -> None:
        nonlocal done
        if stop.is_set():
            return
        try:
            fetch_event(row.Event_ID, row.Footprint_Lat, row.Footprint_Lon, row.Start_Date, row.End_Date, pacer)
        except DailyLimitReached as exc:
            if not stop.is_set():
                print(f"daily limit reached: {exc}", flush=True)
            stop.set()
            return
        except RuntimeError as exc:
            # A network outage or a bad response for one event: skip it, a later run retries it.
            with count_lock:
                failed.append(int(row.Event_ID))
            print(f"event {row.Event_ID} skipped: {str(exc)[:120]}", flush=True)
            return
        with count_lock:
            done += 1
            if done % 100 == 0 or done == len(todo):
                print(f"fetched {done}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, todo.itertuples(index=False)))
    if failed:
        print(f"{len(failed)} events failed this run and will be retried next run", flush=True)
    return sum(cached(i) is None for i in events["Event_ID"])


def _series(payload: dict) -> pd.DataFrame:
    daily = payload["daily"]
    frame = pd.DataFrame({name: pd.to_numeric(pd.Series(daily[name]), errors="coerce") for name in DAILY})
    frame.index = pd.to_datetime(daily["time"])
    return frame


def summarise(payload: dict, start: str, end: str) -> dict:
    """Features from one event's daily series. Window sums grow with duration; early-day values do not."""
    series = _series(payload)
    first = pd.Timestamp(start)
    before = series[series.index < first]
    during = series[series.index >= first]
    early = during.iloc[:EARLY_DAYS]
    return {
        "Elevation_m": float(payload.get("elevation", np.nan)),
        "Wx_Precip_Antecedent_7d_mm": float(before["precipitation_sum"].iloc[-7:].sum()),
        "Wx_Precip_Antecedent_14d_mm": float(before["precipitation_sum"].sum()),
        "Wx_Soil_Moisture_Before_m3m3": float(before["soil_moisture_0_to_7cm_mean"].iloc[-1]),
        "Wx_Precip_Max_1d_First3d_mm": float(early["precipitation_sum"].max()),
        "Wx_Precip_First3d_mm": float(early["precipitation_sum"].sum()),
        "Wx_Wind_Gust_Max_First3d_kmh": float(early["wind_gusts_10m_max"].max()),
        "Wx_Temp_Mean_First3d_C": float(early["temperature_2m_mean"].mean()),
        "Wx_Pressure_Min_First3d_hPa": float(early["pressure_msl_min"].min()),
        "Wx_Precip_Event_mm": float(during["precipitation_sum"].sum()),
        "Wx_Rain_Event_mm": float(during["rain_sum"].sum()),
        "Wx_Snowfall_Event_cm": float(during["snowfall_sum"].sum()),
        "Wx_Precip_Max_1d_mm": float(during["precipitation_sum"].max()),
        "Wx_ET0_Event_mm": float(during["et0_fao_evapotranspiration"].sum()),
        "Wx_Temp_Max_C": float(during["temperature_2m_max"].max()),
        "Wx_RH_Mean_pct": float(during["relative_humidity_2m_mean"].mean()),
        "Wx_Soil_Moisture_Event_Max_m3m3": float(during["soil_moisture_0_to_7cm_mean"].max()),
    }


# Column order of summarise(), for the event table and the variants.
WX_COLUMNS = [
    "Elevation_m",
    "Wx_Precip_Antecedent_7d_mm",
    "Wx_Precip_Antecedent_14d_mm",
    "Wx_Soil_Moisture_Before_m3m3",
    "Wx_Precip_Max_1d_First3d_mm",
    "Wx_Precip_First3d_mm",
    "Wx_Wind_Gust_Max_First3d_kmh",
    "Wx_Temp_Mean_First3d_C",
    "Wx_Pressure_Min_First3d_hPa",
    "Wx_Precip_Event_mm",
    "Wx_Rain_Event_mm",
    "Wx_Snowfall_Event_cm",
    "Wx_Precip_Max_1d_mm",
    "Wx_ET0_Event_mm",
    "Wx_Temp_Max_C",
    "Wx_RH_Mean_pct",
    "Wx_Soil_Moisture_Event_Max_m3m3",
]


def weather_table(events: pd.DataFrame) -> pd.DataFrame:
    """Join footprints and cached weather onto the event table. Every event must be cached."""
    missing = [int(i) for i in events["Event_ID"] if cached(i) is None]
    if missing:
        raise RuntimeError(f"{len(missing)} events have no cached weather; run python -m model.weather")
    rows = []
    for row in events.itertuples(index=False):
        payload = cached(row.Event_ID)
        rows.append({"Event_ID": row.Event_ID, **summarise(payload, row.Start_Date, row.End_Date)})
    return pd.DataFrame(rows)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    sys.path.insert(0, str(REPO))
    from model.impact import SOURCE_CSV, load_records  # noqa: E402  (import cycle: impact imports this module)
    from model.train import download_gpkg  # noqa: E402

    download_gpkg()
    events = load_records(SOURCE_CSV)[["Event_ID", "Start_Date", "End_Date"]].merge(footprints(), on="Event_ID")
    remaining = fetch_all(events)
    print("done" if remaining == 0 else f"{remaining} events still to fetch; run again to resume", flush=True)
    if remaining:
        sys.exit(1)


if __name__ == "__main__":
    main()
