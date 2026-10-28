#!/usr/bin/env python3
"""Cyclone tracks for the Bay of Bengal / Arabian Sea, 2003-present.

Historical tracks come from IBTrACS v04r01 (NOAA NCEI, North Indian basin CSV).
The current season's systems are rebuilt from IMD RSMC New Delhi bulletins
(Tropical Weather Outlooks / Tropical Cyclone Advisories), then GDACS (which
relays JTWC) and the JTWC warning text, because IBTrACS provisional data lag.
Only positions printed by an agency are stored; nothing is interpolated or
invented for the current storms. Observed and forecast positions are kept
apart (kind = best | observed | forecast) with source URL and issue time.

Outputs
  data/db/extras/cyclones.json
  data/geo/cyclone_tracks.geojson
Cache
  data/raw/cyclones/

Usage: .venv/bin/python scripts/build_cyclones.py [--refresh] [--offline]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "cyclones"
OUT_JSON = ROOT / "data" / "db" / "extras" / "cyclones.json"
OUT_GEO = ROOT / "data" / "geo" / "cyclone_tracks.geojson"

IBTRACS_URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-"
               "climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.NI.list.v04r01.csv")
IMD_BASE = "https://rsmcnewdelhi.imd.gov.in/"
IMD_ARCHIVE = IMD_BASE + "archive-information.php?internal_menu=Mg%3D%3D&pageno={p}&menu_id=OQ%3D%3D&search_year="
GDACS_API = "https://www.gdacs.org/gdacsapi/api/"
JTWC_WARN = "https://www.metoc.navy.mil/jtwc/products/{atcf}web.txt"

CHENNAI = (13.08, 80.27)
FIRST_SEASON = 2003
UA = {"User-Agent": "Mozilla/5.0 (StubbleFireWatch cyclone builder)"}

# Current-season systems tracked from operational bulletins. IMD bulletins whose
# analysis time falls in the window and which print a centre position are
# assigned to the storm. Extend this list when a new system forms.
CURRENT = [
    {"name": "ARNAB", "sid": "2026299N16089", "atcf": "io0226", "basin": "Bay of Bengal",
     "season": 2026, "imd_from": "2026-10-24T00:00", "imd_to": "2026-11-10T00:00"},
]
CURRENT_MONTH = "2026-10"   # used for the GDACS sweep for any other NI system

GRADES = ["D", "DD", "CS", "SCS", "VSCS", "ESCS", "SuCS"]
GRADE_NAMES = {
    "depression": "D", "deep depression": "DD", "cyclonic storm": "CS",
    "severe cyclonic storm": "SCS", "very severe cyclonic storm": "VSCS",
    "extremely severe cyclonic storm": "ESCS", "super cyclonic storm": "SuCS",
    "well marked low pressure area": "WML", "well-marked low pressure area": "WML",
    "low pressure area": "L",
}
# IBTrACS IFLAG character order (one per agency)
IFLAG_AGENCIES = ["USA", "TOKYO", "CMA", "HKO", "NEWDELHI", "REUNION", "BOM", "NADI",
                  "WELLINGTON", "DS824", "TD9636", "TD9635", "NEUMANN", "MLC"]

# Coastal reference places used only to label landfall points ("near X").
PLACES = [
    ("Chennai", 13.08, 80.27), ("Mamallapuram", 12.62, 80.19), ("Puducherry", 11.93, 79.83),
    ("Cuddalore", 11.75, 79.77), ("Karaikal", 10.92, 79.84), ("Nagapattinam", 10.77, 79.84),
    ("Vedaranyam", 10.37, 79.85), ("Pamban", 9.28, 79.21), ("Thoothukudi", 8.76, 78.13),
    ("Kanyakumari", 8.08, 77.55), ("Sriharikota", 13.72, 80.23), ("Krishnapatnam", 14.25, 80.12),
    ("Kavali", 14.92, 80.05), ("Ongole", 15.50, 80.10), ("Bapatla", 15.90, 80.47),
    ("Machilipatnam", 16.17, 81.13), ("Narsapur", 16.43, 81.70), ("Yanam", 16.73, 82.21),
    ("Kakinada", 16.95, 82.24), ("Tuni", 17.35, 82.55), ("Visakhapatnam", 17.69, 83.22),
    ("Kalingapatnam", 18.34, 84.12), ("Gopalpur", 19.26, 84.91), ("Puri", 19.81, 85.83),
    ("Paradip", 20.32, 86.61), ("Chandbali", 20.78, 86.74), ("Balasore", 21.49, 86.93),
    ("Digha", 21.63, 87.51), ("Sagar Island", 21.65, 88.05), ("Khepupara", 21.98, 90.22),
    ("Chittagong", 22.33, 91.83), ("Cox's Bazar", 21.43, 92.00), ("Sittwe", 20.15, 92.90),
    ("Kyaukpyu", 19.43, 93.55), ("Gwa", 17.60, 94.58), ("Pathein", 16.78, 94.73),
    ("Yangon", 16.80, 96.15), ("Trincomalee", 8.59, 81.21), ("Mullaitivu", 9.27, 80.81),
    ("Jaffna", 9.66, 80.02), ("Batticaloa", 7.73, 81.69), ("Hambantota", 6.12, 81.12),
    ("Port Blair", 11.62, 92.73), ("Kochi", 9.93, 76.27), ("Kozhikode", 11.25, 75.78),
    ("Mangaluru", 12.87, 74.84), ("Karwar", 14.81, 74.13), ("Panaji", 15.49, 73.83),
    ("Ratnagiri", 16.99, 73.30), ("Alibag", 18.64, 72.87), ("Mumbai", 18.95, 72.82),
    ("Daman", 20.41, 72.83), ("Diu", 20.71, 70.98), ("Veraval", 20.90, 70.37),
    ("Porbandar", 21.64, 69.60), ("Dwarka", 22.24, 68.97), ("Jakhau", 23.22, 68.72),
    ("Karachi", 24.86, 67.00), ("Keti Bandar", 24.14, 67.45), ("Gwadar", 25.12, 62.32),
    ("Chabahar", 25.29, 60.64), ("Muscat", 23.59, 58.41), ("Sur", 22.57, 59.53),
    ("Duqm", 19.66, 57.70), ("Masirah", 20.68, 58.87), ("Salalah", 17.02, 54.09),
    ("Al Ghaydah", 16.20, 52.18), ("Mukalla", 14.54, 49.13), ("Aden", 12.80, 45.03),
    ("Socotra", 12.50, 53.90), ("Bosaso", 11.28, 49.18), ("Eyl", 7.98, 49.82),
    ("Djibouti", 11.59, 43.15), ("Male", 4.18, 73.51), ("Kavaratti", 10.57, 72.64),
    ("Minicoy", 8.28, 73.05),
]

# Known facts used to sanity-check the historical build.
VALIDATION = [
    {"name": "VARDAH", "season": 2016, "expect": "landfall near Chennai on 12 Dec 2016 (IMD: 0930-1130 UTC)",
     "landfall_date": "2016-12-12", "landfall_near": (13.08, 80.27), "max_km": 60},
    {"name": "NIVAR", "season": 2020, "expect": "landfall near Puducherry late 25 Nov 2020 UTC (IMD: 1730-2030 UTC)",
     "landfall_date": "2020-11-25", "landfall_near": (11.93, 79.83), "max_km": 60},
    {"name": "MICHAUNG", "season": 2023, "expect": "moved parallel to the Chennai coast (~4 Dec) then crossed near Bapatla on 5 Dec 2023",
     "landfall_date": "2023-12-05", "landfall_near": (15.90, 80.47), "max_km": 60,
     "chennai_max_km": 150, "chennai_date": "2023-12-04"},
    {"name": "MANDOUS", "season": 2022, "expect": "landfall near Mamallapuram 9-10 Dec 2022",
     "landfall_date": "2022-12-09", "landfall_near": (12.62, 80.19), "max_km": 60},
    {"name": "FENGAL", "season": 2024, "expect": "landfall close to Puducherry on 30 Nov 2024 (IMD: 1430-1630 UTC)",
     "landfall_date": "2024-11-30", "landfall_near": (11.93, 79.83), "max_km": 60},
    {"name": "FANI", "season": 2019, "expect": "ESCS, landfall near Puri 3 May 2019", "grade": "ESCS",
     "landfall_date": "2019-05-03", "landfall_near": (19.81, 85.83), "max_km": 60},
    {"name": "AMPHAN", "season": 2020, "expect": "SuCS, landfall near Digha/Sundarbans 20 May 2020", "grade": "SuCS",
     "landfall_date": "2020-05-20", "landfall_near": (21.65, 88.05), "max_km": 120},
]


# ----------------------------------------------------------------- helpers
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def hav_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_name(lat1, lon1, lat2, lon2):
    """Compass direction of point 2 as seen from point 1."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    b = (math.degrees(math.atan2(y, x)) + 360) % 360
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((b + 11.25) // 22.5) % 16]


def near_place(lat, lon):
    best = min(PLACES, key=lambda p: hav_km(lat, lon, p[1], p[2]))
    d = hav_km(lat, lon, best[1], best[2])
    if d < 8:
        return f"{best[0]}"
    return f"{round(d)} km {bearing_name(best[1], best[2], lat, lon)} of {best[0]}"


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M")


def grade_from_imd_kt(kt):
    if kt is None:
        return None
    for lim, g in [(120, "SuCS"), (90, "ESCS"), (64, "VSCS"), (48, "SCS"), (34, "CS"), (28, "DD"), (17, "D")]:
        if kt >= lim:
            return g
    return "L"


def grade_rank(g):
    return GRADES.index(g) if g in GRADES else -1


def fetch(url, path: Path, refresh=False, offline=False, max_age_h=None, timeout=120, tries=3):
    """Download url to path (cached). Returns path or None."""
    if path.exists() and path.stat().st_size > 0 and not refresh:
        if max_age_h is None or (time.time() - path.stat().st_mtime) < max_age_h * 3600:
            return path
    if offline:
        return path if path.exists() and path.stat().st_size > 0 else None
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200 and r.content:
                tmp = path.with_suffix(path.suffix + ".part")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                return path
            log(f"  {r.status_code} {url}")
            if r.status_code == 404:
                break
        except requests.RequestException as e:
            log(f"  fetch error ({i + 1}/{tries}) {url}: {e}")
        time.sleep(2 * (i + 1))
    return path if path.exists() and path.stat().st_size > 0 else None


def closest_approach(points, ref=CHENNAI):
    """Minimum distance from ref to the polyline through points (sorted by time).
    Segments are treated as straight in a local equirectangular projection."""
    if not points:
        return None
    lat0, lon0 = ref
    kx = 111.32 * math.cos(math.radians(lat0))

    def xy(p):
        return ((p["lon"] - lon0) * kx, (p["lat"] - lat0) * 110.57)

    best = None
    for i, p in enumerate(points):
        d = hav_km(lat0, lon0, p["lat"], p["lon"])
        if best is None or d < best[0]:
            best = (d, parse_iso(p["t"]), p["lat"], p["lon"])
        if i == 0:
            continue
        a, b = points[i - 1], p
        (ax, ay), (bx, by) = xy(a), xy(b)
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 == 0:
            continue
        f = max(0.0, min(1.0, -(ax * vx + ay * vy) / L2))
        if 0 < f < 1:
            lat = a["lat"] + f * (b["lat"] - a["lat"])
            lon = a["lon"] + f * (b["lon"] - a["lon"])
            d = hav_km(lat0, lon0, lat, lon)
            if d < best[0]:
                ta, tb = parse_iso(a["t"]), parse_iso(b["t"])
                best = (d, ta + (tb - ta) * f, lat, lon)
    d, t, lat, lon = best
    return {"km": round(d, 1), "t": iso(t), "lat": round(lat, 2), "lon": round(lon, 2),
            "bearing_from_chennai": bearing_name(lat0, lon0, lat, lon)}


def window_within(points, km, ref=CHENNAI):
    inside = [p["t"] for p in points if hav_km(ref[0], ref[1], p["lat"], p["lon"]) <= km]
    return [inside[0], inside[-1]] if inside else None


# ----------------------------------------------------------------- IBTrACS
def num(v):
    try:
        v = str(v).strip()
        return float(v) if v else None
    except ValueError:
        return None


def build_ibtracs(path: Path):
    log("parsing IBTrACS", path)
    df = pd.read_csv(path, skiprows=[1], low_memory=False, keep_default_na=False, dtype=str)
    df["SEASON"] = df["SEASON"].astype(int)
    df = df[df["SEASON"] >= FIRST_SEASON]
    storms = []
    for sid, g in df.groupby("SID", sort=False):
        g = g.sort_values("ISO_TIME")
        subs = set(g["SUBBASIN"].str.strip())
        if not subs & {"BB", "AS"}:
            continue  # only remnants of other basins (e.g. W Pacific typhoons over Indochina)
        track_type = g["TRACK_TYPE"].iloc[0].strip()
        kind = "best" if track_type == "main" else "observed"
        pts = []
        for _, r in g.iterrows():
            lat, lon = num(r["LAT"]), num(r["LON"])
            if lat is None or lon is None:
                continue
            t = dt.datetime.strptime(r["ISO_TIME"], "%Y-%m-%d %H:%M:%S")
            iflag = r["IFLAG"]
            nd_flag = iflag[4] if len(iflag) > 4 else "_"
            us_flag = iflag[0] if iflag else "_"
            grade = r["NEWDELHI_GRADE"].strip() or None
            if grade == "SUCS":
                grade = "SuCS"
            wind, pres = num(r["NEWDELHI_WIND"]), num(r["NEWDELHI_PRES"])
            pts.append({
                "t": iso(t), "lat": round(lat, 2), "lon": round(lon, 2),
                "wind_kt": wind, "pres_hpa": pres, "grade": grade,
                "usa_wind_kt": num(r["USA_WIND"]), "usa_pres_hpa": num(r["USA_PRES"]),
                "kind": kind, "agency": "IBTrACS (IMD position where available)",
                "source": "IBTrACS v04r01 NI" + ("" if kind == "best" else f" {track_type}"),
                "interp": nd_flag not in ("O",) and not (nd_flag == "_" and us_flag == "O"),
                "dist2land_km": num(r["DIST2LAND"]),
                "subbasin": r["SUBBASIN"].strip(),
            })
        if not pts:
            continue
        name = g["NAME"].iloc[0].strip()
        grades = [p["grade"] for p in pts if p["grade"]]
        gmax = max(grades, key=grade_rank) if grades else None
        if gmax and grade_rank(gmax) < 0:
            gmax = None
        names = {"BB": "Bay of Bengal", "AS": "Arabian Sea"}
        first = next(p["subbasin"] for p in pts if p["subbasin"] in names)
        basin = names[first]  # basin of the first Bay of Bengal / Arabian Sea position
        crossed = sorted({names[p["subbasin"]] for p in pts if p["subbasin"] in names} - {basin})
        winds = [p["wind_kt"] for p in pts if p["wind_kt"] is not None]
        uwinds = [p["usa_wind_kt"] for p in pts if p["usa_wind_kt"] is not None]
        pres = [p["pres_hpa"] for p in pts if p["pres_hpa"] is not None]
        landfalls = find_landfalls(pts)
        storms.append({
            "id": sid, "name": name if name != "NOT_NAMED" else "UNNAMED",
            "season": int(g["SEASON"].iloc[0]), "basin": basin, "also_in": crossed,
            "atcf_id": g["USA_ATCF_ID"].iloc[0].strip() or None,
            "ibtracs_track_type": track_type,
            "grade_max": gmax,
            "imd_wind_max_kt": max(winds) if winds else None,
            "usa_wind_max_kt": max(uwinds) if uwinds else None,
            "imd_pres_min_hpa": min(pres) if pres else None,
            "start": pts[0]["t"], "end": pts[-1]["t"],
            "landfall": primary_landfall(landfalls),
            "landfalls": landfalls,
            "track": pts,
        })
    log(f"  {len(storms)} storms {FIRST_SEASON}+ with Bay of Bengal / Arabian Sea positions")
    return storms


def find_landfalls(pts):
    """Sea->land transitions in IBTrACS DIST2LAND (0 = centre over land). The point
    is placed on the segment at the last sea point's distance to land."""
    out = []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        da, db = a.get("dist2land_km"), b.get("dist2land_km")
        if da is None or db is None or not (da > 0 and db == 0):
            continue
        nxt = [p.get("dist2land_km") for p in pts[i + 1:i + 4]]
        # sustained = centre stays over land for the next >= 6 h (or until the track ends)
        sustained = all(d == 0 for d in nxt)
        seg = hav_km(a["lat"], a["lon"], b["lat"], b["lon"])
        f = 1.0 if seg <= 0 else max(0.0, min(1.0, da / seg))
        lat = a["lat"] + f * (b["lat"] - a["lat"])
        lon = a["lon"] + f * (b["lon"] - a["lon"])
        ta, tb = parse_iso(a["t"]), parse_iso(b["t"])
        w = a["wind_kt"] if a["wind_kt"] is not None else b["wind_kt"]
        g = a["grade"] or b["grade"] or grade_from_imd_kt(w)
        out.append({"t": iso(ta + (tb - ta) * f), "lat": round(lat, 2), "lon": round(lon, 2),
                    "near": near_place(lat, lon), "wind_kt": w, "grade": g,
                    "usa_wind_kt": a["usa_wind_kt"], "sustained": sustained,
                    "method": "IBTrACS DIST2LAND sea-to-land transition, interpolated",
                    "source": "IBTrACS v04r01 NI"})
    return out


def primary_landfall(landfalls):
    if not landfalls:
        return None
    # strongest sustained crossing (by IMD wind, then USA wind); ties -> earliest.
    # Brief 'landfalls' of coast-grazing tracks only count if nothing sustained exists.
    landfalls = [l for l in landfalls if l["sustained"]] or landfalls

    def key(l):
        return (l["wind_kt"] if l["wind_kt"] is not None else -1,
                l["usa_wind_kt"] if l["usa_wind_kt"] is not None else -1)
    best = max(landfalls, key=key)
    return {k: v for k, v in best.items() if k != "usa_wind_kt"}


# ----------------------------------------------------------------- IMD bulletins
def imd_bulletin_list(refresh, offline):
    links = set()
    for p in (1, 2):
        f = fetch(IMD_ARCHIVE.format(p=p), RAW / "imd" / f"archive2_p{p}.html",
                  refresh=refresh, offline=offline, max_age_h=1)
        if f:
            links |= set(re.findall(r"uploads/archive/2/[^\"' <>]+\.pdf", f.read_text(errors="ignore")))
    # include anything already cached (older pages roll off the listing)
    for f in (RAW / "imd" / "pdf").glob("*.pdf"):
        links.add("uploads/archive/2/" + f.name)
    return sorted(links)


def pdf_text(pdf: Path) -> str:
    txt = pdf.with_suffix(".txt")
    if not txt.exists() or txt.stat().st_mtime < pdf.stat().st_mtime:
        subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
    return txt.read_text(errors="ignore")


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}


