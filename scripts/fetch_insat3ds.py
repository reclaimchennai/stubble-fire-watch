#!/usr/bin/env python3
"""INSAT-3DS fire, read from MOSDAC's open gallery maps: when in the day each fire cell is first seen.

INSAT-3DS (82 E) images India every 30 minutes and sees Punjab at ~36 deg from vertical, a better angle than
Meteosat. Its fire points (L2P FIR) need a MOSDAC login as KML, but MOSDAC also publishes every slot as a map image
(mosdac.gov.in/look/3S_IMG/preview/<yyyy>/<ddMON>/3SIMG_<ddMONyyyy>_<hhmm>_L2P_FIR_V01R00.jpg, UTC), with fire pixels
drawn as red markers. This script reads the markers back off the map:

- the map is Mercator: gridlines put latitude at y = 1912 - 1870.5 * ln(tan(45 + lat/2)) and longitude at
  30.54 px per degree; X70 (the column of 70 E) and DY were fitted against Meteosat detections in the same slots;
- a marker is ~13 x 11 px (~0.35 deg, ~36 km): red pixels are eroded by a 9 x 9 disc so each marker shrinks to its
  centre, and merged markers over Punjab shrink to the band of their centres;
- a fire cell is a 0.1 deg cell holding a marker centre; its first-seen time is the first slot of the IST day it holds one.
Positions are therefore good to ~0.1-0.2 deg and counts are not fire pixels. The timing (30-min slots) is the point.

The product is near-empty before 2025 (MOSDAC's 2024 maps show almost no fires in Punjab in Oct-Nov, while Meteosat saw
thousands), so seasons start in 2025. The 2025 map layout (2002 x 2242 px) is required; other layouts are skipped.

Outputs
  data/raw/insat3ds/<year>.json                 per slot: marker-centre pixels on a 3 px grid, or "missing"/"layout"
  data/geostationary/insat3ds.json              per season and region: hour first seen, daily fire cells, coverage
  data/geostationary/insat3ds_cells_<year>.json first-seen fire cells (lon, lat, minutes from season start) for the map
Usage: fetch_insat3ds.py [--season 2025 2026] [--no-download]   (default: this year's season, during 1 Oct - 30 Nov)
"""
import argparse
import datetime as dt
import json
import math
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import Point, shape
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "insat3ds"
OUT = ROOT / "data" / "geostationary"
URL = "https://mosdac.gov.in/look/3S_IMG/preview/{y}/{dm}/3SIMG_{dm}{y}_{hm}_L2P_FIR_V01R00.jpg"
MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
SIZE = (2002, 2242)                       # width, height of the 2025 layout
Y0, YS = 1912.0, 1870.5                   # equator row, px per Mercator unit
XS = 30.54                                # px per degree of longitude
X70, DY = 782.0, 3.0                      # 70 E column and a row offset, fitted against Meteosat: 88% of centres within 0.1 deg
GRID = 3                                  # cache marker centres on a 3 px grid (~0.1 deg)
CELL = 0.1
BOX = (69.0, 27.5, 78.0, 34.2)            # lon0, lat0, lon1, lat1: both Punjabs and Haryana
REGIONS = {"Punjab": ("India", "Punjab"), "Haryana": ("India", "Haryana"), "Punjab (PK)": ("Pakistan", "Punjab")}
IST = dt.timedelta(hours=5, minutes=30)
SEASON = ((10, 1), (11, 30))
WINDOW = ((10, 15), (11, 15))
FIRST_YEAR = 2025
DISC = np.hypot(*np.mgrid[-4:5, -4:5]) <= 4.2


def lonlat(x, y):
    m = (Y0 - (y + DY)) / YS
    return 70 + (x - X70) / XS, math.degrees(2 * math.atan(math.exp(m)) - math.pi / 2)


def pixel(lon, lat):
    return X70 + (lon - 70) * XS, Y0 - YS * math.log(math.tan(math.radians(45 + lat / 2))) - DY


def crop_box():
    (xa, ya), (xb, yb) = pixel(BOX[0] - 0.5, BOX[3] + 0.5), pixel(BOX[2] + 0.5, BOX[1] - 0.5)
    return max(0, int(xa)), max(260, int(ya)), min(SIZE[0], int(xb) + 1), min(SIZE[1], int(yb) + 1)


