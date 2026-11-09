#!/usr/bin/env python3
"""Afternoon fires from geostationary orbit: LSA SAF MSG-IODC FRP-PIXEL over the two Punjabs and Haryana.

SEVIRI on Meteosat-8 (41.5 E, to mid-2022) and Meteosat-9 (45.5 E) images the Indian Ocean disk every 15 minutes, so
it sees fires lit after the ~13:30 IST polar-orbiter passes. Its 3 km (about 4 x 4 km over Punjab) pixels only catch
fires above roughly 40-60 MW, so its counts are an index of the larger fires, not a census. The 2024-25 pilot that
justified this source is docs/geostationary_pilot.md (scripts/geostationary_pilot.py).

Needs a free LSA SAF data-server account: LSASAF_USER / LSASAF_PASSWORD in .env. Without them the fetch is skipped and
the output is rebuilt from what is cached.

Per season (1 Oct - 30 Nov IST) every 15-min list product is downloaded once, the fire pixels inside the box are kept
and the HDF5 file is deleted. Slots the archive lacks are retried for 3 days, then recorded as missing.

Outputs
  data/raw/geostationary/<year>/fires.csv     fire pixels in the box (slot, lat, lon, FRP, confidence, view angle, area)
  data/raw/geostationary/<year>/slots.json    status of every slot, satellite
  data/geostationary/geostationary.json       per season and region: daily fire cells, hour first seen, totals
Usage: fetch_geostationary.py [--season 2024 2025 ...] [--no-download]   (default: this year's season)
"""
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "geostationary"
PILOT = RAW / "iodc"                      # HDF5 kept by the pilot; read from here instead of downloading again
OUT = ROOT / "data" / "geostationary"
URL = "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MSG-IODC/FRP-PIXEL/HDF5"
NAME = "HDF5_LSASAF_MSG-IODC_FRP-PIXEL-ListProduct_IODC-Disk_{}"
BOX = (69.0, 27.5, 78.0, 34.2)            # lon0, lat0, lon1, lat1: Indian Punjab, Haryana, Pakistani Punjab
REGIONS = {"Punjab": ("India", "Punjab"), "Haryana": ("India", "Haryana"), "Punjab (PK)": ("Pakistan", "Punjab")}
IST = dt.timedelta(hours=5, minutes=30)
SEASON = ((10, 1), (11, 30))              # IST days fetched and plotted
WINDOW = ((10, 15), (11, 15))             # IST days for the season totals (same as the pilot)
CELL = 0.04                               # degrees, ~4 km: one SEVIRI pixel over Punjab
FIRST_YEAR = 2017                         # start of the IODC FRP-PIXEL archive
SATS = {"MSG1": "Meteosat-8, 41.5° E", "MSG2": "Meteosat-9, 45.5° E"}
FIELDS = ["slot_utc", "lat", "lon", "frp", "conf", "vza", "area_km2"]


def env():
    out = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def season_days(year, span=SEASON):
    d0, d1 = dt.date(year, *span[0]), dt.date(year, *span[1])
    return [d0 + dt.timedelta(n) for n in range((d1 - d0).days + 1)]


def slots(year):
    """UTC slot times covering the season's IST days."""
    t0 = dt.datetime.combine(dt.date(year, *SEASON[0]), dt.time()) - IST
    t1 = dt.datetime.combine(dt.date(year, *SEASON[1]) + dt.timedelta(1), dt.time()) - IST
    t0 = t0.replace(minute=t0.minute - t0.minute % 15)
    out, t = [], t0
    while t < t1:
        out.append(t)
        t += dt.timedelta(minutes=15)
    return out