def parse_imd(pdf: Path, url: str):
    raw = pdf_text(pdf)
    s = re.sub(r"\s+", " ", raw)
    m = re.search(r"BASED ON (\d{4}) UTC OF (\d{1,2})\.(\d{1,2})\.(\d{4})", s, re.I)
    if not m:
        m2 = re.search(r"based_on_(\d{4})_UTC_of_(\d{2})\.(\d{2})\.(\d{4})", pdf.name)
        if not m2:
            return None
        m = m2
    hhmm, d, mo, y = m.groups()
    base = dt.datetime(int(y), int(mo), int(d), int(hhmm[:2]), int(hhmm[2:]))
    issued = None
    m = re.search(r"ISSUED AT (\d{1,2}):?(\d{2}) UTC OF (\d{1,2})\.(\d{1,2})\.(\d{4})", s, re.I)
    if m:
        hh, mi, d, mo, y = map(int, m.groups())
        issued = dt.datetime(y, mo, d, hh, mi)
    kind_doc = "Tropical Cyclone Advisory" if "advisory" in pdf.name.lower() else "Tropical Weather Outlook"
    no = re.search(r"ADVISORY NO\.?\s*(\d+)", s, re.I)
    sub = re.search(r"Sub\s*:\s*(.{0,200}?)(?: The | \[)", s)
    rec = {"file": pdf.name, "url": url, "doc": kind_doc + (f" No. {no.group(1)}" if no else ""),
           "base": base, "issued": issued, "subject": sub.group(1).strip() if sub else None,
           "obs": None, "fc": [], "landfall": None}

    lat_lon = r"latitude\s*([\d.]+)\s*(?:°|º|0)?\s*N[\s,]*(?:and\s*)?(?:longitude\s*)?([\d.]+)\s*(?:°|º|0)?\s*E"
    m = re.search(r"lay\s+c\w+\s.{0,260}?near\s+" + lat_lon, s, re.I)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        # name of the system: the category word right before 'lay centred'
        pre = s[max(0, m.start() - 400):m.start()].lower()
        hits = [(pre.rfind(n) + len(n), len(n), g) for n, g in GRADE_NAMES.items() if n in pre]
        cat = max(hits)[2] if hits else None
        mp = re.search(r"estimated central pressure (?:is|was)\s*(?:about\s*)?(\d{3,4})\s*hpa\s*and\s*(?:the\s*)?"
                       r"maximum sustained (?:surface )?wind speed\s*(?:\(MSW\)\s*)?(?:is|was)\s*(?:about\s*)?(\d+)\s*kt", s, re.I)
        pres = float(mp.group(1)) if mp else None
        wind = float(mp.group(2)) if mp else None
        g = grade_from_imd_kt(wind) if wind is not None else cat
        rec["obs"] = {"t": iso(base), "lat": lat, "lon": lon, "wind_kt": wind, "pres_hpa": pres, "grade": g}

    for line in raw.splitlines():
        m = re.match(r"\s*(\d{2})\.(\d{2})\.(\d{2})\s*/\s*(\d{4})\d?\s+(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s+"
                     r"(\d+)\s*-\s*(\d+)\s+gusting to\s+(\d+)\s+(.+?)\s*$", line)
        if not m:
            continue
        d, mo, yy, hhmm, lat, lon, w1, w2, gust, cat = m.groups()
        t = dt.datetime(2000 + int(yy), int(mo), int(d), int(hhmm[:2]), int(hhmm[2:]))
        lo, hi = int(w1), int(w2)
        rec["fc"].append({"t": iso(t), "lat": float(lat), "lon": float(lon),
                          "wind_kt": round((lo + hi) / 2 / 1.852), "wind_kmph": f"{lo}-{hi} gusting {gust}",
                          "grade": GRADE_NAMES.get(cat.strip().lower(), cat.strip()),
                          "lead_h": round((t - base).total_seconds() / 3600)})
    if rec["obs"] is None:
        for f in rec["fc"]:
            if f["lead_h"] == 0:
                rec["obs"] = {"t": f["t"], "lat": f["lat"], "lon": f["lon"], "wind_kt": None,
                              "pres_hpa": None, "grade": f["grade"], "from_table": True}

    m = re.search(r"crossed\s.{0,260}?near\s+" + lat_lon + r".{0,260}?during\s+(\d{4})\s*UTC\s+of\s+(\d{1,2})\w*\s+(\w+)"
                  r"(?:,?\s*\d{4})?\s*[–\-]+\s*(\d{4})\s*UTC\s+of\s+(?:today,?\s*)?(?:the\s+)?(\d{1,2})\w*\s+(\w+)"
                  r"(.{0,200}?)\.", s, re.I)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        t1 = dt.datetime(base.year, MONTHS[m.group(5).lower()], int(m.group(4)), int(m.group(3)[:2]), int(m.group(3)[2:]))
        t2 = dt.datetime(base.year, MONTHS[m.group(8).lower()], int(m.group(7)), int(m.group(6)[:2]), int(m.group(6)[2:]))
        tail = m.group(9).lower()
        g = None
        for name, gg in sorted(GRADE_NAMES.items(), key=lambda kv: -len(kv[0])):
            if name in tail:
                g = gg
                break
        mw = re.search(r"(\d+)\s*-\s*(\d+)\s*kmph", m.group(0))
        excerpt = re.sub(r"\s+", " ", m.group(0))[:400]
        rec["landfall"] = {"t": iso(t1 + (t2 - t1) / 2), "t_start": iso(t1), "t_end": iso(t2),
                           "lat": lat, "lon": lon, "near": near_place(lat, lon), "grade": g,
                           "wind_kt": round((int(mw.group(1)) + int(mw.group(2))) / 2 / 1.852) if mw else None,
                           "wind_kmph": f"{mw.group(1)}-{mw.group(2)}" if mw else None,
                           "imd_text": excerpt, "method": "IMD advisory statement",
                           "source": url, "issued": iso(issued) if issued else None}
    return rec


