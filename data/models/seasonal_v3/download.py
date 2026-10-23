#!/usr/bin/env python3
"""Download the open datasets the v3 seasonal model is trained on (about 1.3 GB, resumable).

Everything lands in data/raw/seasonal/ (git-ignored). Same sources as ngao_elnino_model_v3/REPORT_v3.md:
  NOAA ERSSTv5 monthly SST (2 deg)            sst.mnmean.nc        Aug-Sep SST indices
  NCEP/NCAR Reanalysis 1 monthly u850 / SLP   uwnd.nc, slp.nc      Jul-Sep wind and pressure indices
  GPCC Full Data v2020 monthly (1 deg)        gpcc.nc              labels 1951-1980
  CHIRPS v2.0 Africa monthly (0.05 deg)       chirps/*.tif.gz      labels 1981-2025 (OND only)
  geoBoundaries KEN ADM3                      KEN_ADM3.geojson     the 1,452 wards

  python3 data/models/seasonal_v3/download.py [--out data/raw/seasonal] [--first-year 1981] [--last-year 2025]
"""
import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PSL = "https://downloads.psl.noaa.gov/Datasets"
FILES = {
    "ersst.nc": f"{PSL}/noaa.ersst.v5/sst.mnmean.nc",
    "uwnd.nc": f"{PSL}/ncep.reanalysis.derived/pressure/uwnd.mon.mean.nc",
    "slp.nc": f"{PSL}/ncep.reanalysis.derived/surface/slp.mon.mean.nc",
    "gpcc.nc": f"{PSL}/gpcc/full_v2020/precip.mon.total.1x1.v2020.nc",
}
CHIRPS = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/africa_monthly/tifs/chirps-v2.0.{year}.{month:02d}.tif.gz"
GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/KEN/ADM3/"
USER_AGENT = "ngao-seasonal-v3/1.0 (GreenTech Hackathon 2026)"


def fetch(url, dest):
    """Download url to dest unless a complete copy is already there (size matches Content-Length)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        size = int(response.headers.get("Content-Length") or 0)
        if dest.exists() and size and dest.stat().st_size == size:
            return f"have {dest.name}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
    if size and tmp.stat().st_size != size:
        raise IOError(f"{url}: got {tmp.stat().st_size} of {size} bytes")
    tmp.replace(dest)
    return f"got  {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "raw" / "seasonal")
    parser.add_argument("--first-year", type=int, default=1981)
    parser.add_argument("--last-year", type=int, default=2025)
    args = parser.parse_args(argv)
    (args.out / "chirps").mkdir(parents=True, exist_ok=True)

    jobs = [(url, args.out / name) for name, url in FILES.items()]
    jobs += [(CHIRPS.format(year=y, month=m), args.out / "chirps" / f"{y}.{m:02d}.tif.gz")
             for y in range(args.first_year, args.last_year + 1) for m in (10, 11, 12)]

    meta = json.load(urllib.request.urlopen(urllib.request.Request(GEOBOUNDARIES_API, headers={"User-Agent": USER_AGENT}), timeout=60))
    jobs.append((meta["gjDownloadURL"], args.out / "KEN_ADM3.geojson"))

    failed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (url, dest), future in zip(jobs, [pool.submit(fetch, url, dest) for url, dest in jobs]):
            try:
                print(future.result(), flush=True)
            except Exception as exc:  # report every failure, then exit non-zero
                failed += 1
                print(f"FAIL {dest.name}: {exc}", file=sys.stderr, flush=True)
    (args.out / "SOURCES.json").write_text(json.dumps({"files": FILES, "chirps": CHIRPS,
                                                       "geoboundaries": meta.get("gjDownloadURL"),
                                                       "geoboundaries_build": meta.get("buildDate")}, indent=1))
    print(f"done: {len(jobs) - failed} ok, {failed} failed -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