def read(path):
    """Fire pixels inside BOX, plus the satellite name, from one list product."""
    import h5py
    lon0, lat0, lon1, lat1 = BOX
    with h5py.File(path, "r") as f:
        sat = bytes(np.ravel(f.attrs.get("SATELLITE", [b""]))[0]).decode().strip("-").strip()

        def ds(name):
            if name not in f:
                return None
            d = f[name]
            raw = d[:]
            v = raw.astype("float64")
            sc, off, miss = d.attrs.get("SCALING_FACTOR"), d.attrs.get("OFFSET"), d.attrs.get("MISSING_VALUE")
            if sc is not None:
                v = v / float(np.ravel(sc)[0]) + (float(np.ravel(off)[0]) if off is not None else 0.0)
            if miss is not None:
                v[raw == np.ravel(miss)[0]] = np.nan
            return v

        lat, lon = ds("LATITUDE"), ds("LONGITUDE")
        if lat is None or len(lat) == 0:
            return sat, []
        cols = [ds(n) for n in ("FRP", "FIRE_CONFIDENCE", "PIXEL_VZA", "PIXEL_SIZE")]
    keep = np.where((lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1))[0]
    rows = []
    for i in keep:
        vals = [None if c is None or np.isnan(c[i]) else round(float(c[i]), 2) for c in cols]
        rows.append([round(float(lat[i]), 3), round(float(lon[i]), 3), *vals])
    return sat, rows


def fetch_season(year, creds, workers=6):
    ydir = RAW / str(year)
    ydir.mkdir(parents=True, exist_ok=True)
    sf = ydir / "slots.json"
    state = json.loads(sf.read_text()) if sf.exists() else {"satellite": None, "slots": {}}
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    todo = [t for t in slots(year) if state["slots"].get(f"{t:%Y%m%d%H%M}") not in ("ok", "missing")
            and t + dt.timedelta(minutes=75) < now]       # files appear about an hour after the slot
    if not todo:
        return state, Counter()
    user, pw = creds
    tmp = Path(tempfile.mkdtemp(prefix="iodc-", dir=RAW))

    def get(t):
        stamp = f"{t:%Y%m%d%H%M}"
        name = NAME.format(stamp)
        local = PILOT / f"{t:%Y%m%d}" / name
        if local.exists() and local.stat().st_size:
            return t, "ok", *read(local)
        path = tmp / name
        cfg = f'user = "{user}:{pw}"\n'                     # on stdin, so the password never reaches argv
        for i in range(4):
            r = subprocess.run(["curl", "-s", "-m", "120", "-K", "-", "-o", str(path), "-w", "%{http_code}",
                                f"{URL}/{t:%Y/%m/%d}/{name}"], input=cfg, text=True, capture_output=True)
            code = r.stdout.strip()[-3:]
            if r.returncode == 0 and code == "200":
                try:
                    return t, "ok", *read(path)
                finally:
                    path.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            # not in the archive: the server answers 404 and then often resets the connection (curl exit 56)
            if code == "404":
                old = now - t > dt.timedelta(days=3)
                return t, "missing" if old else "pending", None, []
            time.sleep(3 * (i + 1))
        return t, "failed", None, []

    status = Counter()
    fcsv = ydir / "fires.csv"
    new = not fcsv.exists()
    with open(fcsv, "a", newline="") as fh, ThreadPoolExecutor(workers) as ex:
        w = csv.writer(fh)
        if new:
            w.writerow(FIELDS)
        for n, fut in enumerate(as_completed([ex.submit(get, t) for t in todo])):
            t, st, sat, rows = fut.result()
            status[st] += 1
            if st == "ok":
                for r in rows:
                    w.writerow([t.isoformat(timespec="minutes"), *r])
                if sat and not state["satellite"]:
                    state["satellite"] = sat
            if st in ("ok", "missing"):
                state["slots"][f"{t:%Y%m%d%H%M}"] = st
            if n % 200 == 199:                              # checkpoint: rows are flushed before slots are marked done
                fh.flush()
                sf.write_text(json.dumps(state))
    sf.write_text(json.dumps(state))
    tmp.rmdir()
    return state, status


def regions():
    g = json.load(open(ROOT / "data" / "geo" / "states.geojson"))
    out = {}
    for key, (country, name) in REGIONS.items():
        f = next(f for f in g["features"] if f["properties"].get("country") == country and f["properties"]["name"] == name)
        out[key] = prep(shape(f["geometry"]))
    return out


