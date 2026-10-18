#!/usr/bin/env python3
"""Build supporting datasets for the /db analysis page.

Outputs (data/db/extras/):
  rice_area.json  - kharif (and total-year) rice area/production by state and district,
                    districts mapped to data/geo/districts.geojson ids.
  aq_met.json     - daily air quality (Open-Meteo / CAMS) and meteorology (Open-Meteo / ERA5)
                    for receptor cities, Sep 15 - Jan 31 of each season.

Inputs / cache (data/raw/extras/):
  apy_district_best.parquet, apy_state_official.parquet, crop_lookup.csv, SOURCES.md, ref/*.csv
      downloaded from https://agri.reclaimchennai.city/datasets/crops_india/ if missing.
  openmeteo/*.json   raw API responses (re-used if present).

Usage:  .venv/bin/python scripts/build_db_extras.py [--rice-only|--aq-only] [--refresh]
"""
import argparse
import csv
import datetime as dt
import json
import math
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "extras"
OUT = ROOT / "data" / "db" / "extras"
GEO = ROOT / "data" / "geo" / "districts.geojson"

# public download catalogue of the "Agri at Source" project (district crop statistics, DES / UPAg)
AGRI_BASE = "https://agri.reclaimchennai.city/datasets/crops_india"
REMOTE_FILES = ["apy_district_best.parquet", "apy_state_official.parquet", "crop_lookup.csv", "SOURCES.md",
                "ref/district_crosswalk.csv", "ref/lgd_districts.csv", "ref/district_aliases.csv",
                "ref/lgd_states.csv", "ref/unmatched_districts.csv"]

STATES = ["Punjab", "Haryana", "Uttar Pradesh", "Delhi", "Rajasthan", "Madhya Pradesh",
          "Himachal Pradesh", "Uttarakhand", "Bihar"]

# APY (current LGD name) -> geoBoundaries LGD-2021 name, same state.
# kind "rename": same territory, new/alternate name.
# kind "parent": district carved out after the 2021 boundary set; mapped to the parent polygon
#                (the parent polygon then also covers the child's area -> sum both for that polygon).
ALIASES = {
    ("Bihar", "Purbi Champaran"): ("Purba Champaran", "rename"),
    ("Haryana", "Gurugram"): ("Gurgaon", "rename"),
    ("Haryana", "Nuh"): ("Mewat", "rename"),
    ("Haryana", "Charkhi Dadri"): ("Bhiwani", "parent"),          # carved from Bhiwani 2016
    ("Madhya Pradesh", "Agar-Malwa"): ("Agar", "rename"),
    ("Madhya Pradesh", "Narmadapuram"): ("Hoshangabad", "rename"),  # renamed 2022
    ("Madhya Pradesh", "MAUGANJ"): ("Rewa", "parent"),            # carved from Rewa 2023
    ("Madhya Pradesh", "Maihar"): ("Satna", "parent"),            # carved from Satna 2023
    ("Madhya Pradesh", "Pandhurna"): ("Chhindwara", "parent"),    # carved from Chhindwara 2023
    ("Punjab", "Ferozepur"): ("Firozpur", "rename"),
    ("Punjab", "S.A.S Nagar"): ("Sahibzada Ajit Singh Nagar", "rename"),
    ("Punjab", "Sri Muktsar Sahib"): ("Muktsar", "rename"),
    ("Punjab", "Malerkotla"): ("Sangrur", "parent"),              # carved from Sangrur 2021
    ("Rajasthan", "Chittorgarh"): ("Chittaurgarh", "rename"),
    ("Rajasthan", "Dholpur"): ("Dhaulpur", "rename"),
    ("Rajasthan", "Deeg"): ("Bharatpur", "parent"),               # carved from Bharatpur 2023
    ("Rajasthan", "Khairthal-Tijara"): ("Alwar", "parent"),       # carved from Alwar 2023
    ("Rajasthan", "Salumbar"): ("Udaipur", "parent"),             # carved from Udaipur 2023
    ("Uttar Pradesh", "Amroha"): ("Jyotiba Phule Nagar", "rename"),
    ("Uttar Pradesh", "Ayodhya"): ("Faizabad", "rename"),
    ("Uttar Pradesh", "Bhadohi"): ("Sant Ravidas Nagar (Bhadohi)", "rename"),
    ("Uttar Pradesh", "Hathras"): ("Mahamaya Nagar", "rename"),
    ("Uttar Pradesh", "Kasganj"): ("Kanshiram Nagar", "rename"),
    ("Uttar Pradesh", "Prayagraj"): ("Allahabad", "rename"),
    ("Uttar Pradesh", "Shamli"): ("Samli", "rename"),
    ("Uttarakhand", "Haridwar"): ("Hardwar", "rename"),
    ("Uttarakhand", "Pauri Garhwal"): ("Garhwal", "rename"),
}

