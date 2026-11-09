#!/usr/bin/env python3
"""Pilot: INSAT-3DR (74 E) and INSAT-3DS (82 E) active fire from MOSDAC, against Meteosat IODC and FIRMS.

Same region, window and measures as scripts/geostationary_pilot.py (Punjab + Haryana, 15 Oct - 15 Nov), so the two
geostationary products can be compared directly: detections and ~4 km fire cells by IST hour, share first seen after
15:00 IST, daily correlation with VIIRS, and colocation with Meteosat detections in the same half hour.

Products (MOSDAC, imager L2P point product, one KML of fire pixels every ~30 min)
  3RIMG_L2P_FIR   INSAT-3DR, archive covers 2021 onward
  3SIMG_L2P_FIR   INSAT-3DS, from 2024
The catalogue search is open; downloads need a free MOSDAC single sign-on account: MOSDAC_USER / MOSDAC_PASSWORD in .env.
MOSDAC rate-limits per minute and per day; the script backs off on a minute limit and stops cleanly on a daily one
(rerun later, cached files are skipped).

Outputs
  data/raw/insat/<dataset>/<file>.kml            raw KML, small (kept)
  data/raw/insat/<dataset>_<year><tag>.csv       fire pixels inside the region
  data/raw/insat/pilot<tag>.json                 all figures quoted in docs/geostationary_pilot.md
Usage: insat_pilot.py [--years 2024 2025] [--products 3S 3R] [--window 12-05 12-25] [--no-download]
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import requests
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geostationary_pilot as gp          # region, FIRMS baseline, env, window

ROOT = gp.ROOT
RAW = ROOT / "data" / "raw" / "insat"
API = "https://mosdac.gov.in"
PRODUCTS = {"3R": "3RIMG_L2P_FIR", "3S": "3SIMG_L2P_FIR"}
IST = gp.IST
CELL = gp.CELL
STAMP = re.compile(r"_(\d{2}[A-Z]{3}\d{4})_(\d{4})_")


class Mosdac:
    def __init__(self, user, pw):
        self.user, self.pw, self.s = user, pw, requests.Session()
        self.login()

    def login(self):
        r = self.s.post(f"{API}/download_api/gettoken", json={"username": self.user, "password": self.pw}, timeout=60)
        if r.status_code != 200:
            raise SystemExit(f"MOSDAC login failed: HTTP {r.status_code}")
        j = r.json()
        self.access, self.refresh = j["access_token"], j["refresh_token"]

    def renew(self):
        r = self.s.post(f"{API}/download_api/refresh-token", json={"refresh_token": self.refresh}, timeout=60)
        if r.status_code == 200:
            j = r.json()
            self.access, self.refresh = j["access_token"], j.get("refresh_token", self.refresh)
        else:
            self.login()

    def get(self, rid, path):
        for attempt in range(8):
            try:
                r = self.s.get(f"{API}/download_api/download", params={"id": rid},
                               headers={"Authorization": f"Bearer {self.access}"}, timeout=120)
            except requests.RequestException:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code == 200:
                path.write_bytes(r.content)
                return "ok"
            try:
                j = r.json()
            except ValueError:
                j = {}
            if r.status_code == 401 and j.get("code") == "INVALID_TOKEN":
                self.renew()
                continue
            if r.status_code == 429:
                if j.get("type") == "daily_limit":
                    return "daily_limit"
                time.sleep(25)
                continue
            if r.status_code in (400, 404):
                return "missing"
            time.sleep(10 * (attempt + 1))
        return "failed"

    def logout(self):
        try:
            self.s.post(f"{API}/download_api/logout", json={"username": self.user}, timeout=10)
        except requests.RequestException:
            pass


def search(dataset, d0, d1):
    """All catalogue entries between two dates (open endpoint, 100 per page)."""
    out, start = [], 1
    while True:
        r = requests.get(f"{API}/apios/datasets.json", params={"datasetId": dataset, "startTime": d0.isoformat(),
                                                                "endTime": d1.isoformat(), "startIndex": start}, timeout=120)
        j = r.json()
        ents = j.get("entries") or []
        out += ents
        if not ents or len(out) >= j.get("totalResults", 0):
            return out
        start += len(ents)


def slot_of(identifier):
    m = STAMP.search(identifier)
    return dt.datetime.strptime(m.group(1) + m.group(2), "%d%b%Y%H%M")


def parse_kml(path):
    """Fire pixels as (lat, lon, attrs). Placemark points; any ExtendedData or name/description kept as text."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    out = []
    for pm in root.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        coords = next((e.text for e in pm.iter() if e.tag.endswith("coordinates") and e.text), None)
        if not coords:
            continue
        for tok in coords.split():
            p = tok.split(",")
            if len(p) >= 2:
                attrs = {e.get("name"): (e.findtext("{*}value") or "").strip() for e in pm.iter() if e.tag.endswith("Data")}
                out.append((float(p[1]), float(p[0]), attrs))
    return out


def fetch(dataset, year, cli, tag):
    d0, d1 = gp.window(year)
    ents = search(dataset, d0 - dt.timedelta(1), d1)
    ddir = RAW / dataset
    ddir.mkdir(parents=True, exist_ok=True)
    status = Counter()
    for e in ents:
        path = ddir / e["identifier"]
        if path.exists() and path.stat().st_size:
            status["cached"] += 1
            continue
        if cli is None:
            status["skipped"] += 1
            continue
        st = cli.get(e["id"], path)
        status[st] += 1
        if st == "daily_limit":
            print(f"  {dataset}: MOSDAC daily download limit reached; rerun tomorrow to continue")
            break
    return ents, dict(status)


