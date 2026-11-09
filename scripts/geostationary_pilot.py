#!/usr/bin/env python3
"""Pilot: can a geostationary fire product see the Punjab/Haryana fires lit after the ~13:30 IST polar passes?

Window 15 Oct - 15 Nov of 2024 and 2025, Punjab + Haryana (state polygons from data/geo/states.geojson).

Sources
  FIRMS MODIS / VIIRS S-NPP / VIIRS NOAA-20 yearly country CSVs (data/raw/firms_country, already cached)
  MODIS MCD64A1 burned area by day (data/burnscar/burnscar.json, from scripts/burnscar.py)
  LSA SAF MSG-IODC FRP-PIXEL (SEVIRI on Meteosat-9 at 45.5 E, every 15 min), list product over the IODC disk.
    Needs a free LSA SAF data-server account: LSASAF_USER / LSASAF_PASSWORD in .env. Without them the
    geostationary part is skipped and only the polar baseline is reported.

Besides counting detections by IST hour, the VIIRS detections of each overpass are summed into ~4 km cells
(about one SEVIRI pixel over Punjab, where the view zenith is ~48 deg) to estimate how much of what VIIRS sees
would clear SEVIRI's ~30-40 MW (nadir) detection floor at all.

Outputs
  data/raw/geostationary/iodc/<yyyymmdd>/<file>      raw LSA SAF HDF5 (list product, ~90 KB each)
  data/raw/geostationary/iodc_<year>.csv            IODC fire pixels inside the region
  data/raw/geostationary/pilot.json                 all figures quoted in docs/geostationary_pilot.md
Usage: geostationary_pilot.py [--years 2024 2025] [--no-download]
"""
import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "geostationary"
FIRMS = ROOT / "data" / "raw" / "firms_country"
IODC = "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MSG-IODC/FRP-PIXEL/HDF5"
STATES = ("Punjab", "Haryana")
IST = dt.timedelta(hours=5, minutes=30)
LATE = set(range(14, 24)) | {0}          # 14:00-01:00 IST, after the afternoon passes, before the night pass
LATE15 = LATE - {14}                     # Aqua has drifted to 14-16 h IST and VIIRS swath edges reach 14:59
SENSORS = {"MODIS": "modis", "VIIRS S-NPP": "viirs-snpp", "VIIRS NOAA-20": "viirs-jpss1"}
CELL = 0.04                              # degrees, ~4 km: one SEVIRI pixel footprint over Punjab


def env():
    out = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def region():
    g = json.load(open(ROOT / "data" / "geo" / "states.geojson"))
    geom = unary_union([shape(f["geometry"]) for f in g["features"]
                        if f["properties"].get("country") == "India" and f["properties"]["name"] in STATES])
    return geom, prep(geom)


WIN = ((10, 15), (11, 15))               # season window; --window overrides it for placebo runs
TAG = ""


def window(year):
    return dt.date(year, *WIN[0]), dt.date(year, *WIN[1])


def hour_hist(times_ist):
    h = Counter(t.hour for t in times_ist)
    return [h.get(i, 0) for i in range(24)]


def late_share(hist, hours=LATE):
    n = sum(hist)
    return round(sum(hist[i] for i in hours) / n, 4) if n else None