CITIES = {
    "Delhi": (28.61, 77.21), "Ludhiana": (30.90, 75.85), "Amritsar": (31.63, 74.87),
    "Chandigarh": (30.73, 76.78), "Lucknow": (26.85, 80.95), "Patiala": (30.34, 76.39),
    "Hisar": (29.15, 75.72),
}
TODAY = dt.date(2026, 10, 17)
LAST_DAY = TODAY - dt.timedelta(days=1)    # last complete IST day; later hours are forecasts
MET_FIRST_SEASON = 2003
AQ_PROBE_START = dt.date(2022, 7, 1)       # CAMS archive in Open-Meteo starts early Aug 2022 (probed)
SEASON_START = (9, 15)                     # Sep 15 of season year y
SEASON_END = (1, 31)                       # Jan 31 of y+1 (Sep 15-Nov 30 core + Oct-Jan winter)
QA_GLITCH = ("2022-12-13T12:00", "2022-12-16T06:00")  # IST window searched for the CAMS near-zero glitch
MIN_HOURS = 18                             # valid hours required for a daily mean


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def r(x, nd):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    v = round(float(x), nd)
    return int(v) if nd == 0 else v


# ----------------------------------------------------------------------------- rice
def fetch_remote(refresh=False):
    (RAW / "ref").mkdir(parents=True, exist_ok=True)
    for f in REMOTE_FILES:
        dst = RAW / f
        if dst.exists() and not refresh:
            continue
        print("download", f)
        req = urllib.request.Request(f"{AGRI_BASE}/{f}", headers={"User-Agent": "stubble-fire-watch/1.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dst, "wb") as fh:
            fh.write(r.read())


def build_rice():
    geo = {}
    for f in json.load(open(GEO))["features"]:
        p = f["properties"]
        if p["country"] == "India":
            geo[(p["state"], norm(p["name"]))] = (p["id"], p["name"])

    # LGD code -> every name it has been reported under (crosswalk) + LGD's own name
    names_by_code = defaultdict(set)
    with open(RAW / "ref" / "district_crosswalk.csv") as fh:
        for row in csv.DictReader(fh):
            if row["lgd_district_code"]:
                c = int(float(row["lgd_district_code"]))
                names_by_code[c].update([row["district_name"], row["district_name_std"]])
    with open(RAW / "ref" / "lgd_districts.csv") as fh:
        for row in csv.DictReader(fh):
            names_by_code[int(row["lgd_district_code"])].add(row["district_name"])

    con = duckdb.connect()
    p = str(RAW / "apy_district_best.parquet")
    st_list = ",".join(f"'{s}'" for s in STATES)
    crop_check = con.sql(f"select distinct crop, crop_raw from '{p}' where crop='rice'").fetchall()
    assert crop_check == [("rice", "Rice")], crop_check

    # Districts: 1998-99..2024-25 from UPAg (source upag_des, the series in _best for these years);
    # 1997-98 only exists in DES APY (UPAg 1997-98 empty). Kharif = kharif (+autumn+winter for
    # DES seasons, which UPAg folds into kharif). Total = the source's own 'total' row, else the
    # sum of all seasonal rows for that district-year (DES 1997 often lacks a total row).
    rows = con.sql(f"""
        select state_name_std st, district_name_std d, lgd_district_code code, year_start y, source, season,
               sum(area_ha) a, sum(production_t) pr, count(*) n
        from '{p}'
        where crop='rice' and state_name_std in ({st_list})
          and ((year_start=1997 and source='des_apy') or (year_start>=1998 and source='upag_des'))
        group by all""").fetchall()
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    seasons_seen = defaultdict(set)
    meta = {}
    for st, d, code, y, src, season, a, pr, n in rows:
        key = (st, int(code))
        if y >= meta.get(key, (-1, None))[0]:
            meta[key] = (y, d)
        rec = agg[key][y]
        seasons_seen[(key, y)].add(season)
        # Bihar: UPAg district data 1998-99..2022-23 label winter (aghani) rice, the main kharif-sown crop
        # harvested Nov-Dec, as "rabi"; district kharif+rabi reproduces the official state kharif figure
        # (checked), so it is counted as kharif here. Elsewhere rabi rice stays out of kharif.
        bihar_rabi = st == "Bihar" and season == "rabi" and src == "upag_des" and y <= 2022
        if season in ("kharif", "autumn", "winter") or bihar_rabi:
            rec["ka"] += a or 0; rec["kp"] += pr or 0; rec["kn"] += 1
            rec["kp_nan"] += 1 if pr is None or (isinstance(pr, float) and math.isnan(pr)) else 0
        if season == "total":
            rec["ta"] = a; rec["tp"] = pr
        else:
            rec["sa"] += a or 0; rec["sp"] += pr or 0
    districts, unmatched, method_count = [], [], defaultdict(int)
    for (st, code), years in sorted(agg.items(), key=lambda kv: (kv[0][0], meta[kv[0]][1])):
        dname = meta[(st, code)][1]  # name in the latest year reported
        match_id, match_name, method = None, None, None
        k = (st, norm(dname))
        if k in geo:
            (match_id, match_name), method = geo[k], "name"
        if match_id is None:
            for alt in sorted(names_by_code.get(code, [])):
                if (st, norm(alt)) in geo:
                    (match_id, match_name), method = geo[(st, norm(alt))], "lgd_crosswalk"
                    break
        if match_id is None and (st, dname) in ALIASES:
            target, kind = ALIASES[(st, dname)]
            if (st, norm(target)) in geo:
                (match_id, match_name), method = geo[(st, norm(target))], "alias_" + kind
        method_count[method or "unmatched"] += 1
        if match_id is None:
            unmatched.append({"state": st, "district": dname, "lgd_code": code})
        series = {}
        for y in sorted(years):
            rec = years[y]
            ent = {}
            if rec.get("kn"):
                ent["area"] = r(rec["ka"], 0)
                ent["prod"] = None if rec.get("kp_nan") else r(rec["kp"], 0)
            if "ta" in rec:
                ent["area_total"], ent["prod_total"] = r(rec["ta"], 0), r(rec["tp"], 0)
            elif rec.get("sa"):
                ent["area_total"], ent["prod_total"] = r(rec["sa"], 0), r(rec["sp"], 0)
            if ent:
                series[str(y)] = ent
        districts.append({"state": st, "district": dname, "lgd_code": code, "match_id": match_id,
                          "match_name": match_name, "match_method": method or "unmatched",
                          "years": [min(years), max(years)], "series": series})

    # States: official UPAg state-wise APY (latest estimate) - includes Delhi and 2025-26 (3rd AE).
    s = str(RAW / "apy_state_official.parquet")
    srows = con.sql(f"""
        select state_name_std, year_start, season, estimate_type, area_ha, production_t
        from '{s}' where crop='rice' and record_type in ('State - Actual','State - Season Total')
          and state_name_std in ({st_list})""").fetchall()
    states = defaultdict(lambda: defaultdict(dict))
    estimate_note = {}
    for st, y, season, est, a, pr in srows:
        ent = states[st][str(y)]
        if season == "kharif":
            ent["area"], ent["prod"] = r(a, 0), r(pr, 0)
        elif season == "total":
            ent["area_total"], ent["prod_total"] = r(a, 0), r(pr, 0)
        if est != "Final Estimate":
            estimate_note[str(y)] = est
    # district sums alongside (for QA / coverage); not used to fill official gaps
    for dd in districts:
        for y, ent in dd["series"].items():
            sv = states[dd["state"]][y]
            if ent.get("area") is not None:
                sv["dist_sum_area"] = sv.get("dist_sum_area", 0) + ent["area"]
            if ent.get("prod") is not None:
                sv["dist_sum_prod"] = sv.get("dist_sum_prod", 0) + ent["prod"]
    states = {st: dict(sorted(v.items())) for st, v in sorted(states.items())}

    out = {
        "source": ("District: UPAg 'Complete APY Dataset (District Level)', DA&FW/DES, Final Estimates "
                   "1998-99..2024-25 (retrieved 2026-10-08); 1997-98 from DES Crop Production Statistics via "
                   "India Data Portal mirror (ODC-By). State: UPAg 'Crop Profile: State-wise APY' (official), "
                   "1997-98..2025-26. Compiled in the agri.reclaimchennai.city crops_india layer "
                   "(apy_district_best.parquet, apy_state_official.parquet)."),
        "crop": "rice (crop_raw 'Rice'; milled rice, not paddy)",
        "year_convention": ("Keys are the agricultural-year start: '2023' = 2023-24 (July-June); kharif rice "
                            "of key Y is harvested Oct-Nov of Y, i.e. the residue burned in the Y fire season."),
        "unit_area": "ha", "unit_prod": "t",
        "fields": {"area/prod": "kharif season (for 1997-98 DES rows: kharif+autumn+winter, the seasons "
                                "UPAg folds into kharif)",
                   "area_total/prod_total": "all-season total as reported by the source (or, where the source "
                                            "has no total row, sum of its seasonal rows)",
                   "dist_sum_area/dist_sum_prod": "(states only) sum of district kharif values in this file"},
        "state_estimate_types": {"default": "Final Estimate", **estimate_note},
        "season_notes": ("Bihar 1998-2022: UPAg district rows label winter (aghani) rice as 'rabi'; these are "
                         "counted in kharif here because district kharif+rabi matches the official state kharif "
                         "figure. Other states: rabi/summer rice excluded from kharif, included in *_total."),
        "district_values": "as reported (not benchmarked to state totals); district boundaries as reported each year",
        "match_note": ("match_id = properties.id in data/geo/districts.geojson (geoBoundaries LGD-2021). "
                       "match_method: name | lgd_crosswalk (another name the same LGD code was reported under) | "
                       "alias_rename | alias_parent (district created after 2021, mapped to its parent polygon; "
                       "sum children with the parent for that polygon). Several APY districts can share a "
                       "match_id."),
        "match_summary": dict(method_count),
        "unmatched": unmatched,
        "states": states,
        "districts": districts,
    }
    if "Delhi" not in {d["state"] for d in districts}:
        out["notes"] = ["Delhi has no district-level rice rows in the APY district data; state-level only."]
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "rice_area.json", "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print("rice: districts", len(districts), dict(method_count), "unmatched", unmatched)
    return out


# ----------------------------------------------------------------------------- AQ / met
def get_json(url, params, cache, refresh=False):
    if cache.exists() and not refresh:
        return json.load(open(cache))
    q = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": "fire-dashboard/1.0"}),
                                        timeout=300) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = 30 * (attempt + 1)
                print(f"  HTTP {e.code}; retry in {wait}s")
                time.sleep(wait)
                continue
            raise
    else:
        raise RuntimeError("giving up on " + q)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "w") as fh:
        json.dump(data, fh)
    time.sleep(2)
    return data


