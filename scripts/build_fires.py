#!/usr/bin/env python3
"""Fuse NASA FIRMS detections (2003 -> today) into compact files for the dashboard.

Inputs
  data/raw/firms_country/*.csv  public FIRMS yearly country files (MODIS 2003+, VIIRS S-NPP 2012+, NOAA-20 2018+)
  data/raw/firms_api/*.csv      current year from the FIRMS area API (SP + NRT, incl. NOAA-21, Landsat)
  data/geo/districts.geojson    from build_geo.py

Filtering
  - region bbox 66-98E, 6-37.5N (all of India; until Oct 2026 it was 68.5-89E, 21-35.5N)
  - API rows outside every India/Pakistan district are dropped (the archive country files hold only those two)
  - archive rows with type 2 (static land source: industry, gas flares) or 3 (offshore) are dropped;
    NRT rows have no type column, so cells (0.01 deg) flagged as static in any archive year are dropped too
  - every detection is kept per sensor; no cross-sensor merging (counts are reported per sensor)

Outputs (data/fires/)
  points_YYYY.bin  14-byte records: u16 lonq, u16 latq, u16 doy, u16 hhmm, u16 frp*10, u16 district(+1, 0=none),
                   u8 sensor, u8 confidence(0 low,1 nominal,2 high)
  summary.json     daily counts + FRP by state/sensor/year, district season totals, metadata
"""
import csv
import datetime as dt
import glob
import json
import re
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
RAW_C = ROOT / "data" / "raw" / "firms_country"
RAW_A = ROOT / "data" / "raw" / "firms_api"
OUT = ROOT / "data" / "fires"
W, S, E, N = 66.0, 6.0, 98.0, 37.5
RES = 0.01
SENSORS = ["MODIS Terra", "MODIS Aqua", "VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21", "Landsat 8/9", "CREAMS (IARI)"]
CREAMS_EVENTS = ROOT / "data" / "creams" / "creams_events.csv"
SAT_CODE = {"Terra": 0, "T": 0, "Aqua": 1, "A": 1, "N": 2, "1": 3, "N20": 3, "J1": 3, "N21": 4, "2": 4, "J2": 4,
            "L8": 5, "L9": 5, "LANDSAT": 5}
FOCUS = ["Punjab", "Haryana", "Delhi", "Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Uttarakhand",
         "Himachal Pradesh", "Bihar", "Punjab (PK)", "Sindh (PK)", "Khyber Pakhtunkhwa (PK)"]
SEASONS = {"rice": ((9, 15), (11, 30)), "wheat": ((4, 1), (5, 31))}
BASIS = {"modis": (0, 1), "snpp": (2,), "viirs": (2, 3, 4)}  # sensor groups for year-on-year comparison


def district_raster():
    gj = json.loads((ROOT / "data" / "geo" / "districts.geojson").read_text())
    w, h = round((E - W) / RES), round((N - S) / RES)
    img = Image.new("I;16", (w, h), 0)
    dr = ImageDraw.Draw(img)
    meta = []
    for f in gj["features"]:
        p = f["properties"]
        st = p["state"] + (" (PK)" if p["country"] == "Pakistan" else "")
        meta.append({"id": p["id"], "name": p["name"], "state": st})
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            pts = [((x - W) / RES, (N - y) / RES) for x, y in poly[0]]
            if len(pts) > 2:
                dr.polygon(pts, fill=p["id"] + 1)
            for hole in poly[1:]:
                dr.polygon([((x - W) / RES, (N - y) / RES) for x, y in hole], fill=0)
    return np.array(img, dtype=np.uint16), meta


def conf_code(c):
    c = (c or "").strip().lower()
    if c in ("l", "low"):
        return 0
    if c in ("h", "high"):
        return 2
    if c in ("n", "nominal"):
        return 1
    try:
        v = float(c)
        return 0 if v < 30 else (1 if v < 80 else 2)
    except ValueError:
        return 1


def sensor_code(row, fname):
    if "LANDSAT" in fname:
        return 5
    sat = row.get("satellite", "")
    if "modis" in fname.lower() and sat not in ("Terra", "Aqua", "T", "A"):
        return 0
    return SAT_CODE.get(sat, 2 if "SNPP" in fname or "snpp" in fname else 3)