def build_imd(refresh, offline):
    (RAW / "imd" / "pdf").mkdir(parents=True, exist_ok=True)
    recs = []
    for rel in imd_bulletin_list(refresh, offline):
        url = IMD_BASE + rel
        pdf = fetch(url, RAW / "imd" / "pdf" / Path(rel).name, offline=offline)
        if not pdf:
            continue
        try:
            r = parse_imd(pdf, url)
        except Exception as e:  # keep going; one bad PDF must not sink the build
            log(f"  IMD parse failed {pdf.name}: {e}")
            continue
        if r:
            recs.append(r)
    recs.sort(key=lambda r: (r["base"], r["issued"] or r["base"]))
    log(f"  IMD bulletins parsed: {len(recs)} ({sum(1 for r in recs if r['obs'])} with a centre position)")
    return recs


# ----------------------------------------------------------------- GDACS / JTWC
def gdacs_events(refresh, offline):
    today = dt.date.today()
    url = (GDACS_API + f"events/geteventlist/SEARCH?eventlist=TC&fromDate={CURRENT_MONTH}-01"
           f"&toDate={today.isoformat()}&alertlevel=")
    f = fetch(url, RAW / "gdacs_tc_search.json", refresh=refresh, offline=offline, max_age_h=1)
    if not f:
        return [], url
    feats = json.loads(f.read_text()).get("features", [])
    out = []
    for ft in feats:
        lon, lat = ft["geometry"]["coordinates"][:2]
        p = ft["properties"]
        if 40 <= lon <= 100 and -5 <= lat <= 30:
            out.append(p)
    return out, url