def collect(dataset, year, reg, ents, tag):
    d0, d1 = gp.window(year)
    rows, attrs_seen = [], Counter()
    for e in ents:
        path = RAW / dataset / e["identifier"]
        if not path.exists():
            continue
        slot = slot_of(e["identifier"])
        for lat, lon, attrs in parse_kml(path):
            attrs_seen.update(attrs.keys())
            if not reg.contains(Point(lon, lat)):
                continue
            t = slot + IST
            if d0 <= t.date() <= d1:
                rows.append((t, lat, lon, attrs))
    with open(RAW / f"{dataset}_{year}{tag}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["slot_ist", "lat", "lon", "attrs"])
        for t, lat, lon, a in rows:
            w.writerow([t.isoformat(timespec="minutes"), lat, lon, json.dumps(a) if a else ""])
    return rows, dict(attrs_seen)


def stats(rows, slots_have, year):
    d0, d1 = gp.window(year)
    hist = gp.hour_hist([r[0] for r in rows])
    first = {}
    for t, lat, lon, _ in sorted(rows, key=lambda r: r[0]):
        first.setdefault((t.date(), round(lat / CELL), round(lon / CELL)), t)
    fh = Counter(t.hour for t in first.values())
    fhist = [fh.get(h, 0) for h in range(24)]
    slot_hours = Counter((s + IST).hour for s in slots_have)
    return {"detections": len(rows), "fire_cells": len(first), "by_ist_hour": hist,
            "first_seen_hour": fhist, "late15_share_cells": gp.late_share(fhist, gp.LATE15),
            "late15_share_detections": gp.late_share(hist, gp.LATE15),
            "files": len(slots_have), "files_by_ist_hour": [slot_hours.get(h, 0) for h in range(24)],
            "daily_cells": [sum(1 for k in first if k[0] == d0 + dt.timedelta(i)) for i in range((d1 - d0).days + 1)]}


def near_share(a, b, km=6, minutes=30):
    """Share of points in a with a point of b within km and minutes."""
    idx = defaultdict(list)
    for t, la, lo in b:
        idx[(round(la * 10), round(lo * 10))].append((t, la, lo))
    hit = 0
    for t, la, lo in a:
        ok = False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for tb, lb, ob in idx.get((round(la * 10) + di, round(lo * 10) + dj), []):
                    if abs((tb - t).total_seconds()) <= minutes * 60 and ((lb - la) * 111) ** 2 + ((ob - lo) * 96) ** 2 <= km * km:
                        ok = True
                        break
                if ok:
                    break
            if ok:
                break
        hit += ok
    return {"n": len(a), "matched": hit, "share": round(hit / len(a), 3) if a else None}


def meteosat(year, tag):
    p = gp.RAW / f"iodc_{year}{tag}.csv"
    if not p.exists():
        return None
    return [(dt.datetime.fromisoformat(r["slot_utc"]) + IST, float(r["lat"]), float(r["lon"])) for r in csv.DictReader(open(p))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    ap.add_argument("--products", nargs="+", default=["3S"], choices=list(PRODUCTS))
    ap.add_argument("--window", nargs=2, metavar="MM-DD")
    ap.add_argument("--no-download", action="store_true")
    a = ap.parse_args()
    tag = ""
    if a.window:
        gp.WIN = tuple(tuple(int(x) for x in w.split("-")) for w in a.window)
        tag = "_" + "_".join(a.window)
    e = gp.env()
    cli = None
    if not a.no_download:
        if e.get("MOSDAC_USER") and e.get("MOSDAC_PASSWORD"):
            cli = Mosdac(e["MOSDAC_USER"], e["MOSDAC_PASSWORD"])
        else:
            print("no MOSDAC_USER / MOSDAC_PASSWORD in .env: using cached KML only")
    _, reg = gp.region()
    RAW.mkdir(parents=True, exist_ok=True)
    res = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "window": a.window or "10-15 11-15",
           "years": {}}
    try:
        for y in a.years:
            f, cells = gp.firms(y, reg)
            vday = Counter(k[0] for k in {**cells["VIIRS S-NPP"], **cells["VIIRS NOAA-20"]})   # VIIRS 4 km cells per day
            d0, d1 = gp.window(y)
            yr = {"viirs_detections": f["VIIRS S-NPP"]["detections"] + f["VIIRS NOAA-20"]["detections"]}
            ms = meteosat(y, tag)
            for p in a.products:
                ds = PRODUCTS[p]
                ents, st = fetch(ds, y, cli, tag)
                if not ents:
                    yr[ds] = {"files": 0}
                    continue
                rows, attrs = collect(ds, y, reg, ents, tag)
                have = [slot_of(x["identifier"]) for x in ents if (RAW / ds / x["identifier"]).exists()]
                s = stats(rows, have, y)
                s["download"], s["kml_fields"], s["catalogue_files"] = st, attrs, len(ents)
                days = [d0 + dt.timedelta(i) for i in range((d1 - d0).days + 1)]
                vd = [vday.get(d, 0) for d in days]
                s["daily_r_viirs"] = round(float(np.corrcoef(s["daily_cells"], vd)[0, 1]), 2) if sum(s["daily_cells"]) else None
                if ms is not None:
                    pts = [(t, la, lo) for t, la, lo, _ in rows]
                    s["matched_by_meteosat"] = near_share(pts, ms)
                    s["meteosat_matched_by_insat"] = near_share([m for m in ms if d0 <= m[0].date() <= d1], pts)
                yr[ds] = s
                print(f"{y} {ds}: files {len(have)}/{len(ents)} {st}; detections {s['detections']}, cells {s['fire_cells']}, "
                      f"late15 {s['late15_share_cells']}; fields {list(attrs)[:8]}")
            res["years"][str(y)] = yr
    finally:
        if cli:
            cli.logout()
    out = RAW / f"pilot{tag}.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
