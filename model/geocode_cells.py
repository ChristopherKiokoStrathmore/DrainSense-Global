"""Give every scored cell a readable place name by reverse geocoding it.

    python -m model.geocode_cells            # name the cells that have no name yet
    python -m model.geocode_cells --refresh  # name every cell again

Reads model/artifacts/scores.geojson and writes model/artifacts/cell_places.json, which the API
serves as `place_name`. Names come from OpenStreetMap Nominatim at region level ("Los Lagos Region,
Chile"), because a cell is 5 degrees on a side and a town name would claim more precision than the
score has. The file is committed, so the API never calls Nominatim at runtime.

A cell centre can fall in the sea, so the script also tries the centres of the cell's four quarters.
A cell with no land hit keeps `name: null` and the API falls back to its coordinates.

Nominatim's usage policy allows one request per second with an identifying User-Agent; the script
keeps to that, and saves after every cell so an interrupted run picks up where it stopped.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
SCORES_PATH = ARTIFACTS / "scores.geojson"
PLACES_PATH = ARTIFACTS / "cell_places.json"

NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "DrainSense-Global/1.0 (+https://github.com/ChristopherKiokoStrathmore/DrainSense-Global)"
# Nominatim zoom 5 is state / region level.
ZOOM = 5
PAUSE_S = 1.1
# The address keys that name a first-level region, in the order Nominatim tends to fill them.
REGION_KEYS = ("state", "region", "province", "state_district", "county", "archipelago", "island")


def sample_points(props: dict) -> list[tuple[float, float]]:
    """The cell centre, then the centres of its four 2.5-degree quarters."""
    lat, lon = props["center_lat"], props["center_lon"]
    return [(lat, lon)] + [(lat + dlat, lon + dlon) for dlat in (-1.25, 1.25) for dlon in (-1.25, 1.25)]


def reverse(lat: float, lon: float) -> dict | None:
    query = urllib.parse.urlencode({
        "format": "jsonv2", "lat": lat, "lon": lon, "zoom": ZOOM,
        "addressdetails": 1, "accept-language": "en",
    })
    request = urllib.request.Request(f"{NOMINATIM}?{query}", headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
            return None if "error" in body else body
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise
            print(f"  retry after {exc}")
            time.sleep(5 * (attempt + 1))
    return None


def place_from(body: dict) -> dict | None:
    address = body.get("address") or {}
    country = address.get("country")
    region = next((address[key] for key in REGION_KEYS if address.get(key)), None)
    if not (region or country):
        return None
    name =", ".join(dict.fromkeys(part for part in (region, country) if part))
    return {
        "name": name,
        "region": region,
        "country": country,
        "country_code": (address.get("country_code") or "").upper() or None,
    }


def geocode_cell(props: dict) -> dict:
    for lat, lon in sample_points(props):
        body = reverse(lat, lon)
        time.sleep(PAUSE_S)
        place = place_from(body) if body else None
        if place:
            return {**place, "sampled_lat": lat, "sampled_lon": lon}
    return {"name": None, "region": None, "country": None, "country_code": None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="name every cell again")
    args = parser.parse_args()

    cells = [f["properties"] for f in json.loads(SCORES_PATH.read_text())["features"]]
    places = {} if args.refresh or not PLACES_PATH.exists() else json.loads(PLACES_PATH.read_text())["cells"]
    todo = [props for props in cells if props["cell_id"] not in places]
    print(f"{len(cells)} cells, {len(todo)} to geocode")

    for index, props in enumerate(todo, start=1):
        places[props["cell_id"]] = geocode_cell(props)
        print(f"[{index}/{len(todo)}] {props['cell_id']}: {places[props['cell_id']]['name']}")
        write(places)
    write(places)
    unnamed = sum(1 for place in places.values() if not place["name"])
    print(f"wrote {PLACES_PATH} ({len(places) - unnamed} named, {unnamed} without a land hit)")


def write(places: dict) -> None:
    payload = {
        "source": "OpenStreetMap Nominatim reverse geocoding, zoom 5 (region level). Data © OpenStreetMap contributors, ODbL 1.0.",
        "cells": dict(sorted(places.items())),
    }
    PLACES_PATH.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