def summarise(year, regs):
    ydir = RAW / str(year)
    sf, fcsv = ydir / "slots.json", ydir / "fires.csv"
    if not sf.exists():
        return None
    state = json.loads(sf.read_text())
    all_slots = slots(year)
    done = [t for t in all_slots if state["slots"].get(f"{t:%Y%m%d%H%M}") in ("ok", "missing")]
    if not done:
        return None
    days = season_days(year)
    win = set(season_days(year, WINDOW))
    seen = set()
    per = {k: {"daily_cells": defaultdict(set), "first": {}, "det": 0, "fre_mj": 0.0, "hours": Counter()} for k in regs}
    if fcsv.exists():
        for r in csv.DictReader(open(fcsv)):
            key = (r["slot_utc"], r["lat"], r["lon"])
            if key in seen:                                  # a re-run after an interrupted checkpoint can repeat rows
                continue
            seen.add(key)
            t = dt.datetime.fromisoformat(r["slot_utc"]) + IST
            lat, lon = float(r["lat"]), float(r["lon"])
            reg = next((k for k, g in regs.items() if g.contains(Point(lon, lat))), None)
            if reg is None:
                continue
            p, cell = per[reg], (round(lat / CELL), round(lon / CELL))
            p["daily_cells"][t.date()].add(cell)
            k = (t.date(), cell)
            if k not in p["first"] or t < p["first"][k]:
                p["first"][k] = t
            if t.date() in win:
                p["det"] += 1
                p["fre_mj"] += float(r["frp"] or 0) * 900      # MW over a 15-min slot
                p["hours"][t.hour] += 1
    out = {}
    for reg, p in per.items():
        fh = Counter(t.hour for (d, _), t in p["first"].items() if d in win)
        out[reg] = {
            "daily_cells": [len(p["daily_cells"].get(d, ())) for d in days],
            "window": {"detections": p["det"], "fire_cells": sum(fh.values()), "fre_tj": round(p["fre_mj"] / 1e6, 1),
                       "first_seen_hour": [fh.get(h, 0) for h in range(24)],
                       "detections_hour": [p["hours"].get(h, 0) for h in range(24)]},
        }
    last = max(done)
    return {
        "satellite": SATS.get(state.get("satellite"), state.get("satellite")),
        "through": (last + IST).isoformat(timespec="minutes"),
        "complete": len(done) == len(all_slots),
        "window_complete": all(state["slots"].get(f"{t:%Y%m%d%H%M}") in ("ok", "missing") for t in all_slots
                               if (t + IST).date() in win),
        "missing_slots": sum(1 for v in state["slots"].values() if v == "missing"),
        "slots": len(all_slots),
        "regions": out,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, nargs="+")
    ap.add_argument("--no-download", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    years = a.season or ([today.year] if today >= dt.date(today.year, *SEASON[0]) else [])
    e = env()
    creds = (e["LSASAF_USER"], e["LSASAF_PASSWORD"]) if e.get("LSASAF_USER") and e.get("LSASAF_PASSWORD") else None
    if not a.no_download:
        if not creds:
            print("no LSASAF_USER / LSASAF_PASSWORD in .env; rebuilding from the cache only")
        for y in years if creds else []:
            st, status = fetch_season(y, creds)
            print(f"{y}: {dict(status) or 'up to date'}; satellite {st.get('satellite')}")
    regs = regions()
    seasons = {}
    for y in range(FIRST_YEAR, today.year + 1):
        s = summarise(y, regs)
        if s:
            seasons[str(y)] = s
    if not seasons:
        print("nothing cached yet")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "source": "LSA SAF MSG-IODC FRP-PIXEL (SEVIRI, 15 min), CC BY 4.0",
           "season_start": f"{SEASON[0][0]:02d}-{SEASON[0][1]:02d}", "window": [f"{m:02d}-{d:02d}" for m, d in WINDOW],
           "cell_deg": CELL, "seasons": seasons}
    tmp = OUT / ".geostationary.json.tmp"
    tmp.write_text(json.dumps(res, separators=(",", ":")))
    os.replace(tmp, OUT / "geostationary.json")
    print("wrote", OUT / "geostationary.json", "seasons:", ", ".join(seasons))


if __name__ == "__main__":
    main()
