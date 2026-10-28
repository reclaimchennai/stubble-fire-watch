#!/usr/bin/env python3
"""Pull current-year fire detections from the NASA FIRMS area API (needs NASA_FIRMS_MAP_KEY in .env).

Historical years (2003-2025) come from the public FIRMS yearly country files instead
(scripts/firms_archive.sh); this script covers what those files don't yet hold:
  - Jan..Jun of the current year: *_SP (standard processing)
  - Jul..today: *_NRT (near real time), incl. NOAA-21

Raw CSVs are cached per sensor/chunk in data/raw/firms_api/ and re-fetched only for the
last few days (NRT files keep filling in for ~2 days after acquisition).
"""
import csv
import datetime as dt
import io
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "firms_api"
BBOX = "66,6,98,37.5"     # W,S,E,N: all of India (68.5,21,89,35.5 until Oct 2026)
CHUNK = 5                 # FIRMS area API max day_range


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def availability(key):
    url = f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
    rows = csv.DictReader(io.StringIO(urllib.request.urlopen(url, timeout=60).read().decode()))
    return {r["data_id"]: (dt.date.fromisoformat(r["min_date"]), dt.date.fromisoformat(r["max_date"])) for r in rows}


def fetch(key, source, start, days, out):
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{BBOX}/{days}/{start.isoformat()}"
    for i in range(4):
        try:
            text = urllib.request.urlopen(url, timeout=120).read().decode()
            if text.startswith("Invalid") or "exceeded" in text[:200]:
                raise RuntimeError(text[:200])
            out.write_text(text)
            return text.count("\n") - 1
        except Exception as e:
            print(f"  retry {source} {start}: {e}", file=sys.stderr)
            time.sleep(20 * (i + 1))
    raise RuntimeError(f"failed {source} {start}")


def main():
    load_env()  # usage: firms_fetch.py [year] [SENSOR,SENSOR]
    key = os.environ.get("NASA_FIRMS_MAP_KEY")
    if not key:
        sys.exit("NASA_FIRMS_MAP_KEY missing in .env")
    RAW.mkdir(parents=True, exist_ok=True)
    avail = availability(key)
    today = dt.date.today()
    year = int(sys.argv[1]) if len(sys.argv) > 1 else today.year
    # past years: only NOAA-21, the one sensor the public country files lack (others would double count)
    default = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21") if year == today.year else ("VIIRS_NOAA21",)
    sensors = sys.argv[2].split(",") if len(sys.argv) > 2 else default
    jobs = []
    for sensor in sensors:  # LANDSAT_NRT exists but covers only North America
        for kind in ("SP", "NRT"):
            src = f"{sensor}_{kind}"
            if src not in avail:
                continue
            lo, hi = avail[src]
            lo, hi = max(lo, dt.date(year, 1, 1)), min(hi, dt.date(year, 12, 31))
            if kind == "NRT" and f"{sensor}_SP" in avail:  # NRT only where SP has not caught up
                lo = max(lo, avail[f"{sensor}_SP"][1] + dt.timedelta(days=1))
            d = lo
            while d <= hi:
                n = min(CHUNK, (hi - d).days + 1)
                jobs.append((src, d, n))
                d += dt.timedelta(days=n)
    total = 0
    for src, d, n in jobs:
        out = RAW / f"{src}_{d.isoformat()}_{n}.csv"
        fresh = (today - (d + dt.timedelta(days=n))).days > 3
        # an empty file can be a feed outage (S-NPP returned nothing for May 2026): retry those every run
        if out.exists() and fresh and out.read_text().count("\n") > 1:
            continue
        c = fetch(key, src, d, n, out)
        total += c
        print(f"{src} {d} +{n}d: {c}", file=sys.stderr)
        time.sleep(0.5)
    print(f"done: {len(jobs)} chunks, {total} new rows", file=sys.stderr)


if __name__ == "__main__":
    main()