def gdacs_track(ev, refresh, offline):
    eid, epi = ev["eventid"], ev["episodeid"]
    url = f"https://www.gdacs.org/datareport/resources/TC/{eid}/geojson_{eid}_{epi}.geojson"
    f = fetch(url, RAW / f"gdacs_{eid}_{epi}.geojson", refresh=refresh, offline=offline)
    if not f:
        return [], url, None
    d = json.loads(f.read_text())
    todate, pub = None, None
    pts = []
    for ft in d["features"]:
        p = ft["properties"]
        if p.get("Class") == "Point_Centroid":
            todate = dt.datetime.strptime(p["todate"], "%d %b %Y %H:%M:%S")
            try:
                pub = dt.datetime.strptime(p["jrc_pubdate"], "%d %b %Y %H:%M:%S")
            except (KeyError, ValueError):
                pub = None
    for ft in d["features"]:
        p = ft["properties"]
        if not re.fullmatch(r"Point_\d+", p.get("Class", "")):
            continue
        t = dt.datetime.strptime(p["trackdate"], "%d/%m/%Y %H:%M:%S")
        kmh = num(p.get("windspeed"))
        pres = num(p.get("pressure"))
        kind = "observed" if (todate is None or t <= todate) else "forecast"
        pts.append({"t": iso(t), "lat": float(p["latitude"]), "lon": float(p["longitude"]),
                    "wind_kt": round(kmh / 1.852) if kmh else None,
                    "pres_hpa": pres if pres else None,
                    "grade": None, "status": p.get("stormstatus"),
                    "kind": kind, "agency": "JTWC via GDACS (1-min wind)",
                    "source": url, "issued": iso(pub) if pub else None})
    return pts, url, pub