def firms(year, reg):
    d0, d1 = window(year)
    out = {}
    cells_all = {}
    for name, key in SENSORS.items():
        rows = []
        with open(FIRMS / f"{key}_{year}_India.csv") as fh:
            for r in csv.DictReader(fh):
                d = dt.date.fromisoformat(r["acq_date"])
                if not d0 <= d <= d1:
                    continue
                lat, lon = float(r["latitude"]), float(r["longitude"])
                if not reg.contains(Point(lon, lat)):
                    continue
                t = r["acq_time"].zfill(4)
                utc = dt.datetime.combine(d, dt.time(int(t[:2]), int(t[2:])))
                rows.append((utc + IST, lat, lon, float(r["frp"] or 0), r["daynight"]))
        hist = hour_hist([r[0] for r in rows])
        frp = np.array([r[3] for r in rows]) if rows else np.zeros(0)
        # sum each overpass's detections into ~4 km cells: what one SEVIRI pixel would integrate
        cells = defaultdict(float)
        for t, lat, lon, f, _ in rows:
            cells[(t.date(), t.hour, int(lat // CELL), int(lon // CELL))] += f
        cfrp = np.array(list(cells.values())) if cells else np.zeros(0)
        out[name] = {
            "detections": len(rows), "by_ist_hour": hist, "late_share": late_share(hist), "late15_share": late_share(hist, LATE15),
            "night_detections": sum(1 for r in rows if r[4] == "N"),
            "frp_median_mw": round(float(np.median(frp)), 1) if len(frp) else None,
            "frp_p90_mw": round(float(np.percentile(frp, 90)), 1) if len(frp) else None,
            "cells_4km": len(cfrp),
            "cells_4km_ge_40mw": int((cfrp >= 40).sum()), "cells_4km_ge_60mw": int((cfrp >= 60).sum()),
            "frp_total_mw": round(float(frp.sum())),
        }
        cells_all[name] = cells
    return out, cells_all


def burned(year):
    """MCD64A1 burned hectares in the window, Punjab + Haryana, from burnscar.json (day index 0 = Sep 15)."""
    s = json.load(open(ROOT / "data" / "burnscar" / "burnscar.json")).get(str(year))
    if not s:
        return None
    d0, d1 = window(year)
    i0 = (d0 - dt.date(year, 9, 15)).days
    i1 = (d1 - dt.date(year, 9, 15)).days
    ha = sum(sum(s["daily_burned_ha"][st][i0:i1 + 1]) for st in STATES)
    return {"burned_ha": round(ha), "missing_months": s.get("missing_months", []), "complete": s.get("complete")}


def fetch_iodc(year, user, pw, reg_bounds):
    """Download the window's 15-min IODC list products (curl, parallel) and keep fire pixels in the box."""
    d0, d1 = window(year)
    days = [d0 + dt.timedelta(n) for n in range((d1 - d0).days + 1)]
    # the window is in IST; include the UTC day before so 00:00-05:30 IST on d0 is covered
    days = [d0 - dt.timedelta(1)] + days
    jobs = []
    for d in days:
        ddir = RAW / "iodc" / d.strftime("%Y%m%d")
        ddir.mkdir(parents=True, exist_ok=True)
        for m in range(0, 24 * 60, 15):
            stamp = f"{d:%Y%m%d}{m // 60:02d}{m % 60:02d}"
            name = f"HDF5_LSASAF_MSG-IODC_FRP-PIXEL-ListProduct_IODC-Disk_{stamp}"
            jobs.append((f"{IODC}/{d:%Y/%m/%d}/{name}", ddir / name))

    def get(job):
        url, path = job
        if path.exists() and path.stat().st_size > 0:
            return "cached"
        cfg = f'user = "{user}:{pw}"\n'          # passed on stdin so the password never reaches argv
        for i in range(4):
            r = subprocess.run(["curl", "-sf", "-m", "120", "-K", "-", "-o", str(path) + ".part", url],
                               input=cfg, text=True)
            if r.returncode == 0:
                os.replace(str(path) + ".part", path)
                return "ok"
            if r.returncode == 22:               # HTTP error: 404 = slot missing in the archive
                return "missing"
            time.sleep(3 * (i + 1))
        return "failed"

    with ThreadPoolExecutor(8) as ex:
        status = Counter(ex.map(get, jobs))
    return [p for _, p in jobs if p.exists()], dict(status)


def read_iodc(path, bounds):
    import h5py
    lon0, lat0, lon1, lat1 = bounds
    with h5py.File(path, "r") as f:
        def ds(name):
            if name not in f:
                return None
            d = f[name]
            v = d[:].astype("float64")
            miss = d.attrs.get("MISSING_VALUE")
            if miss is not None:
                v[d[:] == miss] = np.nan
            sc, off = d.attrs.get("SCALING_FACTOR"), d.attrs.get("OFFSET")
            if sc is not None:
                v = v / float(np.ravel(sc)[0]) + (float(np.ravel(off)[0]) if off is not None else 0.0)
            return v
        lat, lon, frp = ds("LATITUDE"), ds("LONGITUDE"), ds("FRP")
        if lat is None or len(lat) == 0:
            return []
        conf, vza, area, acq = ds("FIRE_CONFIDENCE"), ds("PIXEL_VZA"), ds("PIXEL_SIZE"), ds("ACQTIME")
    keep = np.where((lon >= lon0) & (lon <= lon1) & (lat >= lat0) & (lat <= lat1))[0]
    out = []
    for i in keep:
        out.append({"lat": round(lat[i], 4), "lon": round(lon[i], 4), "frp": round(frp[i], 1),
                    "conf": None if conf is None else round(conf[i], 2),
                    "vza": None if vza is None else round(vza[i], 1),
                    "area_km2": None if area is None else round(area[i], 1),
                    "acqtime": None if acq is None else acq[i]})
    return out


def iodc(year, reg_geom, reg, creds, download):
    d0, d1 = window(year)
    cache = RAW / f"iodc_{year}{TAG}.csv"
    status = None
    if download and creds:
        files, status = fetch_iodc(year, *creds, reg_geom.bounds)
        rows = []
        for p in sorted(files):
            stamp = p.name.rsplit("_", 1)[1]
            slot = dt.datetime.strptime(stamp, "%Y%m%d%H%M")
            for r in read_iodc(p, reg_geom.bounds):
                if reg.contains(Point(r["lon"], r["lat"])):
                    r["slot_utc"] = slot.isoformat()
                    rows.append(r)
        with open(cache, "w", newline="") as fh:
            w = csv.DictWriter(fh, ["slot_utc", "lat", "lon", "frp", "conf", "vza", "area_km2", "acqtime"])
            w.writeheader()
            w.writerows(rows)
    if not cache.exists():
        return None
    rows = list(csv.DictReader(open(cache)))
    times = []
    for r in rows:
        t = dt.datetime.fromisoformat(r["slot_utc"]) + IST
        if d0 <= t.date() <= d1:
            times.append((t, r))
    hist = hour_hist([t for t, _ in times])
    frp = np.array([float(r["frp"]) for _, r in times]) if times else np.zeros(0)
    days_slots = len({t.replace(minute=0) for t, _ in times})
    return {"detections": len(times), "by_ist_hour": hist, "late_share": late_share(hist), "late15_share": late_share(hist, LATE15),
            "fire_hours": days_slots, "download": status,
            "frp_median_mw": round(float(np.median(frp)), 1) if len(frp) else None,
            "frp_total_mw15": round(float(frp.sum())),
            "vza_median": round(float(np.nanmedian([float(r["vza"]) for _, r in times if r["vza"]])), 1)
            if times else None,
            "rows": [(t.isoformat(), float(r["lat"]), float(r["lon"]), float(r["frp"])) for t, r in times]}


def matchup(geo_rows, viirs_cells, radius_cells=1, hours=1):
    """Share of IODC detections within ~1 SEVIRI pixel and +-1 h of a VIIRS 4 km cell (and vice versa)."""
    if not geo_rows:
        return None
    vk = defaultdict(set)
    for (d, h, i, j) in viirs_cells:
        vk[(d, h)].add((i, j))
    near_pass = hit = 0
    for t, lat, lon, _ in geo_rows:
        t = dt.datetime.fromisoformat(t)
        i, j = int(lat // CELL), int(lon // CELL)
        slots = [((t + dt.timedelta(hours=k)).date(), (t + dt.timedelta(hours=k)).hour) for k in range(-hours, hours + 1)]
        cand = set().union(*(vk.get(s, set()) for s in slots))
        if not cand:
            continue
        near_pass += 1
        if any((i + a, j + b) in cand for a in range(-radius_cells, radius_cells + 1) for b in range(-radius_cells, radius_cells + 1)):
            hit += 1
    return {"iodc_within_1h_of_viirs_pass": near_pass, "of_which_colocated": hit,
            "colocated_share": round(hit / near_pass, 3) if near_pass else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--window", nargs=2, metavar="MM-DD", help="other window, e.g. 12-05 12-25 (placebo)")
    a = ap.parse_args()
    global WIN, TAG
    if a.window:
        WIN = tuple(tuple(int(x) for x in w.split("-")) for w in a.window)
        TAG = "_" + "_".join(a.window)
    e = env()
    creds = (e["LSASAF_USER"], e["LSASAF_PASSWORD"]) if e.get("LSASAF_USER") and e.get("LSASAF_PASSWORD") else None
    reg_geom, reg = region()
    RAW.mkdir(parents=True, exist_ok=True)
    res = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "region": list(STATES),
           "late_window_ist": "14:00-01:00", "years": {}}
    for y in a.years:
        f, cells = firms(y, reg)
        g = iodc(y, reg_geom, reg, creds, not a.no_download)
        yr = {"window": [d.isoformat() for d in window(y)], "firms": f, "mcd64a1": burned(y)}
        if g:
            viirs = {**cells["VIIRS S-NPP"], **cells["VIIRS NOAA-20"]}
            yr["iodc_match_viirs"] = matchup(g["rows"], viirs)
            g.pop("rows")
            yr["iodc"] = g
        res["years"][str(y)] = yr
        print(y, json.dumps({k: v for k, v in yr.items() if k != "firms"}, default=str)[:600])
        for n, v in f.items():
            print(f"  {n:14s} n={v['detections']:6d} late={v['late_share']} late15={v['late15_share']} cells>=40MW={v['cells_4km_ge_40mw']}/{v['cells_4km']}")
    out = RAW / f"pilot{TAG}.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print("wrote", out, "| IODC:", "credentials found" if creds else "no LSASAF_USER/LSASAF_PASSWORD in .env, skipped")


if __name__ == "__main__":
    main()