def daily_from_hourly(times, vals, hour=None):
    """IST hourly -> {date: mean} (>= MIN_HOURS valid) or value at `hour`."""
    bucket = defaultdict(list)
    for t, v in zip(times, vals):
        d, h = t[:10], int(t[11:13])
        if hour is not None:
            if h == hour:
                bucket[d].append(v)
        else:
            bucket[d].append(v)
    out = {}
    for d, vs in bucket.items():
        good = [v for v in vs if v is not None]
        if hour is not None:
            out[d] = good[0] if good else None
        else:
            out[d] = sum(good) / len(good) if len(good) >= MIN_HOURS else None
    return out


def season_dates():
    dates = []
    for y in range(MET_FIRST_SEASON, TODAY.year + 1):
        d = dt.date(y, *SEASON_START)
        end = min(dt.date(y + 1, *SEASON_END), LAST_DAY)
        while d <= end:
            dates.append(d.isoformat())
            d += dt.timedelta(days=1)
    return dates


def build_aq_met(refresh=False):
    cache = RAW / "openmeteo"
    dates = season_dates()
    cities, aq_cov, met_cov, qa_removed = {}, {}, {}, {}
    for city, (lat, lon) in CITIES.items():
        slug = city.lower()
        print("city", city)
        # --- air quality: one request per city for the whole CAMS archive (hourly, IST)
        aq = get_json("https://air-quality-api.open-meteo.com/v1/air-quality", {
            "latitude": lat, "longitude": lon,
            "hourly": "pm2_5,pm10,aerosol_optical_depth,dust",
            "start_date": AQ_PROBE_START.isoformat(), "end_date": LAST_DAY.isoformat(),
            "timezone": "Asia/Kolkata"}, cache / f"aq_{slug}_{AQ_PROBE_START}_{LAST_DAY}.json", refresh)
        h = aq["hourly"]
        # QA: the Open-Meteo CAMS archive has near-zero values (pm2_5 ~0.5-0.8, aod ~0.04) at every city
        # 2022-12-13 17:00 .. 2022-12-16 04:00 IST - a model/archive glitch, not real air. Those hours are
        # set to null (not replaced); daily means then fall below MIN_HOURS and become null.
        bad = [i for i, t in enumerate(h["time"]) if QA_GLITCH[0] <= t <= QA_GLITCH[1]
               and h["pm2_5"][i] is not None and h["pm2_5"][i] < 3]
        for i in bad:
            for k in ("pm2_5", "pm10", "aerosol_optical_depth", "dust"):
                h[k][i] = None
        qa_removed[city] = len(bad)
        pm25 = daily_from_hourly(h["time"], h["pm2_5"])
        pm10 = daily_from_hourly(h["time"], h["pm10"])
        aod = daily_from_hourly(h["time"], h["aerosol_optical_depth"])
        dust = daily_from_hourly(h["time"], h["dust"])
        valid = sorted(d for d, v in pm25.items() if v is not None)
        aq_cov[city] = [valid[0], valid[-1]] if valid else None

        # --- meteorology: ERA5 archive, chunks of 6 seasons (Sep 15 y .. Jan 31 y+6)
        met_d = defaultdict(dict)
        blh_h_t, blh_h_v = [], []
        for y0 in range(MET_FIRST_SEASON, TODAY.year + 1, 6):
            s = dt.date(y0, *SEASON_START)
            e = min(dt.date(y0 + 6, *SEASON_END), LAST_DAY)
            js = get_json("https://archive-api.open-meteo.com/v1/archive", {
                "latitude": lat, "longitude": lon, "start_date": s.isoformat(), "end_date": e.isoformat(),
                "daily": "wind_speed_10m_mean,wind_direction_10m_dominant,temperature_2m_mean,precipitation_sum",
                "hourly": "boundary_layer_height", "wind_speed_unit": "ms",
                "timezone": "Asia/Kolkata"}, cache / f"met_{slug}_{s}_{e}.json", refresh)
            dd = js["daily"]
            for i, t in enumerate(dd["time"]):
                for k in ("wind_speed_10m_mean", "wind_direction_10m_dominant", "temperature_2m_mean",
                          "precipitation_sum"):
                    met_d[t][k] = dd[k][i]
            blh_h_t += js["hourly"]["time"]
            blh_h_v += js["hourly"]["boundary_layer_height"]
        blh = daily_from_hourly(blh_h_t, blh_h_v)
        blh14 = daily_from_hourly(blh_h_t, blh_h_v, hour=14)
        mv = sorted(t for t, v in met_d.items() if v.get("temperature_2m_mean") is not None)
        met_cov[city] = [mv[0], mv[-1]] if mv else None

        def col(src, nd, key=None):
            return [r((src.get(d) or {}).get(key) if key else src.get(d), nd) for d in dates]

        cities[city] = {"lat": lat, "lon": lon, "grid_lat_lon_aq": [aq.get("latitude"), aq.get("longitude")],
                        "grid_lat_lon_met": [js.get("latitude"), js.get("longitude")], "daily": {
            "date": dates,
            "pm25": col(pm25, 1), "pm10": col(pm10, 1), "aod": col(aod, 3), "dust": col(dust, 1),
            "wind": col(met_d, 1, "wind_speed_10m_mean"), "wdir": col(met_d, 0, "wind_direction_10m_dominant"),
            "blh": col(blh, 0), "blh14": col(blh14, 0),
            "t2m": col(met_d, 1, "temperature_2m_mean"), "precip": col(met_d, 1, "precipitation_sum")}}

    out = {
        "sources": {
            "pm25,pm10,aod,dust": {
                "provider": "Open-Meteo Air Quality API (https://air-quality-api.open-meteo.com/v1/air-quality), keyless",
                "model": "CAMS global atmospheric composition forecasts (~0.4 deg), as archived by Open-Meteo; "
                         "model output, NOT ground-station measurements",
                "coverage": aq_cov, "units": {"pm25": "ug/m3", "pm10": "ug/m3", "aod": "550 nm, unitless",
                                              "dust": "ug/m3"},
                "aggregation": f"daily mean of hourly values in IST (Asia/Kolkata); null if < {MIN_HOURS} valid hours",
                "note": "Open-Meteo returns nulls before early Aug 2022 (probed); no earlier CAMS reanalysis offered.",
                "qa": {"rule": f"hours in {QA_GLITCH[0]}..{QA_GLITCH[1]} IST with pm2_5 < 3 ug/m3 set to null "
                               "(archive glitch: near-zero PM/AOD at all cities, daily means for 2022-12-13..15 become null)",
                       "hours_nulled": qa_removed}},
            "wind,wdir,t2m,precip,blh,blh14": {
                "provider": "Open-Meteo Historical Weather API (https://archive-api.open-meteo.com/v1/archive), keyless",
                "model": "ERA5 / ERA5-Land reanalysis (Open-Meteo best-match archive)",
                "coverage": met_cov,
                "units": {"wind": "m/s (daily mean 10 m)", "wdir": "deg, dominant 10 m direction (from)",
                          "t2m": "deg C daily mean 2 m", "precip": "mm daily sum",
                          "blh": "m, daily mean of hourly boundary_layer_height",
                          "blh14": "m, boundary_layer_height at 14:00 IST"},
                "aggregation": "daily values in IST (Asia/Kolkata)",
                "gaps": "boundary_layer_height is null in the Open-Meteo archive for 2024-01-01..2024-01-31 "
                        "(all cities) - left null"},
            "cpcb_ground_pm25": {
                "status": "not included",
                "reason": ("No keyless CPCB archive reachable (checked 2026-10-17): the CCR portal "
                           "(airquality.cpcb.gov.in/ccr) data repository / report endpoints return HTTP 401 "
                           "without a client session; OpenAQ v3 requires an API key; data.gov.in requires an "
                           "API key.")},
        },
        "season_window": "Sep 15 of year Y to Jan 31 of Y+1 (core stubble season Sep 15-Nov 30 plus Dec-Jan), "
                         f"seasons {MET_FIRST_SEASON}-{TODAY.year}; last date {LAST_DAY} (last complete IST day)",
        "retrieved": TODAY.isoformat(),
        "cities": cities,
    }
    with open(OUT / "aq_met.json", "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print("aq coverage", aq_cov)
    print("met coverage", met_cov)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rice-only", action="store_true")
    ap.add_argument("--aq-only", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-download instead of using the cache")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not a.aq_only:
        fetch_remote(a.refresh)
        build_rice()
    if not a.rice_only:
        build_aq_met(a.refresh)
    for f in sorted(OUT.glob("*.json")):
        print(f.relative_to(ROOT), f"{f.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    sys.exit(main())