def jtwc_warning(atcf, refresh, offline):
    url = JTWC_WARN.format(atcf=atcf)
    f = fetch(url, RAW / f"jtwc_{atcf}web.txt", refresh=refresh, offline=offline, max_age_h=1)
    if not f:
        return [], url
    s = f.read_text(errors="ignore")
    hm = re.search(r"WTIO\d\d PGTW (\d{2})(\d{2})(\d{2})", s)
    ym = re.search(r"\b\d{2}([A-Z]{3})(\d{2})\.", s)
    if not hm or not ym:
        return [], url
    mon = dt.datetime.strptime(ym.group(1), "%b").month
    year = 2000 + int(ym.group(2))
    issued = dt.datetime(year, mon, int(hm.group(1)), int(hm.group(2)), int(hm.group(3)))

    def zt(z):
        d, hh = int(z[:2]), int(z[2:4])
        t = dt.datetime(year, mon, d, hh, int(z[4:6]))
        if t < issued - dt.timedelta(days=15):
            t = t.replace(month=mon % 12 + 1, year=year + (mon == 12))
        return t
    pts = []
    pm = re.search(r"MINIMUM CENTRAL PRESSURE AT (\d{6})Z IS (\d+) MB", s)
    m = re.search(r"WARNING POSITION:\s*(\d{6})Z --- NEAR ([\d.]+)([NS]) ([\d.]+)([EW]).*?MAX SUSTAINED WINDS - (\d+) KT", s, re.S)
    if m:
        t = zt(m.group(1))
        pts.append({"t": iso(t), "lat": float(m.group(2)) * (1 if m.group(3) == "N" else -1),
                    "lon": float(m.group(4)) * (1 if m.group(5) == "E" else -1), "wind_kt": float(m.group(6)),
                    "pres_hpa": float(pm.group(2)) if pm and pm.group(1) == m.group(1) else None,
                    "grade": None, "kind": "observed", "agency": "JTWC (1-min wind)", "source": url, "issued": iso(issued)})
    for m in re.finditer(r"(\d+) HRS, VALID AT:\s*(\d{6})Z --- ([\d.]+)([NS]) ([\d.]+)([EW]).*?MAX SUSTAINED WINDS - (\d+) KT", s, re.S):
        pts.append({"t": iso(zt(m.group(2))), "lat": float(m.group(3)) * (1 if m.group(4) == "N" else -1),
                    "lon": float(m.group(5)) * (1 if m.group(6) == "E" else -1), "wind_kt": float(m.group(7)),
                    "pres_hpa": None, "grade": None, "kind": "forecast", "lead_h": int(m.group(1)),
                    "agency": "JTWC (1-min wind)", "source": url, "issued": iso(issued)})
    final = "FINAL WARNING" in s
    return pts, url, issued, final