def archive_files():
    """{year: [paths]} of the public yearly country files."""
    out = defaultdict(list)
    for f in sorted(glob.glob(str(RAW_C / "*.csv"))):
        out[int(re.search(r"_(\d{4})_", Path(f).name).group(1))].append(f)
    return out


def static_cells(arch):
    """0.01-deg cells the archive flags as static (type 2/3) in any year; cached against the file list."""
    sig = json.dumps([(Path(f).name, Path(f).stat().st_size) for y in sorted(arch) for f in arch[y]] + [[W, S, E, N]])
    cache = ROOT / "data" / "raw" / "firms_static_cells.json"
    if cache.exists():
        c = json.loads(cache.read_text())
        if c.get("sig") == sig:
            return {tuple(x) for x in c["cells"]}
    cells = set()
    for y in sorted(arch):
        for f in arch[y]:
            for r in read_csv(f, archive=True):
                if r[0] == "STATIC":
                    cells.add((round(r[1] / RES), round(r[2] / RES)))
    cache.write_text(json.dumps({"sig": sig, "cells": sorted(cells)}))
    return cells


def api_rows():
    """{year: [rows]} from the API cache; once a year has country files only NOAA-21 is taken from the API."""
    archived = set(archive_files())
    out, seen = defaultdict(list), set()
    for f in sorted(glob.glob(str(RAW_A / "*.csv"))):
        for r in read_csv(f, archive=False):
            if r[0] == "STATIC":
                continue
            y = int(r[2][:4])
            if y in archived and r[5] != 4:
                continue
            k = (r[0], r[1], r[2], r[3], r[5])
            if k in seen:
                continue
            seen.add(k)
            out[y].append(r)
    return out


def rows_for_year(year, files, api, creams):
    for f in files:
        for r in read_csv(f, archive=True):
            if r[0] != "STATIC":
                yield r
    yield from api.get(year, ())
    yield from creams.get(year, ())


def read_csv(path, archive):
    name = Path(path).name
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (KeyError, ValueError):
                continue
            if not (W <= lon < E and S <= lat < N):
                continue
            typ = row.get("type", "")
            if typ in ("2", "3"):
                yield ("STATIC", lon, lat)
                continue
            try:
                frp = max(0.0, float(row.get("frp") or 0))   # a few north-eastern VIIRS rows carry small negative FRP
            except ValueError:
                frp = 0.0
            yield (lon, lat, row["acq_date"], int(row["acq_time"] or 0), frp, sensor_code(row, name),
                   conf_code(row.get("confidence")), archive)