def centres(path):
    """Marker-centre pixels inside the box, on a GRID px lattice; None if the image is not the expected layout."""
    im = Image.open(path)
    if im.size != SIZE:
        return None
    x0, y0, x1, y1 = crop_box()
    a = np.asarray(im.convert("RGB").crop((x0, y0, x1, y1))).astype(np.int16)
    red = (a[..., 0] > 180) & (a[..., 1] < 90) & (a[..., 2] < 90)
    if not red.any():
        return []
    core = ndimage.binary_erosion(red, structure=DISC)
    lab, n = ndimage.label(red)
    # a marker clipped by the crop edge or slightly smaller than the disc still keeps one centre pixel
    for i, sl in enumerate(ndimage.find_objects(lab)):
        comp = lab[sl] == i + 1
        if not core[sl][comp].any():
            cy, cx = ndimage.center_of_mass(comp)
            core[sl[0].start + int(round(cy)), sl[1].start + int(round(cx))] = True
    ys, xs = np.nonzero(core)
    return sorted({((x + x0) // GRID, (y + y0) // GRID) for x, y in zip(xs.tolist(), ys.tolist())})


def slots(year):
    t0 = dt.datetime.combine(dt.date(year, *SEASON[0]), dt.time()) - IST
    t1 = dt.datetime.combine(dt.date(year, *SEASON[1]) + dt.timedelta(1), dt.time()) - IST
    t = t0.replace(minute=0 if t0.minute < 30 else 30)
    out = []
    while t < t1:
        out.append(t)
        t += dt.timedelta(minutes=30)
    return out


def fetch_season(year, workers=4):
    RAW.mkdir(parents=True, exist_ok=True)
    cf = RAW / f"{year}.json"
    cache = json.loads(cf.read_text()) if cf.exists() else {"slots": {}}
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    todo = [t for t in slots(year) if f"{t:%Y%m%d%H%M}" not in cache["slots"] and t + dt.timedelta(minutes=50) < now]
    if not todo:
        return cache, Counter()
    tmp = Path(tempfile.mkdtemp(prefix="g3s-", dir=RAW))

    def get(t):
        dm = f"{t.day:02d}{MON[t.month - 1]}"
        path = tmp / f"{t:%Y%m%d%H%M}.jpg"
        for _ in range(3):
            r = subprocess.run(["curl", "-s", "-m", "90", "-o", str(path), "-w", "%{http_code}",
                                URL.format(y=t.year, dm=dm, hm=f"{t:%H%M}")], capture_output=True, text=True)
            code = r.stdout.strip()[-3:]
            try:
                if code == "200":
                    c = centres(path)
                    return t, "layout" if c is None else c
                if code == "404":
                    return t, "missing" if now - t > dt.timedelta(days=2) else None
            except Exception:
                pass
            finally:
                path.unlink(missing_ok=True)
        return t, None

    status = Counter()
    with ThreadPoolExecutor(workers) as ex:
        for n, fut in enumerate(as_completed([ex.submit(get, t) for t in todo])):
            t, v = fut.result()
            status["failed" if v is None else v if isinstance(v, str) else "ok"] += 1
            if v is not None:
                cache["slots"][f"{t:%Y%m%d%H%M}"] = v
            if n % 200 == 199:
                cf.write_text(json.dumps(cache, separators=(",", ":")))
    cf.write_text(json.dumps(cache, separators=(",", ":")))
    tmp.rmdir()
    return cache, status


def regions():
    g = json.load(open(ROOT / "data" / "geo" / "states.geojson"))
    out = {}
    for key, (country, name) in REGIONS.items():
        f = next(f for f in g["features"] if f["properties"].get("country") == country and f["properties"]["name"] == name)
        out[key] = prep(shape(f["geometry"]))
    return out


def season_days(year, span):
    d0, d1 = dt.date(year, *span[0]), dt.date(year, *span[1])
    return [d0 + dt.timedelta(n) for n in range((d1 - d0).days + 1)]


def summarise(year, regs):
    cf = RAW / f"{year}.json"
    if not cf.exists():
        return None, None
    cache = json.loads(cf.read_text())["slots"]
    all_slots = slots(year)
    have = [t for t in all_slots if isinstance(cache.get(f"{t:%Y%m%d%H%M}"), list)]
    if not have:
        return None, None
    cell_reg = {}

    def region_of(c):
        if c not in cell_reg:
            p = Point(c[1] * CELL, c[0] * CELL)
            cell_reg[c] = next((k for k, g in regs.items() if g.contains(p)), None)
        return cell_reg[c]

    first = {}                                            # (IST date, cell) -> first IST time
    for t in have:
        ti = t + IST
        cells = set()
        for gx, gy in cache[f"{t:%Y%m%d%H%M}"]:
            lon, lat = lonlat(gx * GRID + 1, gy * GRID + 1)
            if BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]:
                cells.add((round(lat / CELL), round(lon / CELL)))
        for c in cells:
            k = (ti.date(), c)
            if k not in first:
                first[k] = ti
    days, win = season_days(year, SEASON), set(season_days(year, WINDOW))
    out = {}
    for reg in regs:
        ks = [(k, t) for k, t in first.items() if region_of(k[1]) == reg]
        fh = Counter(t.hour for (d, _), t in ks if d in win)
        dc = Counter(d for (d, _), _ in ks)
        out[reg] = {"daily_cells": [dc.get(d, 0) for d in days],
                    "window": {"fire_cells": sum(fh.values()), "first_seen_hour": [fh.get(h, 0) for h in range(24)]}}
    hours = Counter((t + IST).hour for t in have if (t + IST).date() in win)
    nwin = sum(1 for t in all_slots if (t + IST).date() in win)
    status = Counter("ok" if isinstance(v, list) else v for v in cache.values())
    t0 = dt.datetime.combine(days[0], dt.time())          # IST midnight, season start
    cells = sorted([round(c[1] * CELL, 1), round(c[0] * CELL, 1), int((t - t0).total_seconds() // 60)]
                   for (d, c), t in first.items() if region_of(c))
    summ = {
        "through": (max(have) + IST).isoformat(timespec="minutes"),
        "complete": all(f"{t:%Y%m%d%H%M}" in cache for t in all_slots),
        "window_complete": all(f"{t:%Y%m%d%H%M}" in cache for t in all_slots if (t + IST).date() in win),
        "slots": len(all_slots), "slots_ok": status.get("ok", 0), "slots_missing": status.get("missing", 0),
        "slots_layout": status.get("layout", 0),
        "window_images_by_hour": [hours.get(h, 0) for h in range(24)], "window_slots_per_hour": nwin // 24,
        "regions": out,
    }
    return summ, {"season_start_ist": t0.isoformat(), "cell_deg": CELL, "cells": cells}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, nargs="+")
    ap.add_argument("--no-download", action="store_true")
    a = ap.parse_args()
    today = dt.date.today()
    years = a.season or ([today.year] if dt.date(today.year, *SEASON[0]) <= today <= dt.date(today.year, 12, 2) else [])
    if not a.no_download:
        for y in years:
            _, st = fetch_season(y)
            print(f"{y}: {dict(st) or 'up to date'}")
    regs = regions()
    OUT.mkdir(parents=True, exist_ok=True)
    seasons = {}
    for y in range(FIRST_YEAR, today.year + 1):
        s, cells = summarise(y, regs)
        if not s:
            continue
        seasons[str(y)] = s
        tmp = OUT / f".insat3ds_cells_{y}.json.tmp"
        tmp.write_text(json.dumps(cells, separators=(",", ":")))
        os.replace(tmp, OUT / f"insat3ds_cells_{y}.json")
    if not seasons:
        print("nothing cached yet")
        return
    res = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "source": "INSAT-3DS imager L2P fire (ISRO/SAC via MOSDAC), read from the published gallery maps, 30 min",
           "window": [f"{m:02d}-{d:02d}" for m, d in WINDOW], "cell_deg": CELL,
           "georef": {"x70": X70, "dy": DY}, "seasons": seasons}
    tmp = OUT / ".insat3ds.json.tmp"
    tmp.write_text(json.dumps(res, separators=(",", ":")))
    os.replace(tmp, OUT / "insat3ds.json")
    print("wrote", OUT / "insat3ds.json", "seasons:", ", ".join(seasons))


if __name__ == "__main__":
    main()