# ----------------------------------------------------------------- current storms
def build_current(cfg, imd_recs, ib_storm, gdacs_evs, refresh, offline, sources):
    t0, t1 = parse_iso(cfg["imd_from"]), parse_iso(cfg["imd_to"])
    mine = [r for r in imd_recs if t0 <= r["base"] < t1 and r["obs"]]
    track, fc_archive = [], []
    seen = {}
    for r in mine:
        o = dict(r["obs"])
        o.pop("from_table", None)
        pt = {**o, "kind": "observed", "agency": "IMD RSMC New Delhi (3-min wind)",
              "source": r["url"], "issued": iso(r["issued"]) if r["issued"] else None, "bulletin": r["doc"]}
        # one observed fix per analysis time: keep the latest-issued bulletin
        seen[pt["t"]] = pt
        for f in r["fc"]:
            if f["lead_h"] > 0:
                fc_archive.append({**f, "kind": "forecast", "agency": "IMD RSMC New Delhi",
                                   "source": r["url"], "issued": iso(r["issued"]) if r["issued"] else None,
                                   "base": iso(r["base"]), "bulletin": r["doc"]})
    imd_obs = sorted(seen.values(), key=lambda p: p["t"])
    track += imd_obs
    latest = max(mine, key=lambda r: (r["base"], r["issued"] or r["base"])) if mine else None
    if latest:
        for f in latest["fc"]:
            if f["lead_h"] > 0:
                track.append({**f, "kind": "forecast", "agency": "IMD RSMC New Delhi",
                              "source": latest["url"], "issued": iso(latest["issued"]) if latest["issued"] else None,
                              "bulletin": latest["doc"]})
    for r in mine:
        sources.append({"id": f"imd:{r['file']}", "agency": "IMD RSMC New Delhi", "doc": r["doc"],
                        "url": r["url"], "issued": iso(r["issued"]) if r["issued"] else None,
                        "analysis_time": iso(r["base"])})

    # IBTrACS provisional fixes (USA tcvitals etc.)
    if ib_storm:
        for p in ib_storm["track"]:
            track.append({"t": p["t"], "lat": p["lat"], "lon": p["lon"], "wind_kt": p["usa_wind_kt"],
                          "pres_hpa": p["usa_pres_hpa"], "grade": None, "kind": "observed",
                          "agency": "IBTrACS provisional (USA/JTWC, 1-min wind)",
                          "source": IBTRACS_URL, "issued": None, "interp": p["interp"]})

    # GDACS (JTWC) latest episode
    gd = [e for e in gdacs_evs if cfg["name"].lower() in e.get("name", "").lower()]
    for ev in gd:
        pts, url, pub = gdacs_track(ev, refresh, offline)
        track += pts
        sources.append({"id": f"gdacs:{ev['eventid']}:{ev['episodeid']}", "agency": "GDACS (JRC) relaying JTWC",
                        "url": url, "report": f"https://www.gdacs.org/report.aspx?eventid={ev['eventid']}&episodeid={ev['episodeid']}&eventtype=TC",
                        "issued": iso(pub) if pub else None, "points": len(pts)})

    # JTWC warning text
    jt = jtwc_warning(cfg["atcf"], refresh, offline)
    if jt and jt[0]:
        pts, url, issued, final = jt
        track += pts
        sources.append({"id": f"jtwc:{cfg['atcf']}", "agency": "JTWC", "url": url, "issued": iso(issued),
                        "note": "final warning" if final else "latest warning only (file is overwritten each warning)"})

    order = {"observed": 0, "best": 0, "forecast": 1}
    track.sort(key=lambda p: (order[p["kind"]], p["t"], p["agency"]))

    obs_line = imd_obs or [p for p in track if p["kind"] == "observed"]
    ca = closest_approach(obs_line)
    winds = [p["wind_kt"] for p in imd_obs if p["wind_kt"] is not None]
    pres = [p["pres_hpa"] for p in imd_obs if p["pres_hpa"] is not None]
    grades = [p["grade"] for p in imd_obs if p.get("grade") in GRADES]
    lf = None
    for r in mine:
        if r["landfall"]:
            lf = r["landfall"]  # latest statement wins
    jt_obs = [p["wind_kt"] for p in track if p["kind"] == "observed" and "JTWC" in p["agency"] and p["wind_kt"]]
    storm = {
        "id": cfg["sid"], "name": cfg["name"], "season": cfg["season"], "basin": cfg["basin"],
        "atcf_id": cfg["atcf"].upper()[:4] + "20" + cfg["atcf"][-2:], "status": "operational (not best track)",
        "grade_max": max(grades, key=grade_rank) if grades else None,
        "imd_wind_max_kt": max(winds) if winds else None,
        "usa_wind_max_kt": max(jt_obs) if jt_obs else None,
        "imd_pres_min_hpa": min(pres) if pres else None,
        "start": imd_obs[0]["t"] if imd_obs else None, "end": imd_obs[-1]["t"] if imd_obs else None,
        "landfall": lf, "landfalls": [lf] if lf else [],
        "closest_to_chennai_km": ca["km"] if ca else None,
        "closest_time": ca["t"] if ca else None,
        "closest": ca,
        "chennai_window_500km": window_within(obs_line, 500),
        "track": track,
        "forecast_archive": fc_archive,
        "counts": {"imd_observed": len(imd_obs), "imd_forecast_latest": sum(1 for p in track if p["kind"] == "forecast" and p["agency"].startswith("IMD")),
                   "imd_forecast_archive": len(fc_archive),
                   "jtwc_gdacs_observed": sum(1 for p in track if p["kind"] == "observed" and "JTWC" in p["agency"]),
                   "ibtracs_provisional": sum(1 for p in track if p["agency"].startswith("IBTrACS"))},
    }
    return storm