def iter_creams():
    """Residue-burning events listed in CREAMS bulletin annexures (times printed in IST, FRP units unclear -> 0)."""
    out = defaultdict(list)
    if not CREAMS_EVENTS.exists():
        return out
    with open(CREAMS_EVENTS, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
                d = dt.datetime.fromisoformat(f"{r['date']}T{r['acq_time'] or '00:00:00'}") - dt.timedelta(hours=5, minutes=30)
            except ValueError:
                continue
            if W <= lon < E and S <= lat < N:
                out[d.year].append((lon, lat, d.date().isoformat(), d.hour * 100 + d.minute, 0.0, 6, 1, True))
    return out


def in_season(d, season):
    (m0, d0), (m1, d1) = SEASONS[season]
    return (m0, d0) <= (d.month, d.day) <= (m1, d1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    raster, dmeta = district_raster()
    nd = len(dmeta)
    states = sorted({m["state"] for m in dmeta})
    st_index = {s: i for i, s in enumerate(states)}
    dist_state = np.array([st_index[m["state"]] for m in dmeta])

    arch = archive_files()
    static = static_cells(arch)
    api = api_rows()
    creams = iter_creams()
    years = sorted(set(arch) | set(api) | set(creams))
    last_utc_row = None

    daily = {}       # year -> sensor -> state -> [doy counts], frp
    dist_season = {}  # year -> season -> sensor -> [district counts]
    hourly = {}       # year -> sensor -> [24] rice-season detections by IST hour
    dist_daily = {}   # year -> basis -> district -> [[day index from Sep 15, count], ...] (rice season, sparse)
    files = {}
    for year in years:
        ndays = 366 if year % 4 == 0 else 365
        cnt = np.zeros((len(SENSORS), len(states) + 1, ndays), np.int32)
        frp = np.zeros((len(SENSORS), len(states) + 1, ndays), np.float32)
        ds = {s: np.zeros((len(SENSORS), nd), np.int32) for s in SEASONS}
        hr = np.zeros((len(SENSORS), 24), np.int32)
        sep15 = dt.date(year, 9, 15)
        dd = {b: np.zeros((nd, 77), np.int32) for b in BASIS}
        bufs = {s: bytearray() for s in SEASONS}
        kept = 0
        for lon, lat, date, hhmm, f, sen, conf, archive in rows_for_year(year, arch.get(year, ()), api, creams):
            if not archive and (round(lon / RES), round(lat / RES)) in static:
                continue
            d = dt.date.fromisoformat(date)
            doy = d.timetuple().tm_yday
            col, row_ = int((lon - W) / RES), int((N - lat) / RES)
            did = int(raster[row_, col]) if 0 <= row_ < raster.shape[0] and 0 <= col < raster.shape[1] else 0
            if not did and not archive:   # API rows from Nepal, Bangladesh, Myanmar, Sri Lanka, sea
                continue
            si = dist_state[did - 1] if did else len(states)
            cnt[sen, si, doy - 1] += 1
            frp[sen, si, doy - 1] += f
            for s in SEASONS:
                if did and in_season(d, s):
                    ds[s][sen, did - 1] += 1
            if in_season(d, "rice"):
                if did:
                    for b, group in BASIS.items():
                        if sen in group:
                            dd[b][did - 1, (d - sep15).days] += 1
                ist = ((hhmm // 100) * 60 + hhmm % 100 + 330) // 60 % 24
                hr[sen, ist] += 1
            season = next((s for s in SEASONS if in_season(d, s)), None)
            if season:
                bufs[season] += struct.pack("<HHHHHHBB", round((lon - W) / (E - W) * 65535), round((lat - S) / (N - S) * 65535),
                                       doy, hhmm, min(65535, round(f * 10)), did, sen, conf)
            kept += 1
            if last_utc_row is None or (date, hhmm) > last_utc_row:
                last_utc_row = (date, hhmm)
        for s, b in bufs.items():
            (OUT / f"points_{year}_{s}.bin").write_bytes(bytes(b))
        files[year] = {"points": kept, **{s: len(b) // 14 for s, b in bufs.items()}}
        names = states + ["Outside districts"]
        daily[year] = {SENSORS[s]: {names[i]: {"n": cnt[s, i].tolist(), "frp": [round(x) for x in frp[s, i].tolist()]}
                                     for i in range(len(names)) if cnt[s, i].any()}
                       for s in range(len(SENSORS)) if cnt[s].any()}
        dist_season[year] = {season: {SENSORS[s]: arr[s].tolist() for s in range(len(SENSORS)) if arr[s].any()}
                             for season, arr in ds.items()}
        dist_daily[year] = {b: {str(i): [[j, int(v)] for j, v in enumerate(arr[i]) if v]
                                for i in range(nd) if arr[i].any()} for b, arr in dd.items()}
        hourly[year] = {SENSORS[s]: hr[s].tolist() for s in range(len(SENSORS)) if hr[s].any()}
        print(year, kept)

    last_date, hhmm = last_utc_row                         # newest detection: (date, hhmm UTC)
    last_utc = f"{last_date}T{hhmm // 100:02d}:{hhmm % 100:02d}Z"
    summary = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "last_detection_date": last_date, "last_detection_utc": last_utc,
        "bbox": [W, S, E, N], "sensors": SENSORS, "states": states + ["Outside districts"], "focus_states": FOCUS,
        "districts": dmeta, "seasons": {k: [f"{a[0]:02d}-{a[1]:02d}", f"{b[0]:02d}-{b[1]:02d}"] for k, (a, b) in SEASONS.items()},
        "static_cells_removed": len(static),
        "files": files, "daily": daily, "district_season": dist_season, "hourly_ist_rice": hourly,
        "record": "<HHHHHHBB lonq latq doy hhmm frp10 district+1 sensor conf",
    }
    (OUT / "district_daily.json").write_text(json.dumps(dist_daily, separators=(",", ":")))
    (OUT / "summary.json").write_text(json.dumps(summary, separators=(",", ":")))
    # tiny file the map polls every few minutes; summary.json is only refetched when this changes
    (OUT / "live.json").write_text(json.dumps({k: summary[k] for k in ("generated", "last_detection_date", "last_detection_utc", "bbox")}))
    print("summary written", (OUT / "summary.json").stat().st_size)


if __name__ == "__main__":
    main()