# ----------------------------------------------------------------- outputs
def finalize_hist(s):
    pts = [p for p in s["track"]]
    ca = closest_approach(pts)
    s["closest_to_chennai_km"] = ca["km"]
    s["closest_time"] = ca["t"]
    s["closest"] = ca
    s["chennai_window_500km"] = window_within(pts, 500)
    for p in s["track"]:
        p.pop("subbasin", None)
    return s


def validate(storms):
    out = []
    for v in VALIDATION:
        cand = [s for s in storms if s["name"] == v["name"] and s["season"] == v["season"]]
        if not cand:
            out.append({**v, "ok": False, "found": None})
            continue
        s = cand[0]
        checks, ok = [], True
        lf = s["landfall"]
        lat0, lon0 = v["landfall_near"]
        if lf:
            d = hav_km(lat0, lon0, lf["lat"], lf["lon"])
            good = d <= v["max_km"] and lf["t"][:10] in (v["landfall_date"],
                                                         (dt.date.fromisoformat(v["landfall_date"]) + dt.timedelta(days=1)).isoformat())
            checks.append(f"landfall {lf['t']} at {lf['lat']},{lf['lon']} ({lf['near']}), {d:.0f} km from expected")
            ok &= good
        else:
            checks.append("no landfall found")
            ok = False
        if "grade" in v:
            checks.append(f"grade_max {s['grade_max']}")
            ok &= s["grade_max"] == v["grade"]
        if "chennai_max_km" in v:
            checks.append(f"closest to Chennai {s['closest_to_chennai_km']} km at {s['closest_time']}")
            ok &= s["closest_to_chennai_km"] <= v["chennai_max_km"] and s["closest_time"][:10] == v["chennai_date"]
        out.append({"storm": f"{v['name']} {v['season']}", "id": s["id"], "expect": v["expect"],
                    "result": "; ".join(checks), "ok": bool(ok)})
    return out


def geojson(storms):
    feats = []

    def line(coords, props):
        if len(coords) >= 2:
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props})
        elif len(coords) == 1:
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": coords[0]}, "properties": props})

    for s in storms:
        base = {"id": s["id"], "name": s["name"], "season": s["season"], "basin": s["basin"],
                "grade_max": s["grade_max"], "imd_wind_max_kt": s.get("imd_wind_max_kt"),
                "closest_to_chennai_km": s["closest_to_chennai_km"], "closest_time": s["closest_time"],
                "start": s.get("start"), "end": s.get("end")}
        live = s.get("status", "").startswith("operational")
        if live:
            groups = {}
            for p in s["track"]:
                ag = "IMD" if p["agency"].startswith("IMD") else "JTWC" if "JTWC" in p["agency"] and "IBTrACS" not in p["agency"] else "IBTrACS"
                key = (p["kind"], ag)
                if p["kind"] == "forecast" and ag == "JTWC" and "GDACS" in p["agency"]:
                    continue  # same JTWC forecast as the warning text
                groups.setdefault(key, []).append(p)
            for (kind, ag), pts in groups.items():
                if ag == "IBTrACS":
                    continue
                pts = sorted({p["t"]: p for p in pts}.values(), key=lambda p: p["t"])
                if kind == "forecast":  # draw from the last observed fix of that agency
                    last = [p for p in groups.get(("observed", ag), []) if p["t"] <= pts[0]["t"]]
                    if last:
                        pts = [max(last, key=lambda p: p["t"])] + pts
                line([[p["lon"], p["lat"]] for p in pts],
                     {**base, "feature": "track", "kind": kind, "agency": ag,
                      "issued": max((p.get("issued") or "") for p in pts) or None})
            for p in s["track"]:
                if p["agency"].startswith("IMD"):
                    feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                                  "properties": {"id": s["id"], "name": s["name"], "feature": "fix", "kind": p["kind"],
                                                 "agency": "IMD", "t": p["t"], "wind_kt": p["wind_kt"],
                                                 "pres_hpa": p.get("pres_hpa"), "grade": p.get("grade"),
                                                 "issued": p.get("issued"), "source": p["source"]}})
        else:
            line([[p["lon"], p["lat"]] for p in s["track"]], {**base, "feature": "track", "kind": "best" if s["ibtracs_track_type"] == "main" else "observed", "agency": "IBTrACS"})
        if s["landfall"]:
            lf = s["landfall"]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lf["lon"], lf["lat"]]},
                          "properties": {"id": s["id"], "name": s["name"], "season": s["season"], "feature": "landfall",
                                         "t": lf["t"], "near": lf["near"], "grade": lf.get("grade"), "wind_kt": lf.get("wind_kt")}})
        if s.get("closest"):
            c = s["closest"]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                          "properties": {"id": s["id"], "name": s["name"], "season": s["season"], "feature": "closest_to_chennai",
                                         "t": c["t"], "km": c["km"], "bearing_from_chennai": c["bearing_from_chennai"]}})
    return {"type": "FeatureCollection", "features": feats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download IBTrACS and listing pages")
    ap.add_argument("--offline", action="store_true", help="use cache only")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    ib = fetch(IBTRACS_URL, RAW / "ibtracs.NI.list.v04r01.csv", refresh=a.refresh, offline=a.offline,
               max_age_h=24 * 7, timeout=600)
    if not ib:
        sys.exit("IBTrACS unavailable")
    hist = build_ibtracs(ib)
    sources = [{"id": "ibtracs", "agency": "NOAA NCEI IBTrACS v04r01 (North Indian basin)", "url": IBTRACS_URL,
                "fetched": iso(dt.datetime.fromtimestamp(ib.stat().st_mtime, dt.timezone.utc)),
                "note": "best track (TRACK_TYPE main) with IMD (NEWDELHI, 3-min) and USA (1-min) wind/pressure; "
                        "3-hourly points include IBTrACS-interpolated positions (interp=true)"}]

    imd = build_imd(a.refresh, a.offline)
    gd_evs, gd_url = gdacs_events(a.refresh, a.offline)
    sources.append({"id": "gdacs:search", "agency": "GDACS", "url": gd_url,
                    "note": "TC events in the North Indian Ocean this month: " + ", ".join(e["name"] for e in gd_evs)})

    by_sid = {s["id"]: s for s in hist}
    storms = [finalize_hist(s) for s in hist if s["id"] not in {c["sid"] for c in CURRENT}]
    current = []
    for cfg in CURRENT:
        st = build_current(cfg, imd, by_sid.get(cfg["sid"]), gd_evs, a.refresh, a.offline, sources)
        current.append(st)
    # IMD-named systems this month that are not configured: flag them so a human adds them
    notes = []
    known = {c["name"].lower() for c in CURRENT}
    for r in imd:
        if r["base"].strftime("%Y-%m") == CURRENT_MONTH and r["obs"] and r["subject"]:
            nm = re.search(r"[“\"]\s*(\w+)\s*[”\"]", r["subject"])
            if nm and nm.group(1).lower() not in known:
                notes.append(f"IMD bulletin {r['file']} names an unconfigured system {nm.group(1)}")
    for e in gd_evs:
        nm = e["name"].split()[-1].split("-")[0].lower()
        if nm not in known and e["fromdate"][:7] == CURRENT_MONTH:
            notes.append(f"GDACS lists {e['name']} ({e['fromdate']}..{e['todate']}) not in CURRENT")
    storms += current
    storms.sort(key=lambda s: (s["start"] or "", s["id"]))

    val = validate(storms)
    for v in val:
        log(("OK  " if v["ok"] else "FAIL"), v.get("storm"), "|", v.get("result"))

    seasons = sorted({s["season"] for s in storms})
    doc = {
        "generated": iso(now),
        "reference_point": {"name": "Chennai", "lat": CHENNAI[0], "lon": CHENNAI[1]},
        "coverage": {"seasons": [seasons[0], seasons[-1]], "storms": len(storms),
                     "bay_of_bengal": sum(1 for s in storms if s["basin"] == "Bay of Bengal"),
                     "arabian_sea": sum(1 for s in storms if s["basin"] == "Arabian Sea"),
                     "crossed_basins": sum(1 for s in storms if s.get("also_in")),
                     "within_300km_of_chennai": sum(1 for s in storms if (s["closest_to_chennai_km"] or 1e9) <= 300)},
        "fields": {
            "wind_kt": "IMD 3-min sustained wind (kt) for best/IMD points; JTWC/GDACS/IBTrACS-provisional points carry 1-min winds (see agency)",
            "usa_wind_kt": "US agency (JTWC) 1-min sustained wind, IBTrACS",
            "grade": "IMD grade D<DD<CS<SCS<VSCS<ESCS<SuCS (WML/L = low pressure area in IMD forecasts)",
            "kind": "best = IBTrACS best track; observed = operational analysis/fix; forecast = forecast position",
            "interp": "true when IBTrACS interpolated the position (no agency fix at that time)",
            "landfall": "IBTrACS: sea-to-land transition of DIST2LAND (strongest crossing); current storms: IMD advisory statement",
            "closest_to_chennai_km": "minimum distance from Chennai to the track polyline (observed/best positions only)",
        },
        "notes": notes + [
            "JTWC warning 02B NR010 mentions an 'Invest 90B' east of Arnab that moved inland on 28 Oct 2026; "
            "no positions for it were published in the sources used, so it is not stored.",
            "IMD did not issue bulletins for any other depression in the Bay of Bengal or Arabian Sea during Oct 2026 "
            "(Tropical Weather Outlooks 1-23 Oct 2026 report no system).",
        ],
        "validation": val,
        "sources": sources,
        "storms": storms,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
    OUT_GEO.parent.mkdir(parents=True, exist_ok=True)
    OUT_GEO.write_text(json.dumps(geojson(storms), separators=(",", ":"), ensure_ascii=False))
    log(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size // 1024} KB), {OUT_GEO} ({OUT_GEO.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
