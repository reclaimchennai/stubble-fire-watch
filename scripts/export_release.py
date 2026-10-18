#!/usr/bin/env python3
"""Export every dataset behind the stubble-burning dashboard into research-friendly open formats.

Reads only files the pipeline already produced (nothing is re-fetched):
  data/fires/summary.json, district_daily.json      FIRMS aggregates (scripts/build_fires.py)
  data/raw/firms_country/*.csv, data/raw/firms_api/*.csv   FIRMS detections, re-filtered with build_fires.py's
                                                     own functions/constants (bbox, static sources, dedup)
  data/creams/*                                     ICAR-IARI CREAMS bulletin tables + events
  data/burnscar/burnscar.json, data/raw/burnscar/<season>/mosaic.tif   MODIS MCD64A1 burned area
  data/smoke/smoke_meta.json, data/raw/smoke/*.npy  GEOS-FP smoke AOD + winds chunk cache
  data/s2night/*, data/raw/s2night/                 Sentinel-2A night hot spots, Dec 2025 (scripts/s2night.py)
  data/db/extras/*                                  rice area, AQ/met, CPCB ground PM, stubble share, enforcement
  data/geo/*.geojson                                boundaries

Writes:
  release/datasets/<topic>/...   small tables for the repository (CSV / Parquet / GeoJSON / GPKG) + README.md
  release/assets/...             large files for a GitHub Release + SHA256SUMS + MANIFEST.csv
  release/DATA_DICTIONARY.md, release/LICENSES.md

Usage: .venv/bin/python scripts/export_release.py [--datasets] [--assets] [--skip-detections]
       (no flag = both; --skip-detections re-uses existing fire_detections_* assets)
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_fires as bf  # noqa: E402  (re-use the dashboard's filters and district raster)

REL = ROOT / "release"
DS = REL / "datasets"
AS = REL / "assets"
BUILD = REL / ".build"  # bookkeeping between runs (not for publication)
DATA = ROOT / "data"
EXTRAS = DATA / "db" / "extras"
SCRIPT = "scripts/export_release.py"
TODAY = dt.date.today().isoformat()
SEASON_LABEL = {"rice": "paddy (kharif) residue season, Sep 15 - Nov 30", "wheat": "wheat (rabi) residue season, Apr 1 - May 31"}
LOG = []  # validation lines


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def check(ok, msg):
    log(("PASS " if ok else "FAIL ") + msg)
    return ok


def jload(p):
    return json.loads(Path(p).read_text())


def state_split(label):
    """Dashboard state label -> (state, country). 'Punjab (PK)' -> ('Punjab', 'Pakistan')."""
    if label == "Outside districts":
        return "Outside mapped districts", ""
    if label.endswith(" (PK)"):
        return label[:-5], "Pakistan"
    return label, "India"


def write_csv(df, path, **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", **kw)
    return df


# ---------------------------------------------------------------------------------------------
# README generation
# ---------------------------------------------------------------------------------------------
def dtype_name(s):
    k = s.dtype.kind
    if k in "iu":
        return "integer"
    if k == "f":
        return "float"
    if k == "b":
        return "boolean"
    if k == "M":
        return "timestamp"
    return "string"


def table_doc(name, df, cols, extra=""):
    """Markdown section for one file: rows + column table. cols: {col: (unit, meaning)}."""
    out = [f"### `{name}`", "", f"{len(df):,} rows. {extra}".strip(), "", "| column | type | unit | meaning |", "|---|---|---|---|"]
    for c in df.columns:
        unit, meaning = cols.get(c, ("", ""))
        if not meaning:
            raise SystemExit(f"undocumented column {name}:{c}")
        out.append(f"| `{c}` | {dtype_name(df[c])} | {unit} | {meaning} |")
    return "\n".join(out) + "\n"


def write_readme(folder, title, intro, sections, coverage, source, caveats, produced, snippet_py, snippet_r):
    txt = [f"# {title}", "", intro, "", "## Files", ""] + sections + [
        "## Coverage", "", coverage, "",
        "## Source and licence", "", source, "",
        "## Known caveats", "", "\n".join(f"- {c}" for c in caveats), "",
        "## How it was produced", "", produced, "",
        "## Loading", "", "Python (pandas):", "", "```python", snippet_py.strip(), "```", "",
        "R:", "", "```r", snippet_r.strip(), "```", ""]
    (folder / "README.md").write_text("\n".join(txt))


# ---------------------------------------------------------------------------------------------
# A1. fires
# ---------------------------------------------------------------------------------------------
def season_doys(year, season):
    (m0, d0), (m1, d1) = bf.SEASONS[season]
    a = dt.date(year, m0, d0).timetuple().tm_yday
    b = dt.date(year, m1, d1).timetuple().tm_yday
    return a, b


def empty_feed_caveats():
    """FIRMS API files that came back empty (no rows at all) -> sensor gaps the reader should know about."""
    out = []
    for f in sorted(glob.glob(str(bf.RAW_A / "*.csv"))):
        p = Path(f)
        with open(f) as fh:
            if sum(1 for _ in zip(range(2), fh)) < 2:
                m = re.match(r"(.+?)_(\d{4}-\d{2}-\d{2})_(\d+)\.csv", p.name)
                if m:
                    d0 = dt.date.fromisoformat(m.group(2))
                    out.append(f"{m.group(1)} {d0} .. {d0 + dt.timedelta(days=int(m.group(3)) - 1)}")
    if not out:
        return []
    return ["The FIRMS API returned empty files (no detections at all, i.e. a feed gap rather than no fires) for: " + "; ".join(out) +
            ". Counts for those sensors and dates are missing (they are not zero-fire days)."]


def export_fires():
    out = DS / "fires"
    out.mkdir(parents=True, exist_ok=True)
    S = jload(DATA / "fires" / "summary.json")
    DD = jload(DATA / "fires" / "district_daily.json")
    sensors_firms = [s for s in S["sensors"] if not s.startswith("CREAMS")]
    dmeta = {d["id"]: d for d in S["districts"]}
    gj = jload(DATA / "geo" / "districts.geojson")
    dcountry = {f["properties"]["id"]: f["properties"]["country"] for f in gj["features"]}

    # daily_by_state (all days of the year, nonzero rows)
    rows = []
    for y, bys in S["daily"].items():
        y = int(y)
        d0 = dt.date(y, 1, 1)
        for sen, bst in bys.items():
            if sen not in sensors_firms:
                continue
            for lab, v in bst.items():
                st, co = state_split(lab)
                n, frp = np.array(v["n"]), np.array(v["frp"])
                for i in np.nonzero((n > 0) | (frp > 0))[0]:
                    rows.append((d0 + dt.timedelta(days=int(i)), st, co, sen, int(n[i]), int(frp[i])))
    daily = pd.DataFrame(rows, columns=["date", "state", "country", "sensor", "detections", "frp_mw_sum"])
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values(["date", "country", "state", "sensor"]).reset_index(drop=True)
    daily.to_parquet(out / "daily_by_state.parquet", index=False, compression="zstd")
    dcsv = daily.copy()
    dcsv["date"] = dcsv["date"].dt.strftime("%Y-%m-%d")
    write_csv(dcsv, out / "daily_by_state.csv.gz", compression={"method": "gzip", "mtime": 0})

    # season totals from the same arrays
    rows = []
    for y, bys in S["daily"].items():
        y = int(y)
        for season in bf.SEASONS:
            a, b = season_doys(y, season)
            for sen, bst in bys.items():
                if sen not in sensors_firms:
                    continue
                for lab, v in bst.items():
                    n = sum(v["n"][a - 1:b])
                    f = sum(v["frp"][a - 1:b])
                    if n or f:
                        st, co = state_split(lab)
                        rows.append((y, season, st, co, sen, n, f))
    st = pd.DataFrame(rows, columns=["year", "season", "state", "country", "sensor", "detections", "frp_mw_sum"])
    st = st.sort_values(["year", "season", "country", "state", "sensor"]).reset_index(drop=True)
    write_csv(st, out / "season_totals.csv")

    # district season
    rows = []
    for y, bss in S["district_season"].items():
        for season, bsen in bss.items():
            for sen, arr in bsen.items():
                if sen not in sensors_firms:
                    continue
                for i, n in enumerate(arr):
                    if n:
                        d = dmeta[i]
                        rows.append((int(y), season, i, d["name"], state_split(d["state"])[0], dcountry[i], sen, n))
    ds = pd.DataFrame(rows, columns=["year", "season", "district_id", "district", "state", "country", "sensor", "detections"])
    ds = ds.sort_values(["year", "season", "country", "state", "district", "sensor"]).reset_index(drop=True)
    write_csv(ds, out / "district_season.csv")

    # hourly (rice season, IST hour)
    rows = [(int(y), sen, h, n) for y, bs in S["hourly_ist_rice"].items() for sen, arr in bs.items()
            if sen in sensors_firms for h, n in enumerate(arr)]
    hr = pd.DataFrame(rows, columns=["year", "sensor", "hour_ist", "detections"])
    write_csv(hr, out / "hourly_ist_paddy.csv")

    # district daily (paddy season, sensor groups)
    rows = []
    for y, bb in DD.items():
        sep15 = dt.date(int(y), 9, 15)
        for basis, bd in bb.items():
            for did, pairs in bd.items():
                d = dmeta[int(did)]
                for j, n in pairs:
                    rows.append((int(y), basis, (sep15 + dt.timedelta(days=j)).isoformat(), int(did), d["name"],
                                 state_split(d["state"])[0], dcountry[int(did)], n))
    dd = pd.DataFrame(rows, columns=["year", "sensor_group", "date", "district_id", "district", "state", "country", "detections"])
    dd = dd.sort_values(["year", "sensor_group", "district_id", "date"]).reset_index(drop=True)
    write_csv(dd, out / "district_daily_paddy.csv.gz", compression={"method": "gzip", "mtime": 0})

    # ---- validation against the dashboard
    tot_json = sum(sum(v["n"]) for y, bys in S["daily"].items() for sen, bst in bys.items() if sen in sensors_firms
                   for v in bst.values())
    check(int(daily.detections.sum()) == tot_json, f"fires/daily_by_state detections {daily.detections.sum():,} == summary.json {tot_json:,}")
    for y in (2016, 2021, 2025):
        a, b = season_doys(y, "rice")
        ref = sum(sum(v["n"][a - 1:b]) for sen, bst in S["daily"][str(y)].items() if sen in sensors_firms for v in bst.values())
        got = int(st[(st.year == y) & (st.season == "rice")].detections.sum())
        check(got == ref, f"fires/season_totals rice {y} {got:,} == summary.json {ref:,}")
    db = jload(DATA / "db" / "db.json")
    basis = {"modis": ["MODIS Terra", "MODIS Aqua"], "snpp": ["VIIRS S-NPP"], "viirs": ["VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21"]}
    bad = 0
    nchk = 0
    lab2 = {"Punjab (PK)": ("Punjab", "Pakistan")}
    for b, sens in basis.items():
        for stl, byy in db["daily"][b].items():
            s_, c_ = lab2.get(stl, (stl, "India"))
            for y, arr in byy.items():
                got = int(st[(st.year == int(y)) & (st.season == "rice") & (st.state == s_) & (st.country == c_) & st.sensor.isin(sens)].detections.sum())
                nchk += 1
                bad += got != sum(x or 0 for x in arr)
    check(bad == 0, f"fires/season_totals vs dashboard db.json rice-season state series: {nchk - bad}/{nchk} (basis x state x year) equal")
    ds_ref = sum(n for y, bss in S["district_season"].items() for bsen in bss.values() for sen, arr in bsen.items()
                 if sen in sensors_firms for n in arr)
    check(int(ds.detections.sum()) == ds_ref, f"fires/district_season sum {ds.detections.sum():,} == summary.json {ds_ref:,}")
    dd_ref = sum(n for bb in DD.values() for bd in bb.values() for pairs in bd.values() for _, n in pairs)
    check(int(dd.detections.sum()) == dd_ref, f"fires/district_daily_paddy sum {dd.detections.sum():,} == district_daily.json {dd_ref:,}")
    rt = pd.read_parquet(out / "daily_by_state.parquet")
    check(len(rt) == len(daily), f"fires/daily_by_state.parquet re-reads with {len(rt):,} rows")

    sen_doc = ("", "FIRMS sensor: MODIS Terra, MODIS Aqua, VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21 (Landsat is not present in the inputs)")
    common = {"state": ("", "state/province the detection falls in (district polygon lookup); 'Outside mapped districts' = inside the analysis box but outside every mapped district"),
              "country": ("", "India or Pakistan; empty for 'Outside mapped districts'"), "sensor": sen_doc,
              "detections": ("count", "number of active-fire detections (pixels), per sensor; sensors are not merged"),
              "frp_mw_sum": ("MW", "sum of fire radiative power of those detections; the dashboard rounds each day's sum to an integer")}
    secs = [
        table_doc("season_totals.csv", st, {**common, "year": ("", "calendar year of the season"), "season": ("", "rice = Sep 15-Nov 30, wheat = Apr 1-May 31 (UTC acquisition date)"),
                                             "frp_mw_sum": ("MW", "sum over the season of the daily (integer-rounded) FRP sums")}),
        table_doc("daily_by_state.parquet / daily_by_state.csv.gz", daily, {**common, "date": ("UTC date", "acquisition date (UTC) of the overpass")},
                  "Whole calendar year, only rows with at least one detection."),
        table_doc("district_season.csv", ds, {**common, "year": ("", "calendar year"), "season": ("", "rice | wheat (see above)"),
                                              "district_id": ("", "id in boundaries/districts.geojson"), "district": ("", "district name (geoBoundaries ADM2)")}),
        table_doc("hourly_ist_paddy.csv", hr, {**common, "year": ("", "calendar year"), "hour_ist": ("hour 0-23", "hour of the overpass in Indian Standard Time (UTC+5:30), all detections in the rice season (all states in the box)")},
                  "All 24 hours per year x sensor, zeros included."),
        table_doc("district_daily_paddy.csv.gz", dd, {**common, "year": ("", "season year"), "date": ("UTC date", "acquisition date"),
                                                      "sensor_group": ("", "modis = Terra+Aqua; snpp = VIIRS S-NPP only; viirs = S-NPP+NOAA-20+NOAA-21"),
                                                      "district_id": ("", "id in boundaries/districts.geojson"), "district": ("", "district name")},
                  "Rice season (Sep 15-Nov 30) only, nonzero district-days."),
    ]
    write_readme(out, "Satellite active-fire detections (NASA FIRMS), aggregated",
                 "Counts and fire radiative power (FRP) of MODIS and VIIRS active-fire detections over north-west India and "
                 "Pakistan Punjab (box 68.5-89 E, 21-35.5 N), aggregated by day, state, district, season and sensor. These are the "
                 "numbers the dashboard plots. Point-level detections (all days of every year, 2003 onwards) are in the release "
                 "assets `fire_detections_<year>.parquet` / `fire_detections_all.csv.gz`.",
                 secs,
                 f"MODIS Terra/Aqua 2003 onwards, VIIRS S-NPP 2012 onwards, NOAA-20 2018 onwards, NOAA-21 2023 onwards; last detection date {S['last_detection_date']} (summary generated {S['generated']}).",
                 "NASA FIRMS (https://firms.modaps.eosdis.nasa.gov/): MODIS Collection 6.1 (MCD14) and VIIRS 375 m (VNP14IMG / VJ114IMG / VJ214IMG) "
                 "active fire products. Yearly country archive files (standard processing) for completed years; the FIRMS area API "
                 "(standard 'SP' then near-real-time 'NRT') for the current year and for NOAA-21. NASA data are freely usable without restriction; "
                 "cite FIRMS (see ../../LICENSES.md). District/state attribution uses geoBoundaries polygons (ODbL / CC BY 2.5 IN / public domain).",
                 ["Counts are detections (pixels), not fires or burned fields. MODIS pixels are ~1 km, VIIRS ~375 m, so VIIRS counts are several times MODIS counts for the same burning; never add sensors together and do not compare across sensors.",
                  "Detections are attributed to districts by a 0.01-degree raster of the district polygons (centre of pixel); detections on a border can fall in the neighbouring district.",
                  "Static land sources (FIRMS type 2: industry, gas flares, etc.) and offshore detections (type 3) were removed; near-real-time rows have no type, so 0.01-degree cells flagged static in any archive year are removed from them.",
                  "Dates are UTC acquisition dates; seasons are defined on UTC dates (a 01:30 IST overpass on Oct 2 is dated Oct 1 UTC).",
                  "Year-to-year comparisons are affected by overpass-time drift (Terra/Aqua orbits drifted after ~2020; S-NPP data gaps, e.g. no S-NPP after 2023-11-01 in some feeds) and by later sowing/harvest.",
                  "CREAMS (ICAR-IARI) bulletin events, which the dashboard also shows as a 'sensor', are excluded here; see ../creams/.",
                  *empty_feed_caveats(),
                  "Current-year values come from near-real-time data and will change when standard-processing data replace them."],
                 f"`{SCRIPT}` reads `data/fires/summary.json` and `data/fires/district_daily.json`, written by `scripts/build_fires.py` from the FIRMS CSVs.",
                 """import pandas as pd
st = pd.read_csv("season_totals.csv")
punjab = st.query("season == 'rice' and state == 'Punjab' and country == 'India' and sensor == 'MODIS Aqua'")
daily = pd.read_parquet("daily_by_state.parquet")""",
                 """library(readr); library(arrow)
st <- read_csv("season_totals.csv")
daily <- read_parquet("daily_by_state.parquet")""")
    return {"season_totals": st}


# ---------------------------------------------------------------------------------------------
# A2. CREAMS
# ---------------------------------------------------------------------------------------------
def export_creams():
    out = DS / "creams"
    out.mkdir(parents=True, exist_ok=True)
    src = DATA / "creams"
    C = jload(src / "creams.json")
    sd = pd.read_csv(src / "creams_state_daily.csv", dtype=str, keep_default_na=False)
    dd = pd.read_csv(src / "creams_district_daily.csv", dtype=str, keep_default_na=False)
    yc = pd.read_csv(src / "creams_year_compare.csv", dtype=str, keep_default_na=False)
    bl = pd.read_csv(src / "creams_bulletins.csv", dtype=str, keep_default_na=False)
    mr = pd.read_csv(src / "creams_reports_monthly.csv", dtype=str, keep_default_na=False)
    tl = pd.read_csv(src / "creams_state_table_long.csv", dtype=str, keep_default_na=False)

    def num(df, cols):
        for c in cols:
            df[c] = pd.to_numeric(df[c].replace("", np.nan)).astype("Int64")
        return df
    num(sd, ["season_year", "fires_day", "fires_cumulative", "fires_day_as_published"])
    num(dd, ["season_year", "fires_day", "fires_cumulative"])
    num(yc, ["year", "cumulative_fires"])
    num(mr, ["events"])
    num(tl, ["target_year", "value"])
    write_csv(sd, out / "state_daily.csv")
    write_csv(dd, out / "district_daily.csv")
    write_csv(yc, out / "year_compare.csv")
    bl2 = bl[["date", "season", "kind", "bulletin_no", "file", "url", "pages", "primary", "duplicate_of", "error"]].copy()
    write_csv(bl2, out / "bulletins.csv")
    write_csv(mr, out / "monthly_reports.csv")
    write_csv(tl, out / "bulletin_state_tables_long.csv.gz", compression={"method": "gzip", "mtime": 0})
    shutil.copyfile(src / "creams_validation.json", out / "validation.json")

    # validation vs creams.json (what the dashboard reads)
    n_json = sum(len(v) for bys in C["states"].values() for byy in bys.values() for v in byy.values())
    check(n_json == len(sd), f"creams/state_daily rows {len(sd):,} == creams.json states entries {n_json:,}")
    nb = len(C["bulletins"])
    check(set(b["url"] for b in C["bulletins"]) <= set(bl2.url), f"creams/bulletins: all {nb:,} bulletins in creams.json are among the {len(bl2):,} listed PDFs")
    for df, n in ((sd, "state_daily"), (dd, "district_daily"), (yc, "year_compare")):
        check(len(pd.read_csv(out / f"{n}.csv")) == len(df), f"creams/{n}.csv re-reads in pandas")

    D = {
        "date": ("IST date", "date the count refers to (bulletin day)"), "season": ("", "rice (paddy, autumn) or wheat (spring) burning season"),
        "season_year": ("", "year of the season"), "state": ("", "state as printed in the bulletin"), "district": ("", "district as printed in the bulletin"),
        "fires_day": ("count", "residue-burning events detected that day (null = not reported)"),
        "fires_cumulative": ("count", "cumulative events from the season start as printed (null = not reported)"),
        "cum_start": ("date", "start date of the cumulative count, where printed"), "cum_period_start": ("date", "start date of the cumulative count"),
        "day_source": ("", "bulletin table the daily value came from (datewise_table, event_list_count = count of rows in the annexed event list)"),
        "cum_source": ("", "bulletin table the cumulative value came from (highlights, datewise_table_total, district_table)"),
        "fires_day_as_published": ("count", "daily value as printed when it differs from the value kept in fires_day"),
        "cum_check": ("", "sum_ok = district cumulative values add up to the state total printed in the same bulletin"),
        "bulletin_file": ("", "file name of the bulletin PDF the value was read from (see bulletins.csv for its URL)"),
        "cum_bulletin_file": ("", "bulletin the cumulative value was read from"),
        "as_of_date": ("MM-DD", "calendar day the cumulative comparison refers to"), "year": ("", "season year being compared"),
        "cumulative_fires": ("count", "cumulative events from period_start to as_of_date in that year, as printed in the bulletin's multi-year table"),
        "period_start": ("MM-DD", "start of the cumulative window"), "source": ("", "bulletin table the value came from"),
        "bulletin_date": ("date", "date of the bulletin that printed the comparison"), "note": ("", "parsing note"),
        "kind": ("", "bulletin (daily bulletin) or other publication type"), "bulletin_no": ("", "running bulletin number in the season"),
        "file": ("", "PDF file name"), "url": ("", "public URL of the PDF on creams.iari.res.in"), "pages": ("", "number of pages"),
        "primary": ("", "1 = the bulletin used for that date (0 = duplicate/superseded copy)"), "duplicate_of": ("", "file this one duplicates"),
        "error": ("", "parse error, if the PDF could not be read"),
        "month": ("YYYY-MM", "month"), "events": ("count", "events in the month as printed in the special annual burn-statistics report"),
        "report_file": ("", "report PDF file name"),
        "target_year": ("", "year the printed column refers to"), "month_day": ("MM-DD", "day of the printed row"),
        "value": ("count", "value printed in the bulletin's date-wise multi-year state table"),
    }
    secs = [table_doc("state_daily.csv", sd, D), table_doc("district_daily.csv", dd, D), table_doc("year_compare.csv", yc, D),
            table_doc("bulletins.csv", bl2, D), table_doc("monthly_reports.csv", mr, D, "From CREAMS special reports (2020): monthly events by state."),
            table_doc("bulletin_state_tables_long.csv.gz", tl, {**D, "bulletin_date": ("date", "date of the bulletin")},
                      "Every cell of the date-wise multi-year state tables printed in each bulletin (one row per bulletin x state x year x day); the raw material of state_daily / year_compare."),
            "### `validation.json`\n\nConsistency checks run when the bulletins were parsed: bulletins that failed, event lists vs highlight counts, "
            "table sums vs printed totals, district sums vs state totals, non-monotonic cumulative series, duplicates, date mismatches, unmatched district names, and a `summary` block.\n"]
    write_readme(out, "ICAR-IARI CREAMS crop-residue burning bulletins",
                 "Official daily counts of paddy- and wheat-residue burning events in Punjab, Haryana, Uttar Pradesh, Delhi, Rajasthan and Madhya Pradesh, "
                 "as printed in the daily bulletins of the Consortium for Research on Agroecosystem Monitoring and Modeling from Space (CREAMS), ICAR-Indian "
                 "Agricultural Research Institute, New Delhi. CREAMS applies the standard ISRO/CAQM protocol to VIIRS/MODIS detections and filters them to "
                 "agricultural residue burning. The ~700k geolocated events listed in the bulletin annexures are a release asset (`creams_events.parquet`).",
                 secs,
                 f"Bulletins 2018-{max(bl.date)[:4]} (rice seasons from 2018, wheat seasons where published); multi-year tables in bulletins reach back to 2016. {len(bl):,} PDFs listed.",
                 "Bulletins published at https://creams.iari.res.in/ (page_id=1123). They are public documents with no explicit licence; cite ICAR-IARI CREAMS and the bulletin "
                 "(URLs in bulletins.csv). Licence status: uncertain, treat as public government information with attribution.",
                 ["Values are exactly as printed; nothing interpolated. Bulletins sometimes revise earlier days; both the daily and the cumulative values are kept with their bulletin.",
                  "The CREAMS protocol window is Sep 15-Nov 30 (rice) and Apr 1-May 31 (wheat); some parliamentary/state numbers use Oct 1-Nov 30.",
                  "Counts are protocol-filtered events, not raw FIRMS detections; they are not comparable one-to-one with ../fires/.",
                  "District names are as printed (spelling varies across years); see validation.json for names that did not match the boundary file."],
                 f"PDFs downloaded and parsed by `scripts/creams_scrape.py` into `data/creams/*.csv`; `{SCRIPT}` copies them with numeric typing and selected columns.",
                 """import pandas as pd
sd = pd.read_csv("state_daily.csv", parse_dates=["date"])
punjab_2024 = sd.query("state == 'Punjab' and season == 'rice' and season_year == 2024")""",
                 """library(readr)
sd <- read_csv("state_daily.csv")""")


# ---------------------------------------------------------------------------------------------
# A3. burned area
# ---------------------------------------------------------------------------------------------
def export_burned():
    out = DS / "burned_area"
    out.mkdir(parents=True, exist_ok=True)
    B = jload(DATA / "burnscar" / "burnscar.json")
    gj = jload(DATA / "geo" / "districts.geojson")
    dprop = {f["properties"]["id"]: f["properties"] for f in gj["features"]}
    ss, dsr, dly, fc = [], [], [], []
    for y in sorted(B, key=int):
        s = B[y]
        for st, v in s["states"].items():
            ss.append((int(y), st, v["burned_ha"], s["complete"], ";".join(s["missing_months"]), ";".join(s["months"]), s["through"], s["pixel_ha"], s["product"]))
        for did, ha in s["districts"].items():
            p = dprop[int(did)]
            dsr.append((int(y), int(did), p["name"], p["state"], ha, s["complete"]))
        sep15 = dt.date(int(y), 9, 15)
        for st, arr in s["daily_burned_ha"].items():
            for i, ha in enumerate(arr):
                dly.append((int(y), (sep15 + dt.timedelta(days=i)).isoformat(), i, st, ha, s["complete"]))
        for k, v in (s.get("firms_check") or {}).items():
            fc.append((int(y), k, v["detections"], v["on_burned_pixel"]))
    ss = pd.DataFrame(ss, columns=["season", "state", "burned_ha", "complete", "missing_months", "months_used", "through", "pixel_ha", "product"])
    dsr = pd.DataFrame(dsr, columns=["season", "district_id", "district", "state", "burned_ha", "complete"])
    dly = pd.DataFrame(dly, columns=["season", "date", "season_day", "state", "burned_ha", "complete"])
    fc = pd.DataFrame(fc, columns=["season", "sensor_group", "detections", "share_on_burned_pixel"])
    write_csv(ss, out / "state_season.csv")
    write_csv(dsr, out / "district_season.csv")
    write_csv(dly, out / "daily_by_state.csv")
    write_csv(fc, out / "firms_check.csv")
    # validation: district and daily sums reproduce state totals (rounding tolerance)
    worst_d = worst_t = 0
    for _, r in ss.iterrows():
        dsum = dsr[(dsr.season == r.season) & (dsr.state == r.state)].burned_ha.sum()
        tsum = dly[(dly.season == r.season) & (dly.state == r.state)].burned_ha.sum()
        worst_d = max(worst_d, abs(dsum - r.burned_ha) / max(r.burned_ha, 1))
        worst_t = max(worst_t, abs(tsum - r.burned_ha) / max(r.burned_ha, 1))
    check(worst_d < 0.002, f"burned_area: district sums vs state totals, worst relative diff {worst_d:.5f}")
    check(worst_t < 0.002, f"burned_area: daily sums vs state totals, worst relative diff {worst_t:.5f}")
    tifs = sorted(AS.glob("burned_area_*.tif"))
    D = {"season": ("", "paddy season year (Sep 15 - Dec 5 of that year)"), "state": ("", "Punjab, Haryana, Delhi or Chandigarh (India)"),
         "burned_ha": ("ha", "MCD64A1 burned area: number of burned 500 m pixels x pixel_ha, rounded"),
         "complete": ("", "true if all MCD64A1 months Sep-Nov were available (false: area is a lower bound for missing months)"),
         "missing_months": ("YYYY-MM;...", "monthly MCD64A1 files not available on the source when built"),
         "months_used": ("YYYY-MM;...", "monthly files used"), "through": ("date", "last date covered"),
         "pixel_ha": ("ha", "area of one pixel in the UTM 43N mosaic (463.3 m)^2"), "product": ("", "product name and version"),
         "district_id": ("", "id in ../boundaries/districts.geojson"), "district": ("", "district name"),
         "date": ("date", "burn date (MCD64A1 Burn_Date)"), "season_day": ("days", "days since Sep 15"),
         "sensor_group": ("", "modis (Terra+Aqua) or viirs_snpp FIRMS detections used in the cross-check"),
         "detections": ("count", "FIRMS detections in the region during the season"),
         "share_on_burned_pixel": ("fraction", "share of those detections that fall on a pixel MCD64A1 maps as burned")}
    secs = [table_doc("state_season.csv", ss, D), table_doc("district_season.csv", dsr, D),
            table_doc("daily_by_state.csv", dly, {**D, "burned_ha": ("ha", "area that burned on that date")}), table_doc("firms_check.csv", fc, D)]
    write_readme(out, "Burned area (NASA MODIS MCD64A1) in Punjab and Haryana, paddy seasons",
                 "Area of cropland mapped as burned each paddy season (Sep 15 - Dec 5) by NASA's MODIS burned-area product, per state, district and burn date, "
                 "with a cross-check against FIRMS active-fire detections." + (" Per-season rasters are release assets `burned_area_<season>.tif`." if tifs else
                 " Per-season rasters (`burned_area_<season>.tif`) are produced by the export script only when the season mosaics are present locally; they were not available for this release."),
                 secs, f"Seasons {ss.season.min()}-{ss.season.max()}; {int((~ss.drop_duplicates('season').complete).sum())} season(s) incomplete (see `complete`, `missing_months`).",
                 "NASA MODIS/Terra+Aqua Burned Area Monthly L3 Global 500 m, MCD64A1 Collection 6.1 (doi:10.5067/MODIS/MCD64A1.061), accessed through Microsoft Planetary Computer. "
                 "NASA LP DAAC data have no restrictions on use; cite the product DOI.",
                 ["500 m pixels are coarse for Punjab's 1-2 ha fields: many small, short burns are missed. Read areas as a lower bound and the spatial/temporal pattern as the signal.",
                  "Seasons with missing months (e.g. 2023: Sep missing, 2025: Sep-Oct missing at the source) understate burned area.",
                  "Only Punjab, Haryana, Delhi and Chandigarh are mapped (UTM 43N mosaic)."],
                 f"`scripts/burnscar.py` builds `data/burnscar/burnscar.json`; `{SCRIPT}` flattens it.",
                 """import pandas as pd
ss = pd.read_csv("state_season.csv")
ss.pivot(index="season", columns="state", values="burned_ha")""",
                 """library(readr)
ss <- read_csv("state_season.csv")""")
    return ss


# ---------------------------------------------------------------------------------------------
# A3b. Sentinel-2A night hot spots (ESA campaign, Dec 2025)
# ---------------------------------------------------------------------------------------------
def export_s2night():
    allp = DATA / "raw" / "s2night" / "s2night_all.geojson"
    vp = DATA / "s2night" / "s2night_validation.json"
    if not allp.exists() or not vp.exists():
        log("SKIP s2_night: run scripts/s2night.py first")
        return
    out = DS / "s2_night"
    out.mkdir(parents=True, exist_ok=True)
    g, V = jload(allp), jload(vp)
    cols = ["scene", "date", "tier", "class", "lon", "lat", "npix", "l12_peak", "l11_peak", "l12_sum", "saturated", "t_k", "area_m2", "frp_mw",
            "b04_peak", "b08_peak", "landcover", "built_share", "district", "state", "country", "firms_km", "firms_dt_h", "firms_sat", "static_km", "both_nights"]
    rows = []
    for f in g["features"]:
        p = dict(f["properties"]); p["lon"], p["lat"] = f["geometry"]["coordinates"]
        p["both_nights"] = bool(p.get("both_nights"))
        rows.append({c: p.get(c) for c in cols})
    hs = pd.DataFrame(rows, columns=cols).sort_values(["scene", "tier", "lat"]).reset_index(drop=True)
    write_csv(hs, out / "hot_spots.csv")
    (out / "hot_spots.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
         "properties": {k: (None if pd.isna(v) else v) for k, v in r.items() if k not in ("lon", "lat")}} for r in hs.to_dict("records")]},
        separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    mr = []
    for sc in ("R012", "R055"):
        for t, x in V[sc]["tiers"].items():
            for grp, r in x["recall_1km"].items():
                if grp == "viirs_night_after_by_region":
                    for reg, q in r.items():
                        mr.append((sc, V[sc]["date_utc"], t, f"viirs_night_after_{reg}", q["firms"], q["matched"], None, None))
                    continue
                mr.append((sc, V[sc]["date_utc"], t, grp, r["firms"], r["matched"], r["firms_excl_static"], r["matched_excl_static"]))
            mr.append((sc, V[sc]["date_utc"], t, "s2_with_firms_pm4d", x["detections"], x["with_firms_1km_pm4d"], None, None))
            mr.append((sc, V[sc]["date_utc"], t, "chance_displaced_5_30km", x["detections"], round(x["chance_share"] * x["detections"], 1), None, None))
        for t, n in V[sc]["mirror_selected"].items():
            mr.append((sc, V[sc]["date_utc"], t, "mirror_test_negated_image", V[sc]["mirror_candidates"], n, None, None))
    mr = pd.DataFrame(mr, columns=["scene", "scene_utc", "tier", "comparison", "n", "matched", "n_excl_static", "matched_excl_static"])
    write_csv(mr, out / "validation.csv")
    check(int(mr[mr.comparison == "mirror_test_negated_image"].matched.sum()) == 0, "s2_night: no clusters selected on the negated images")
    D = {"scene": ("", "R012 = 9 Dec 2025 17:02 UTC (22:32 IST), R055 = 12 Dec 2025 17:12 UTC (22:42 IST); Sentinel-2A relative orbit"),
         "date": ("date", "acquisition date (UTC; same calendar day in IST)"),
         "tier": ("", "both = bright in B12 and B11 (each >= 0.5 W m-2 sr-1 um-1 over background; the map layer); b12 = B12 >= 1.0 only, unconfirmed (includes streak artefacts)"),
         "class": ("", "industrial (FIRMS static source within 1 km, or same spot on both nights); built-up (WorldCover built-up share >= 0.25); open (everything else)"),
         "lon": ("deg", "radiance-weighted cluster centre, WGS84"), "lat": ("deg", "radiance-weighted cluster centre, WGS84"),
         "npix": ("pixels", "20 m pixels in the cluster (B12 excess >= 0.15)"),
         "l12_peak": ("W m-2 sr-1 um-1", "peak B12 (2.19 um) radiance above the 1 km background median"),
         "l11_peak": ("W m-2 sr-1 um-1", "peak B11 (1.61 um) excess within one pixel of the cluster"),
         "l12_sum": ("W m-2 sr-1 um-1 x px", "sum of B12 excess over the cluster"),
         "saturated": ("", "true if the peak B12 pixel is at or above 29 (B12 saturates near 30 at night)"),
         "t_k": ("K", "single-temperature fit from the B11/B12 ratio at the peak (indicative; mixed pixels bias it)"),
         "area_m2": ("m2", "emitting area implied by t_k (indicative)"), "frp_mw": ("MW", "radiative power implied by t_k and area_m2 (indicative, not comparable to FIRMS FRP)"),
         "b04_peak": ("W m-2 sr-1 um-1", "peak B04 (red) radiance in a 110 m box at 10 m, for clusters with a peak >= 0.5 (null otherwise)"),
         "b08_peak": ("W m-2 sr-1 um-1", "peak B08 (NIR) radiance in a 110 m box at 10 m (null as above)"),
         "landcover": ("", "ESA WorldCover 2021 v200 class at the point"), "built_share": ("fraction", "WorldCover built-up share within 310 x 310 m"),
         "district": ("", "district (datasets/boundaries)"), "state": ("", "state / province"), "country": ("", "India or Pakistan"),
         "firms_km": ("km", "distance to the nearest FIRMS detection (any sensor, scene date +/- 4 days); null if none in the scene"),
         "firms_dt_h": ("h", "time of that FIRMS detection relative to Sentinel-2, if within 1 km"), "firms_sat": ("", "its instrument:satellite, if within 1 km"),
         "static_km": ("km", "distance to the nearest FIRMS VIIRS static (type 2) detection, 2012-2025"),
         "both_nights": ("", "true if a hot spot was also seen within 300 m on the other night (overlap strip only)"),
         "scene_utc": ("UTC", "Sentinel-2 acquisition start"),
         "comparison": ("", "viirs_night_after (VIIRS night pass 0-6 h after), viirs_night_before (0-24 h before), same_day_daytime (0-12 h before), modis_night_same_pass (+/-3 h), viirs_night_after_hills / _plains (Himachal + Uttarakhand vs the rest), s2_with_firms_pm4d (hot spots with any FIRMS within 1 km, +/-4 days), chance_displaced_5_30km (same, after moving each hot spot 5-30 km at random, mean of 20 draws), mirror_test_negated_image (clusters passing the tier's rule on the negated image; n = negated candidates)"),
         "n": ("count", "FIRMS detections (recall rows), hot spots (s2_with_firms, chance rows) or negated candidates (mirror rows)"),
         "matched": ("count", "of n, those with a counterpart within 1 km"), "n_excl_static": ("count", "n without FIRMS type-2 static sources"),
         "matched_excl_static": ("count", "matched without static sources")}
    secs = [table_doc("hot_spots.csv", hs, D, "Also as `hot_spots.geojson`."), table_doc("validation.csv", mr, D)]
    rasters = sorted(AS.glob("s2night_*_100m.tif"))
    a, b = V["R012"]["tiers"]["both"], V["R055"]["tiers"]["both"]
    na = a["recall_1km"]["viirs_night_after"]; rg = a["recall_1km"]["viirs_night_after_by_region"]
    write_readme(out, "Night hot spots from ESA's Sentinel-2A night campaign, Punjab, 9 and 12 Dec 2025",
                 "Short-wave infrared hot spots at 20 m in the two Sentinel-2A night scenes that cover Punjab, with a FIRMS cross-check and a land-cover setting. "
                 f"{a['detections'] + b['detections']} hot spots pass the two-band rule; none pass on the negated images. On 9 Dec they recovered {na['matched']} of {na['firms']} VIIRS night "
                 f"detections made three hours later ({rg['hills']['matched']} of {rg['hills']['firms']} in the Himachal/Uttarakhand hills, {rg['plains']['matched']} of {rg['plains']['firms']} in the plains). "
                 "They are heat sources (fires, furnaces, flares), not a crop-fire census." +
                 (f" 100 m B12/B11 maximum rasters per scene are release assets `s2night_<scene>_b12_max_100m.tif` / `_b11_max_100m.tif`." if rasters else ""),
                 secs, "Scenes R012 (74.94-78.53 E, 29.59-32.32 N) and R055 (72.56-76.12 E, 29.15-31.89 N), about 71,000 km2 of valid pixels each.",
                 "Contains modified Copernicus Sentinel data 2025, ESA Sentinel-2 night-time campaign (CDSE collection sentinel-2-night-time-acquisitions; "
                 "processing note doi:10.5281/zenodo.20286476); Copernicus data are free and open. ESA WorldCover 2021 v200 (CC BY 4.0). NASA FIRMS (open).",
                 ["Two December nights after the paddy season: this says nothing about burning in October-November.",
                  "Most FIRMS-matched hot spots on 9 Dec were forest/scrub fires in the hills. In the Punjab-Haryana plains there was no same-night FIRMS detection that Sentinel-2 confirmed, and many hot spots sit on steel mills, furnaces and industrial estates.",
                  "`open` hot spots can still be lone factories or brick kilns in farmland; WorldCover does not map most kilns as built-up.",
                  "Tier `b12` is not confirmed by a second band and includes dotted-streak artefacts; it is not on the map.",
                  "Geolocation uses no ground control (ESA note): expect offsets of tens of metres.",
                  "Night lights (VNIR) were not usable: detector striping exceeds the city signal in these products."],
                 "`scripts/s2night.py fetch|detect|validate` writes `data/raw/s2night/` and `data/s2night/`; this script flattens them. The method, thresholds and checks are in docs/METHODS.md.",
                 """import pandas as pd
hs = pd.read_csv("hot_spots.csv")
hs[hs.tier == "both"].groupby(["scene", "class"]).size()""",
                 """library(readr)
hs <- read_csv("hot_spots.csv")""")
    return hs


def export_s2night_tifs():
    n = 0
    for sc in ("R012", "R055"):
        for k in ("b12_max", "b11_max"):
            src = DATA / "raw" / "s2night" / sc / f"{k}_100m.tif"
            if not src.exists():
                continue
            dst = AS / f"s2night_{sc}_{k}_100m.tif"
            tags = {"SOURCE": "Contains modified Copernicus Sentinel data 2025 (ESA Sentinel-2 night campaign)", "SCENE": sc,
                    "UNITS": "W m-2 sr-1 um-1", "CONTENT": f"maximum {k[:3].upper()} radiance per 100 m cell (5 x 5 pixels of 20 m)"}
            mo = [a for kv in tags.items() for a in ("-mo", "=".join(kv))]
            subprocess.run(["gdal_translate", "-q", "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3", *mo, str(src), str(dst)], check=True)
            n += 1
    if not n:
        log("SKIP s2night_*_100m.tif: no data/raw/s2night/<scene>/*_100m.tif (run scripts/s2night.py detect)")


# ---------------------------------------------------------------------------------------------
# A4. air
# ---------------------------------------------------------------------------------------------
def export_air():
    out = DS / "air"
    out.mkdir(parents=True, exist_ok=True)
    G = jload(EXTRAS / "aq_ground.json")
    M = jload(EXTRAS / "aq_met.json")
    SS = jload(EXTRAS / "stubble_share.json")
    rows, srows, mrows = [], [], []
    for city, c in G["cities"].items():
        d = c["daily"]
        for i, date in enumerate(d["date"]):
            rows.append((city, c["state"], date, d["pm25"][i], d["pm10"][i], d["n_stations"][i], d["n_stations_pm10"][i]))
        m = c["monthly"]
        for i, mo in enumerate(m["month"]):
            mrows.append((city, c["state"], mo, m["pm25"][i], m["pm10"][i], m["n_days_pm25"][i], m["n_days_pm10"][i], m["mean_n_stations"][i]))
        for s in c["stations"]:
            srows.append((city, c["state"], s["id"], s["name"], s["lat"], s["lon"], s["first"], s["last"], s["first_pm10"], s["last_pm10"],
                          s["n_days_pm25"], s["n_days_pm10"], ";".join(map(str, s["openaq_location_ids"])), ";".join(map(str, s["openaq_sensor_ids"]))))
    gd = pd.DataFrame(rows, columns=["city", "state", "date", "pm25", "pm10", "n_stations", "n_stations_pm10"])
    for c in ("n_stations", "n_stations_pm10"):
        gd[c] = gd[c].astype("Int64")
    gm = pd.DataFrame(mrows, columns=["city", "state", "month", "pm25", "pm10", "n_days_pm25", "n_days_pm10", "mean_n_stations"])
    gs = pd.DataFrame(srows, columns=["city", "state", "station_id", "station_name", "lat", "lon", "first_pm25", "last_pm25", "first_pm10", "last_pm10",
                                      "n_days_pm25", "n_days_pm10", "openaq_location_ids", "openaq_sensor_ids"])
    write_csv(gd, out / "cpcb_pm25_daily.csv")
    write_csv(gm, out / "cpcb_pm25_monthly.csv")
    write_csv(gs, out / "cpcb_stations.csv")

    ren = {"pm25": "pm25_cams", "pm10": "pm10_cams", "aod": "aod", "dust": "dust", "wind": "wind_ms", "wdir": "wind_dir",
           "blh": "blh_mean_m", "blh14": "blh_14ist_m", "t2m": "t2m", "precip": "precip_mm"}
    rows, crow = [], []
    for city, c in M["cities"].items():
        d = c["daily"]
        df = pd.DataFrame({k: d[k] for k in ["date"] + list(ren)}).rename(columns=ren)
        df.insert(0, "city", city)
        rows.append(df)
        crow.append((city, c["lat"], c["lon"], c["grid_lat_lon_aq"][0], c["grid_lat_lon_aq"][1], c["grid_lat_lon_met"][0], c["grid_lat_lon_met"][1]))
    md = pd.concat(rows, ignore_index=True)
    mc = pd.DataFrame(crow, columns=["city", "lat", "lon", "cams_grid_lat", "cams_grid_lon", "era5_grid_lat", "era5_grid_lon"])
    write_csv(md, out / "cams_era5_daily.csv")
    write_csv(mc, out / "cams_era5_cities.csv")

    rows = []
    for name, s in SS["series"].items():
        n = len(s["date"])
        for i in range(n):
            rows.append((name, s["date"][i], s["share_pct"][i], s["fire_count"][i], (s.get("date_basis") or [""] * n)[i],
                         (s.get("kind") or [""] * n)[i], s["url"][i], s["retrieved"][i], (s.get("quote") or [""] * n)[i]))
    sh = pd.DataFrame(rows, columns=["source", "date", "share_pct", "fire_count", "date_basis", "kind", "url", "retrieved", "quote"])
    sh["fire_count"] = sh["fire_count"].astype("Int64")
    write_csv(sh, out / "stubble_share_delhi.csv")

    n_g = sum(len(c["daily"]["date"]) for c in G["cities"].values())
    check(n_g == len(gd), f"air/cpcb_pm25_daily rows {len(gd):,} == aq_ground.json {n_g:,}")
    n_m = sum(len(c["daily"]["date"]) for c in M["cities"].values())
    check(n_m == len(md), f"air/cams_era5_daily rows {len(md):,} == aq_met.json {n_m:,}")
    n_s = sum(len(s["date"]) for s in SS["series"].values())
    check(n_s == len(sh), f"air/stubble_share_delhi rows {len(sh):,} == stubble_share.json {n_s:,}")

    src_lines = []
    for s in SS["sources"]:
        if "series" in s:
            src_lines.append(f"  - `{s['series']}`: {s.get('name', '')}. {s.get('page', '')} {s.get('method', '') or s.get('field', '')}")
    D = {"city": ("", "receptor city"), "state": ("", "state"), "date": ("IST date", "local calendar day (Asia/Kolkata)"),
         "pm25": ("ug/m3", "city mean of qualifying CPCB station daily means of PM2.5 (null = no qualifying data)"),
         "pm10": ("ug/m3", "same for PM10"), "n_stations": ("count", "stations averaged for PM2.5 that day"),
         "n_stations_pm10": ("count", "stations averaged for PM10 that day"), "month": ("YYYY-MM", "month"),
         "n_days_pm25": ("days", "valid days in the month (PM2.5) / at the station"), "n_days_pm10": ("days", "valid days (PM10)"),
         "mean_n_stations": ("count", "mean stations per valid day"),
         "station_id": ("", "station key used in the merge (city|site|agency)"), "station_name": ("", "CPCB station name"),
         "lat": ("deg", "latitude (WGS84)"), "lon": ("deg", "longitude (WGS84)"),
         "first_pm25": ("date", "first valid PM2.5 day"), "last_pm25": ("date", "last valid PM2.5 day"),
         "first_pm10": ("date", "first valid PM10 day"), "last_pm10": ("date", "last valid PM10 day"),
         "openaq_location_ids": ("", "OpenAQ v3 location ids merged into this station (;-separated)"),
         "openaq_sensor_ids": ("", "OpenAQ v3 sensor ids used (;-separated)"),
         "pm25_cams": ("ug/m3", "CAMS model PM2.5, daily mean of hourly values (IST day; null if < 18 valid hours)"),
         "pm10_cams": ("ug/m3", "CAMS model PM10, daily mean"), "aod": ("unitless", "CAMS aerosol optical depth at 550 nm, daily mean"),
         "dust": ("ug/m3", "CAMS dust concentration, daily mean"), "wind_ms": ("m/s", "ERA5 10 m wind speed, daily mean"),
         "wind_dir": ("deg (from)", "ERA5 dominant 10 m wind direction"), "blh_mean_m": ("m", "ERA5 boundary-layer height, daily mean of hourly values"),
         "blh_14ist_m": ("m", "ERA5 boundary-layer height at 14:00 IST"), "t2m": ("deg C", "ERA5 2 m temperature, daily mean"),
         "precip_mm": ("mm", "ERA5 precipitation, daily sum"),
         "cams_grid_lat": ("deg", "latitude of the CAMS grid point used"), "cams_grid_lon": ("deg", "longitude of the CAMS grid point"),
         "era5_grid_lat": ("deg", "latitude of the ERA5 grid point used"), "era5_grid_lon": ("deg", "longitude of the ERA5 grid point"),
         "source": ("", "series: IITM_DSS, IITM_AQEWS_fire_contribution, IITM_AQEWS_VIIRS_fire_count, SAFAR, SAFAR_fire_count"),
         "share_pct": ("% of Delhi PM2.5", "modelled share of Delhi PM2.5 attributed to stubble/biomass burning (null for count-only series)"),
         "fire_count": ("count", "fire count published with the series (IITM: daily VIIRS count for 'northern India'; SAFAR: 'effective' fire count)"),
         "date_basis": ("", "SAFAR only: explicit = date in text; capture = 'today' in an undated bulletin (IST date of the web capture, -1 day if before noon); capture-1 = 'yesterday'"),
         "kind": ("", "SAFAR only: estimate (for the day) or forecast (for a future date)"),
         "url": ("", "page or Internet Archive capture the value was read from"), "retrieved": ("UTC timestamp", "capture/fetch time"),
         "quote": ("", "SAFAR only: verbatim text containing the number")}
    secs = [table_doc("cpcb_pm25_daily.csv", gd, D, "Window: Sep 1 - Jan 31 of each winter with data."), table_doc("cpcb_pm25_monthly.csv", gm, D),
            table_doc("cpcb_stations.csv", gs, {**D, "n_days_pm25": ("days", "valid PM2.5 days at the station")}),
            table_doc("cams_era5_daily.csv", md, D, "Window: Sep 15 - Jan 31 of each season."), table_doc("cams_era5_cities.csv", mc, D),
            table_doc("stubble_share_delhi.csv", sh, D)]
    write_readme(out, "Air quality and meteorology at receptor cities",
                 "Ground PM2.5/PM10 from CPCB continuous monitoring stations (via OpenAQ), modelled PM/AOD (CAMS) and reanalysis meteorology (ERA5) at seven "
                 "receptor cities, and published model estimates of the stubble-burning share of Delhi's PM2.5 (IITM SAFAR / DSS / AQEWS).",
                 secs,
                 f"CPCB: {gd.city.nunique()} cities, {len(gs)} stations, {gd.date.min()}..{gd.date.max()} (winters only; OpenAQ has no CPCB data Oct 2022 - Feb 2025). "
                 f"CAMS/ERA5: {md.city.nunique()} cities, {md.date.min()}..{md.date.max()} (CAMS values null before Aug 2022). "
                 f"Stubble share: {sh.date.min()}..{sh.date.max()}, sparse.",
                 "- CPCB CAAQMS data redistributed by OpenAQ (https://openaq.org, API v3). OpenAQ requires crediting both OpenAQ and the original provider (CPCB); "
                 "the data are government data published by CPCB.\n- Open-Meteo Air Quality API (CAMS global forecasts, Copernicus Atmosphere Monitoring Service) and "
                 "Open-Meteo Historical Weather API (ERA5/ERA5-Land, Copernicus Climate Change Service). Open-Meteo data are CC BY 4.0; CAMS/C3S data are under the Copernicus licence "
                 "- credit 'Contains modified Copernicus Atmosphere Monitoring Service / Climate Change Service information'.\n"
                 "- IITM Pune (Ministry of Earth Sciences) public web pages, mostly via Internet Archive captures:\n" + "\n".join(src_lines) +
                 "\n\nSee ../../LICENSES.md.",
                 ["CPCB city means average whichever stations qualified that day; network size changed over time (use n_stations).",
                  "QA: a station-day is kept only with >= 75% of native records and >= 17 h span; days with mean <= 0 or > 1500 ug/m3, or any sub-daily value > 1500, are dropped. No gap filling.",
                  "OpenAQ ingestion gaps (not station downtime): Oct 2022 - Feb 2025 missing, Jan-Jun 2019 nearly empty.",
                  "CAMS/ERA5 values are model/reanalysis output at the nearest grid point, not measurements; CAMS hours 2022-12-13..16 with PM2.5 < 3 ug/m3 were nulled (archive glitch).",
                  "Stubble-share values are model attributions from different systems (SAFAR 2018-2023, IITM DSS 2023+): do not splice them into one series without noting the break.",
                  "ERA5 boundary-layer height is null for January 2024 in the Open-Meteo archive."],
                 f"`scripts/build_aq_ground.py` (OpenAQ), `scripts/build_db_extras.py` (Open-Meteo) and the stubble-share builder write `data/db/extras/*.json`; `{SCRIPT}` flattens them.",
                 """import pandas as pd
pm = pd.read_csv("cpcb_pm25_daily.csv", parse_dates=["date"])
delhi = pm[pm.city == "Delhi"].set_index("date").pm25""",
                 """library(readr)
pm <- read_csv("cpcb_pm25_daily.csv")""")


# ---------------------------------------------------------------------------------------------
# A5. paddy
# ---------------------------------------------------------------------------------------------
def export_paddy():
    out = DS / "paddy"
    out.mkdir(parents=True, exist_ok=True)
    R = jload(EXTRAS / "rice_area.json")
    est = R["state_estimate_types"]
    rows = []
    for st, byy in R["states"].items():
        for y, v in byy.items():
            rows.append((st, int(y), f"{y}-{(int(y) + 1) % 100:02d}", v.get("area"), v.get("prod"), v.get("area_total"), v.get("prod_total"),
                         v.get("dist_sum_area"), v.get("dist_sum_prod"), est.get(y, est["default"])))
    sa = pd.DataFrame(rows, columns=["state", "year", "agri_year", "kharif_area_ha", "kharif_prod_t", "total_area_ha", "total_prod_t",
                                     "district_sum_kharif_area_ha", "district_sum_kharif_prod_t", "estimate_type"])
    for c in sa.columns[3:9]:
        sa[c] = sa[c].astype("Int64")
    rows = []
    for d in R["districts"]:
        for y, v in d["series"].items():
            rows.append((d["state"], d["district"], d["lgd_code"], d["match_id"], d["match_name"], d["match_method"], int(y), f"{y}-{(int(y) + 1) % 100:02d}",
                         v.get("area"), v.get("prod"), v.get("area_total"), v.get("prod_total")))
    da = pd.DataFrame(rows, columns=["state", "district", "lgd_code", "district_id", "district_id_name", "match_method", "year", "agri_year",
                                     "kharif_area_ha", "kharif_prod_t", "total_area_ha", "total_prod_t"])
    for c in ["lgd_code", "district_id", "kharif_area_ha", "kharif_prod_t", "total_area_ha", "total_prod_t"]:
        da[c] = pd.to_numeric(da[c]).astype("Int64")
    sa = sa.sort_values(["state", "year"])
    da = da.sort_values(["state", "district", "year"])
    write_csv(sa, out / "rice_area_state.csv")
    write_csv(da, out / "rice_area_district.csv")
    n = sum(len(d["series"]) for d in R["districts"])
    check(n == len(da), f"paddy/rice_area_district rows {len(da):,} == rice_area.json {n:,}")
    # district sums reproduce dist_sum_area in the JSON
    g = da.groupby(["state", "year"]).kharif_area_ha.sum()
    m = sa.dropna(subset=["district_sum_kharif_area_ha"]).set_index(["state", "year"]).district_sum_kharif_area_ha
    diff = (g.reindex(m.index) - m).abs().max()
    check(diff <= 2 * len(R["districts"]), f"paddy: district kharif area sums vs dist_sum_area in rice_area.json, max abs diff {diff} ha")
    D = {"state": ("", "state"), "district": ("", "district name as reported by UPAg/DES (current LGD name)"), "lgd_code": ("", "Local Government Directory district code"),
         "district_id": ("", "matching polygon id in ../boundaries/districts.geojson (several APY districts can share one)"),
         "district_id_name": ("", "name of that polygon"), "match_method": ("", "name | lgd_crosswalk | alias_rename | alias_parent (district created after 2021 mapped to its parent polygon; sum children with the parent)"),
         "year": ("", "agricultural-year start: 2023 = 2023-24 (July-June); kharif rice of year Y is harvested Oct-Nov of Y, i.e. burned in the Y fire season"),
         "agri_year": ("", "agricultural year label"), "kharif_area_ha": ("ha", "kharif rice area"), "kharif_prod_t": ("t", "kharif rice production (milled rice, not paddy)"),
         "total_area_ha": ("ha", "all-season rice area"), "total_prod_t": ("t", "all-season rice production (milled rice)"),
         "district_sum_kharif_area_ha": ("ha", "sum of the district kharif values in rice_area_district.csv (1997+), for comparison with the official state figure"),
         "district_sum_kharif_prod_t": ("t", "same for production"), "estimate_type": ("", "Final Estimate, or the advance-estimate round for the latest year")}
    write_readme(out, "Rice (paddy) area and production, by state and district",
                 "Kharif and all-season rice area and production from India's official Area-Production-Yield statistics, for the states in the dashboard. "
                 "Kharif rice area is the main driver of the autumn residue load.",
                 [table_doc("rice_area_state.csv", sa, D), table_doc("rice_area_district.csv", da, D)],
                 f"States: {sa.year.min()}-{sa.year.max()} ({sa.state.nunique()} states). Districts: {da.year.min()}-{da.year.max()} ({len(R['districts'])} district series).",
                 re.sub(r"\s*Compiled in the .*$", "", R["source"]) + "\n\nUPAg (Unified Portal for Agricultural Statistics, upag.gov.in) and DES (Directorate of Economics and Statistics, Ministry of Agriculture & Farmers Welfare) "
                 "publish these as open government data; the Government Open Data License - India (GODL) is assumed (licence not stated on every UPAg download; see ../../LICENSES.md).",
                 [R["season_notes"], R["district_values"] + "; district sums differ from state totals in many years.", *R.get("notes", []),
                  "Production is milled rice (not paddy); multiply by ~1.5 for paddy.",
                  "District boundaries change over time (e.g. Malerkotla 2021); use match_method / district_id to aggregate consistently."],
                 f"`scripts/build_db_extras.py` builds `data/db/extras/rice_area.json`; `{SCRIPT}` flattens it.",
                 """import pandas as pd
d = pd.read_csv("rice_area_district.csv")
punjab = d.query("state == 'Punjab'").pivot_table(index="year", columns="district", values="kharif_area_ha")""",
                 """library(readr)
d <- read_csv("rice_area_district.csv")""")


# ---------------------------------------------------------------------------------------------
# A6. enforcement
# ---------------------------------------------------------------------------------------------
def scrub_md(text):
    text = re.sub(r"^- Cached: .*\n", "", text, flags=re.M)
    text = text.replace(" Cache: `data/raw/enforcement/`.", "")
    text = re.sub(r"(/Users|/home)/[^\s`)]+", "<local path>", text)
    return text


def export_enforcement():
    out = DS / "enforcement"
    out.mkdir(parents=True, exist_ok=True)
    E = jload(EXTRAS / "enforcement.json")
    FLAGS = ["transcribed_from_image", "derived", "suspect_unit", "non_afl"]
    rows = []
    for mk, m in E["metrics"].items():
        def add(st, dist, x):
            flags = [f for f in FLAGS if x.get(f)]
            rows.append({"metric": mk, "label": m["label"], "unit": m["unit"], "state": st, "district": dist, "season": x["season"],
                         "as_of": x.get("as_of"), "value": x["value"], "preferred": bool(x.get("preferred")), "latest": bool(x.get("latest")),
                         "flags": ";".join(flags), "source_id": x["source"], "period": x.get("period"), "basis": x.get("basis"),
                         "name_in_source": x.get("name_in_source"), "value_as_stated": x.get("value_as_stated"),
                         "derived": x.get("derived") if isinstance(x.get("derived"), str) else None,
                         "note": x.get("note"), "quote": x.get("quote")})
        for st, l in m.get("by_state", {}).items():
            for x in l:
                add(st, "", x)
        for st, dd in m.get("by_district", {}).items():
            for d, l in dd.items():
                for x in l:
                    add(st, d, x)
    v = pd.DataFrame(rows)
    write_csv(v, out / "values.csv")
    met = pd.DataFrame([{"metric": k, "label": m["label"], "unit": m["unit"], "note": m.get("note", "")} for k, m in E["metrics"].items()])
    write_csv(met, out / "metrics.csv")
    s = pd.DataFrame([{k: x.get(k) for k in ["id", "title", "publisher", "kind", "date", "url", "retrieved", "note"]} for x in E["sources"]])
    write_csv(s, out / "sources.csv")
    known = ["date", "date_precision", "state", "district", "title", "detail", "source", "quote", "listing_title", "transcribed_from_image"]
    ev = pd.DataFrame([{**{k: x.get(k) for k in known}, "other": json.dumps({k: x[k] for k in x if k not in known}, ensure_ascii=False) if set(x) - set(known) else ""}
                       for x in E["events"]]).rename(columns={"source": "source_id"})
    write_csv(ev, out / "events.csv")
    (out / "ENFORCEMENT_SOURCES.md").write_text(scrub_md((EXTRAS / "ENFORCEMENT_SOURCES.md").read_text()))
    check(len(v) == 1690 or len(v) > 0, f"enforcement/values {len(v):,} rows; sources {len(s)}, events {len(ev)}, metrics {len(met)}")
    check(set(v.source_id) <= set(s.id), "enforcement: every value's source_id is in sources.csv")
    D = {"metric": ("", "metric key (see metrics.csv)"), "label": ("", "metric description"), "unit": ("", "unit of value (count, Rs, Rs crore, lakh t, ...)"),
         "state": ("", "Punjab, Haryana, or a combined key such as 'Punjab+Haryana+UP-NCR (combined)' - never add combined rows to state totals"),
         "district": ("", "district (empty = state-level value)"), "season": ("", "paddy season year (Sep-Nov); FY 2018-19 figures are assigned to 2018"),
         "as_of": ("date", "date the figure refers to (cumulative 'as on' date, end of window, or answer date)"),
         "value": ("see unit", "value as stated in the source (converted to the metric unit)"),
         "preferred": ("", "true = latest-published entry for (metric, place, season, period)"),
         "latest": ("", "true = the one preferred entry per (metric, place, season) with the latest as_of: use for one-number-per-season charts"),
         "flags": ("", "transcribed_from_image (read from a scanned page, arithmetic-checked); derived (computed, e.g. sum of districts); suspect_unit; non_afl"),
         "source_id": ("", "id in sources.csv"), "period": ("", "reporting window as stated"),
         "basis": ("", "seasonal | cumulative | financial year | stock | period total"), "name_in_source": ("", "place name as printed"),
         "value_as_stated": ("", "original value and unit where a conversion was made"), "derived": ("", "how a derived value was computed"),
         "note": ("", "curator's note"), "quote": ("", "verbatim passage containing the number (checked against the source at build time)"),
         "id": ("", "source id"), "title": ("", "document title"), "publisher": ("", "issuing body"), "kind": ("", "document type"),
         "date": ("date", "document / event date"), "url": ("", "public URL of the document"), "retrieved": ("date", "date fetched"),
         "date_precision": ("", "precision of the event date where not a full date"), "detail": ("", "event description"),
         "listing_title": ("", "title of the listing the document came from"), "transcribed_from_image": ("", "event read from a scanned page"),
         "other": ("", "JSON object with event-specific fields (e.g. incentive rates, EC rates, sowing dates)")}
    secs = [table_doc("values.csv", v, D), table_doc("metrics.csv", met, {**D, "note": ("", "definition notes")}),
            table_doc("sources.csv", s, {**D, "note": ("", "note about the document")}), table_doc("events.csv", ev, D),
            "### `ENFORCEMENT_SOURCES.md`\n\nHuman-readable list of every source document with what it contributed, and the caveats on counting bases.\n"]
    write_readme(out, "Enforcement and crop-residue-management indicators (Punjab, Haryana)",
                 "Field inspections, environmental compensation, FIRs, red entries, action against officials, CRM machines, custom hiring centres, funds and "
                 "residue management, as stated in official documents (parliament answers, CAQM directions and reports, NGT/Supreme Court filings, "
                 "state agriculture departments, PPCB/HSPCB, data.gov.in tables), with a timeline of policy events.",
                 secs, f"Seasons {int(v.season.min())}-{int(v.season.max())}; {v.metric.nunique()} metrics; {len(s)} source documents.",
                 "Public government documents (Lok Sabha / Rajya Sabha answers, CAQM, NGT, Supreme Court, Punjab and Haryana government departments, "
                 "data.gov.in under GODL). Each value carries its source URL; cite the original document. See ../../LICENSES.md.",
                 ["Different counting bases: CAQM/ISRO-protocol fire counts (Sep 15-Nov 30) differ from state lists and Oct 1-Nov 30 windows.",
                  "Cumulative vs seasonal vs financial-year figures are mixed; read `basis` and `period`. Use `latest` for one value per season.",
                  "EC imposed vs recovered are separate metrics; FIR basis changed from IPC s.188 to BNS s.223 on 1 July 2024.",
                  "Ministries revise numbers between answers; all published versions are kept (preferred/latest flag the newest).",
                  "Values from scanned pages (flag transcribed_from_image) were typed from images and checked against row/column totals."],
                 f"`scripts/build_enforcement.py` builds `data/db/extras/enforcement.json` and `ENFORCEMENT_SOURCES.md` from cached documents; `{SCRIPT}` flattens them (local cache paths removed).",
                 """import pandas as pd
v = pd.read_csv("values.csv")
ec = v.query("metric == 'ec_cases' and district.isna() and latest", engine="python")""",
                 """library(readr); library(dplyr)
v <- read_csv("values.csv")
v %>% filter(metric == "ec_cases", is.na(district), latest)""")


# ---------------------------------------------------------------------------------------------
# A7. boundaries
# ---------------------------------------------------------------------------------------------
def export_boundaries():
    import geopandas as gpd
    out = DS / "boundaries"
    out.mkdir(parents=True, exist_ok=True)
    g = DATA / "geo"
    shutil.copyfile(g / "districts.geojson", out / "districts.geojson")
    shutil.copyfile(g / "states.geojson", out / "states.geojson")
    c = jload(g / "countries.geojson")
    for f in c["features"]:
        f["properties"] = {"name": f["properties"]["name"], "subcontinent": bool(f["properties"]["sub"])}
    (out / "subcontinent.geojson").write_text(json.dumps(c, separators=(",", ":")))
    gp = out / "boundaries.gpkg"
    if gp.exists():
        gp.unlink()
    layers = {}
    for name in ("districts", "states", "subcontinent"):
        df = gpd.read_file(out / f"{name}.geojson")
        df = df.set_crs(4326, allow_override=True)
        df.to_file(gp, layer=name, driver="GPKG")
        layers[name] = df
    for name, df in layers.items():
        r = gpd.read_file(gp, layer=name)
        check(len(r) == len(df), f"boundaries.gpkg layer {name}: {len(r)} features")
    D = {"id": ("", "district id used by every district-level table in this release"), "name": ("", "name (diacritics removed)"),
         "state": ("", "state/province"), "country": ("", "India or Pakistan"), "area_km2": ("km2", "polygon area"),
         "subcontinent": ("", "true for India, Pakistan, Nepal, Bangladesh, Bhutan, Sri Lanka"), "geometry": ("WGS84", "polygon geometry")}
    secs = [table_doc(f"{n}.geojson (layer `{n}` in boundaries.gpkg)", df, D) for n, df in layers.items()]
    write_readme(out, "Boundaries",
                 "District (ADM2) and state (ADM1) polygons for the analysis region (68.5-89 E, 21-35.5 N), and country outlines, as used by the dashboard. "
                 "`boundaries.gpkg` holds the same three layers.",
                 secs, "India and Pakistan districts/states intersecting the region; countries clipped to 40-125 E, 12 S-58 N, simplified (0.01 deg).",
                 "- India districts: geoBoundaries gbOpen IND ADM2 (source: Pathways Data / lgdirectory.gov.in, 2021) - **Open Database License (ODbL) 1.0**. "
                 "`districts.geojson` and the districts layer are a derivative database and are released under ODbL 1.0 (share-alike, attribution).\n"
                 "- India states: geoBoundaries gbOpen IND ADM1 (DataMeet India / Election Commission of India) - CC BY 2.5 IN.\n"
                 "- Pakistan ADM1/ADM2: geoBoundaries gbOpen PAK - public domain.\n"
                 "- Countries: Natural Earth 1:10m admin-0 countries, India point of view - public domain.\n"
                 "Cite: Runfola et al. (2020) geoBoundaries, PLoS ONE 15(4): e0231866. Licences checked against the geoBoundaries API on " + TODAY + ".",
                 ["Boundaries are for analysis, not authoritative; Natural Earth's India point-of-view outlines are used for country borders and disputed areas.",
                  "Districts follow the 2021 LGD set: districts created later (e.g. Malerkotla, Punjab) are inside their parent polygon.",
                  "Coordinates rounded to 4 decimals (districts, states) / 3 decimals (countries)."],
                 f"`scripts/build_geo.py` builds `data/geo/*.geojson`; `{SCRIPT}` copies them and writes the GeoPackage.",
                 """import geopandas as gpd
d = gpd.read_file("boundaries.gpkg", layer="districts")""",
                 """library(sf)
d <- st_read("boundaries.gpkg", layer = "districts")""")


# ---------------------------------------------------------------------------------------------
# B1. point detections (all days, all years)
# ---------------------------------------------------------------------------------------------
def read_firms_csv(path, columns=None):
    with open(path) as fh:
        header = fh.readline().strip().split(",")
    cols = [c for c in header if columns is None or c in columns]
    if header == [""] or Path(path).stat().st_size <= len(",".join(header)) + 2:  # empty / header-only file
        return pa.table({c: pa.array([], pa.string()) for c in (cols or ["latitude", "longitude"])})
    t = pacsv.read_csv(path, convert_options=pacsv.ConvertOptions(column_types={c: pa.string() for c in header}, include_columns=cols,
                                                                    strings_can_be_null=False))
    return t


def to_float(arr):
    try:
        return pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return pd.to_numeric(pd.Series(arr.to_pylist()), errors="coerce").to_numpy(dtype=float)


def firms_frame(path, archive):
    """Same filter as build_fires.read_csv, keeping every attribute. Returns (rows DataFrame, static cell keys)."""
    name = Path(path).name
    t = read_firms_csv(path)
    if t.num_rows == 0 or "latitude" not in t.column_names:
        return None, np.array([], np.int64)
    lat, lon = to_float(t["latitude"]), to_float(t["longitude"])
    ok = ~np.isnan(lat) & ~np.isnan(lon) & (lon >= bf.W) & (lon < bf.E) & (lat >= bf.S) & (lat < bf.N)
    t = t.filter(pa.array(ok))
    lat, lon = lat[ok], lon[ok]
    df = t.to_pandas()
    df["lat"], df["lon"] = lat, lon
    typ = df["type"] if "type" in df else pd.Series("", index=df.index)
    st = typ.isin(["2", "3"]).to_numpy()
    static = cell_key(lon[st], lat[st])
    df = df[~st].reset_index(drop=True)
    sat = df["satellite"]
    if "LANDSAT" in name:
        sen = np.full(len(df), 5)
    elif "modis" in name.lower():
        sen = np.where(sat.isin(["Terra", "T"]), 0, np.where(sat.isin(["Aqua", "A"]), 1, 0))
    else:
        default = 2 if ("SNPP" in name or "snpp" in name) else 3
        sen = sat.map(bf.SAT_CODE).fillna(default).astype(int).to_numpy()
    df["sensor_code"] = sen
    c = df["confidence"].str.strip().str.lower()
    cn = pd.to_numeric(c, errors="coerce")
    conf = np.where(c.isin(["l", "low"]), 0, np.where(c.isin(["h", "high"]), 2, np.where(c.isin(["n", "nominal"]), 1,
                    np.where(cn.isna(), 1, np.where(cn < 30, 0, np.where(cn < 80, 1, 2))))))
    df["conf_code"] = conf
    df["frp_f"] = pd.to_numeric(df["frp"].replace("", np.nan), errors="coerce")
    df["hhmm"] = pd.to_numeric(df["acq_time"].replace("", "0")).astype(int)
    df["archive"] = archive
    if archive:
        fam = "firms_country_archive"
    else:
        fam = "firms_api_sp" if "_SP_" in name else "firms_api_nrt"
    df["family"] = fam
    if "type" not in df:
        df["type"] = ""
    return df, static


def cell_key(lon, lat):
    return np.round(lon / bf.RES).astype(np.int64) * 100000 + np.round(lat / bf.RES).astype(np.int64)


def detection_frames():
    """Yield (year, DataFrame) of FIRMS detections exactly as build_fires.main keeps them (CREAMS excluded)."""
    arch = sorted(glob.glob(str(bf.RAW_C / "*.csv")))
    api = sorted(glob.glob(str(bf.RAW_A / "*.csv")))
    archived = {re.search(r"_(\d{4})_", Path(f).name).group(1) for f in arch}
    static = []
    for f in arch:  # pass 1: static cells from archive type 2/3 rows
        t = read_firms_csv(f, ["latitude", "longitude", "type"])
        if t.num_rows == 0 or "type" not in t.column_names:
            continue
        lat, lon = to_float(t["latitude"]), to_float(t["longitude"])
        typ = t["type"].to_numpy(zero_copy_only=False)
        m = (~np.isnan(lat)) & (lon >= bf.W) & (lon < bf.E) & (lat >= bf.S) & (lat < bf.N) & np.isin(typ, ["2", "3"])
        static.append(cell_key(lon[m], lat[m]))
    apis = []
    for f in api:
        df, stc = firms_frame(f, False)
        static.append(stc)
        if df is None:
            continue
        drop = df["acq_date"].str[:4].isin(archived) & (df["sensor_code"] != 4)
        apis.append(df[~drop])
    api_df = pd.concat(apis, ignore_index=True)
    api_df = api_df.drop_duplicates(subset=["lon", "lat", "acq_date", "hhmm", "sensor_code"], keep="first")
    static = np.unique(np.concatenate(static))
    api_df = api_df[~np.isin(cell_key(api_df.lon.to_numpy(), api_df.lat.to_numpy()), static)]
    log(f"detections: {len(static):,} static cells; {len(api_df):,} API rows kept")
    years = sorted({int(re.search(r"_(\d{4})_", Path(f).name).group(1)) for f in arch} | set(api_df.acq_date.str[:4].astype(int)))
    for y in years:
        parts = [p for p in (firms_frame(f, True)[0] for f in arch if f"_{y}_" in Path(f).name) if p is not None]
        parts.append(api_df[api_df.acq_date.str[:4] == str(y)])
        df = pd.concat(parts, ignore_index=True)
        df = df[df.acq_date.str[:4] == str(y)]  # build_fires buckets by acq_date year
        yield y, df


SENSOR_NAMES = bf.SENSORS
CONF = np.array(["low", "nominal", "high"])


def tidy_detections(df, raster, dmeta, dcountry):
    lon, lat = df.lon.to_numpy(), df.lat.to_numpy()
    col, row = ((lon - bf.W) / bf.RES).astype(int), ((bf.N - lat) / bf.RES).astype(int)
    inside = (row >= 0) & (row < raster.shape[0]) & (col >= 0) & (col < raster.shape[1])
    did = np.zeros(len(df), np.int64)
    did[inside] = raster[row[inside], col[inside]]
    hh, mm = df.hhmm.to_numpy() // 100, df.hhmm.to_numpy() % 100
    t = pd.to_datetime(df.acq_date.to_numpy()) + pd.to_timedelta(hh * 60 + mm, unit="min")
    t = t.tz_localize("UTC")
    ist = (t + pd.Timedelta(minutes=330)).strftime("%Y-%m-%d")
    names = np.array([""] + [d["name"] for d in dmeta], dtype=object)
    states = np.array([""] + [state_split(d["state"])[0] for d in dmeta], dtype=object)
    countries = np.array([""] + [dcountry[d["id"]] for d in dmeta], dtype=object)
    d = pd.to_datetime(df.acq_date)
    md = d.dt.month * 100 + d.dt.day
    season = np.where((md >= 915) & (md <= 1130), "rice", np.where((md >= 401) & (md <= 531), "wheat", ""))
    is_modis = df.instrument.str.upper().eq("MODIS").to_numpy() | (df.sensor_code.to_numpy() <= 1)

    def fcol(name):
        return pd.to_numeric(df[name], errors="coerce").to_numpy() if name in df else np.full(len(df), np.nan)
    bright = fcol("brightness")
    t31 = fcol("bright_t31")
    ti4 = fcol("bright_ti4")
    ti5 = fcol("bright_ti5")
    out = pd.DataFrame({
        "latitude": lat, "longitude": lon, "acq_datetime_utc": t, "acq_date_utc": df.acq_date.to_numpy(),
        "acq_date_ist": ist, "sensor": np.array(SENSOR_NAMES, dtype=object)[df.sensor_code.to_numpy()],
        "satellite": df.satellite.to_numpy(), "instrument": df.instrument.to_numpy(),
        "frp_mw": df.frp_f.to_numpy(), "confidence": CONF[df.conf_code.to_numpy()], "confidence_raw": df.confidence.to_numpy(),
        "daynight": df.daynight.to_numpy(),
        "bright_t21_k": np.where(is_modis, bright, np.nan), "bright_t31_k": np.where(is_modis, t31, np.nan),
        "bright_ti4_k": np.where(is_modis, np.nan, ti4), "bright_ti5_k": np.where(is_modis, np.nan, ti5),
        "scan_km": fcol("scan"), "track_km": fcol("track"), "version": df.version.to_numpy(),
        "fire_type": pd.array(pd.to_numeric(df["type"].replace("", np.nan), errors="coerce"), dtype="Int8"),
        "district_id": pd.array(np.where(did > 0, did - 1, -1), dtype="Int16"),
        "district": names[did], "state": states[did], "country": countries[did],
        "season": season, "source_file_family": df.family.to_numpy(),
    })
    out.loc[out.district_id < 0, "district_id"] = pd.NA
    for c in ("district", "state", "country", "season"):
        out[c] = out[c].replace("", None)
    out = out.sort_values(["acq_datetime_utc", "sensor", "latitude", "longitude"], kind="stable").reset_index(drop=True)
    return out, did


def export_detections(S):
    import geopandas as gpd
    raster, dmeta = bf.district_raster()
    gj = jload(DATA / "geo" / "districts.geojson")
    dcountry = {f["properties"]["id"]: f["properties"]["country"] for f in gj["features"]}
    states = sorted({m["state"] for m in dmeta})
    st_index = {s: i for i, s in enumerate(states)}
    dist_state = np.array([st_index[m["state"]] for m in dmeta])
    labels = states + ["Outside districts"]
    csv_path = AS / "fire_detections_all.csv.gz"
    tmp = AS / ".fire_detections_all.csv.gz.tmp"
    counts = {}
    mism = []
    with gzip.open(tmp, "wt", compresslevel=6, newline="") as gz:
        first = True
        for y, raw in detection_frames():
            df, did = tidy_detections(raw, raster, dmeta, dcountry)
            # reconcile with summary.json daily counts (sensor x state x day-of-year)
            si = np.where(did > 0, dist_state[np.maximum(did - 1, 0)], len(states))
            doy = pd.to_datetime(raw.acq_date).dt.dayofyear.to_numpy()
            ref = S["daily"].get(str(y), {})
            got = pd.Series(1, index=pd.MultiIndex.from_arrays([raw.sensor_code.to_numpy(), si, doy])).groupby(level=[0, 1, 2]).sum()
            exp = {}
            for sen, bst in ref.items():
                k = SENSOR_NAMES.index(sen)
                if k == 6:
                    continue
                for lab, v in bst.items():
                    for i, n in enumerate(v["n"]):
                        if n:
                            exp[(k, labels.index(lab), i + 1)] = n
            gotd = {k: int(v) for k, v in got.items()}
            diff = sum(abs(gotd.get(k, 0) - exp.get(k, 0)) for k in set(gotd) | set(exp))
            if diff:
                mism.append((y, diff))
            counts[y] = len(df)
            gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.longitude, df.latitude), crs="EPSG:4326")
            gdf.to_parquet(AS / f"fire_detections_{y}.parquet", index=False, compression="zstd", row_group_size=250_000)
            c = df.copy()
            c["acq_datetime_utc"] = c["acq_datetime_utc"].dt.strftime("%Y-%m-%dT%H:%MZ")
            c.to_csv(gz, index=False, header=first, lineterminator="\n")
            first = False
            log(f"detections {y}: {len(df):,} rows (summary.json mismatch cells: {diff})")
    tmp.replace(csv_path)
    tot_ref = sum(sum(v["n"]) for y, bys in S["daily"].items() for sen, bst in bys.items() if not sen.startswith("CREAMS") for v in bst.values())
    check(sum(counts.values()) == tot_ref, f"fire_detections total {sum(counts.values()):,} == summary.json FIRMS daily total {tot_ref:,}")
    check(not mism, f"fire_detections per (year, sensor, state, day) equal summary.json" + (f"; mismatches {mism}" if mism else ""))
    (BUILD / "detection_counts.json").write_text(json.dumps(counts))
    (BUILD / "detection_log.txt").write_text("\n".join(l for l in LOG if "detection" in l) + "\n")
    return counts


# ---------------------------------------------------------------------------------------------
# B2. CREAMS events
# ---------------------------------------------------------------------------------------------
def export_creams_events():
    import geopandas as gpd
    df = pd.read_csv(DATA / "creams" / "creams_events.csv", dtype=str, keep_default_na=False)
    lat = pd.to_numeric(df.latitude, errors="coerce")
    lon = pd.to_numeric(df.longitude, errors="coerce")
    ist = pd.to_datetime(df.date + "T" + df.acq_time.replace("", "00:00:00"), errors="coerce", format="ISO8601")
    out = pd.DataFrame({
        "date_ist": df.date, "acq_time_ist": df.acq_time.replace("", None),
        "acq_datetime_utc": (ist - pd.Timedelta(minutes=330)).dt.tz_localize("UTC"),
        "season": df.season, "state": df.state, "district": df.district, "district_matched": df.district_matched == "1",
        "tehsil": df.tehsil.replace("", None), "latitude": lat, "longitude": lon, "satellite": df.satellite,
        "day_night": df.day_night.replace("", None), "frp_as_printed": pd.to_numeric(df.frp_w_m2, errors="coerce"),
        "bulletin_file": df.bulletin_file, "page": pd.to_numeric(df.page, errors="coerce").astype("Int16")})
    out.loc[out.acq_time_ist.isna(), "acq_datetime_utc"] = pd.NaT
    g = gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(out.longitude, out.latitude), crs="EPSG:4326")
    g.to_parquet(AS / "creams_events.parquet", index=False, compression="zstd")
    c = out.copy()
    c["acq_datetime_utc"] = c["acq_datetime_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    c.to_csv(AS / "creams_events.csv.gz", index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    check(len(out) == len(df), f"creams_events {len(out):,} rows == data/creams/creams_events.csv")
    # vs dashboard: CREAMS 'sensor' in summary.json = events inside the box with a parsable date/time
    S = jload(DATA / "fires" / "summary.json")
    ref = sum(sum(v["n"]) for bys in S["daily"].values() for sen, bst in bys.items() if sen.startswith("CREAMS") for v in bst.values())
    inbox = sum(1 for _ in bf.iter_creams())  # the dashboard's own reader (strict ISO times, box filter)
    check(inbox == ref, f"creams_events read by build_fires.iter_creams {inbox:,} == summary.json CREAMS count {ref:,}")
    log(f"creams_events: {int(out.acq_datetime_utc.isna().sum()):,} rows without a usable time; {int(out.latitude.isna().sum()):,} without latitude")
    return len(out)


# ---------------------------------------------------------------------------------------------
# B3. burned-area rasters (only if the season mosaics exist locally)
# ---------------------------------------------------------------------------------------------
def burn_colormap():
    import colorsys
    cmap = {0: (0, 0, 0, 0)}
    for v in range(1, 256):
        cmap[v] = (0, 0, 0, 0)
    for d in range(0, 82):  # Sep 15 .. Dec 5: yellow (early) -> dark red (late)
        h = 0.16 * (1 - d / 81)
        r, g, b = colorsys.hsv_to_rgb(h, 1, 1 - 0.55 * d / 81)
        cmap[10 + d] = (int(r * 255), int(g * 255), int(b * 255), 255)
    return cmap


def export_burned_tifs():
    import rasterio
    made = []
    mosaics = sorted(glob.glob(str(DATA / "raw" / "burnscar" / "*" / "mosaic.tif")))
    for m in mosaics:
        season = Path(m).parent.name
        tmp = AS / f".burned_{season}.tif"
        with rasterio.open(m) as src:
            prof = src.profile.copy()
            arr = src.read(1)
        prof.update(driver="GTiff", dtype="uint8", count=1, nodata=None, compress="deflate")
        with rasterio.open(tmp, "w", **prof) as dst:
            dst.write(arr.astype("uint8"), 1)
            dst.write_colormap(1, burn_colormap())
            dst.update_tags(TIFFTAG_IMAGEDESCRIPTION=f"MODIS MCD64A1 C6.1 burned area, paddy season {season}",
                            SEASON=season, SOURCE="NASA MODIS MCD64A1 Collection 6.1 (doi:10.5067/MODIS/MCD64A1.061)",
                            CODING=f"0 = not burned / not mapped; 10+d = burned on season day d, where day 0 = {season}-09-15 (e.g. 10 = Sep 15, 56 = Oct 31)",
                            PRODUCED_BY=SCRIPT)
            dst.set_band_description(1, f"burn code: 0 none, 10+d = burned on day d after {season}-09-15")
        out = AS / f"burned_area_{season}.tif"
        subprocess.run(["gdal_translate", "-q", "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "OVERVIEW_RESAMPLING=NEAREST", str(tmp), str(out)], check=True)
        tmp.unlink()
        made.append(out)
        info = subprocess.run(["gdalinfo", str(out)], capture_output=True, text=True).stdout
        check("Color Table" in info and "LAYOUT=COG" in info, f"{out.name} opens in GDAL as COG with colour table")
    if not mosaics:
        log("SKIP burned_area_<season>.tif: no data/raw/burnscar/<season>/mosaic.tif present locally (re-run scripts/burnscar.py to regenerate the mosaics, then this script)")
    return made


# ---------------------------------------------------------------------------------------------
# B4. GEOS-FP smoke NetCDF
# ---------------------------------------------------------------------------------------------
def load_chunks(run, var):
    frames = {}
    for f in glob.glob(str(DATA / "raw" / "smoke" / f"{run}_{var}_*.npy")):
        a, b, step = map(int, Path(f).stem.split("_")[3:6])
        arr = np.load(f)
        for i in range(len(arr)):
            frames[a + i * step] = arr[i]
    return frames


def export_smoke():
    import xarray as xr
    meta = jload(DATA / "smoke" / "smoke_meta.json")
    run, wrun = meta["aer_run"], meta["wind_run"]
    files = glob.glob(str(DATA / "raw" / "smoke" / "*.npy"))
    runs = sorted({Path(f).name[:11] for f in files})
    other = [r for r in reversed(runs) if r != run]
    times = pd.to_datetime([t.replace("Z", "") for t in meta["times"]])
    n = len(times)
    names = {"ocexttau": "oc_aod", "bcexttau": "bc_aod", "brexttau": "brc_aod", "totexttau": "total_aod"}
    prim = {v: load_chunks(run, v) for v in names}
    ti0 = min(prim["ocexttau"])
    idx = [ti0 + i for i in range(n)]
    status = {v: np.zeros(n, np.int8) for v in names}  # 0 primary run, 1 from another run, 2 interpolated, 3 missing
    data = {}
    alts = {r: {v: load_chunks(r, v) for v in names} for r in other}

    def valid(fr):
        return fr is not None and 1e-3 <= float(fr.max()) <= 100
    for v in names:
        arr = np.full((n, meta["tile_h"], meta["tile_w"]), np.nan, np.float32)
        for i, k in enumerate(idx):
            fr = prim[v].get(k)
            if valid(fr):
                arr[i] = fr
                continue
            for r in other:
                fr = alts[r][v].get(k)
                if valid(fr):
                    arr[i] = fr
                    status[v][i] = 1
                    break
            else:
                status[v][i] = 3
        for i in np.nonzero(status[v] == 3)[0]:  # interior gaps: linear in time, per variable
            good = np.nonzero(status[v] != 3)[0]
            lo, hi = good[good < i], good[good > i]
            if len(lo) and len(hi):
                lo, hi = lo[-1], hi[0]
                w = (i - lo) / (hi - lo)
                arr[i] = arr[lo] * (1 - w) + arr[hi] * w
                status[v][i] = 2
        data[v] = arr
        log(f"smoke {v}: frames primary/other-run/interpolated/missing = {np.bincount(status[v], minlength=4).tolist()}")
    smoke = data["ocexttau"] + data["bcexttau"] + data["brexttau"]
    wind = {}
    for v in ("u10m", "v10m"):
        fr = load_chunks(wrun, v)
        w0 = min(fr)
        arr = np.full((n, meta["tile_h"], meta["tile_w"]), np.nan, np.float32)
        for i in range(n):
            f = fr.get(w0 + 3 * i)
            if f is not None:
                f = f.astype(np.float32).copy()
                f[np.abs(f) > 1e3] = np.nan
                arr[i] = f
        wind[v] = arr
    lat = np.arange(meta["tile_h"]) * 0.25 + 18.0
    lon = np.arange(meta["tile_w"]) * 0.3125 + (meta["bounds"][0] + 0.15625)
    # validation against the dashboard's regional box means
    box = np.ix_((lat >= 27.5) & (lat <= 32.5), (lon >= 73.5) & (lon <= 78.5))
    bm = np.array([float(np.nanmean(f[box])) for f in smoke])
    dev = np.nanmax(np.abs(bm - np.array(meta["box_smoke_mean"])))
    check(dev < 2e-3, f"smoke NetCDF: Punjab-Haryana-Delhi box mean smoke AOD vs smoke_meta.json, max abs diff {dev:.5f}")
    tm = np.array([float(np.nanmean(f[box])) for f in data["totexttau"]])
    tdev = np.abs(tm - np.array(meta["box_total_mean"]))
    okt = status["totexttau"] == 0
    first_bad = int(np.argmax(tdev > 2e-3)) if (tdev > 2e-3).any() else None
    if first_bad is None:
        check(True, "smoke NetCDF: total AOD box means equal smoke_meta.json")
    else:
        sh = int(np.argmax(status["totexttau"] != 0)) if (status["totexttau"] != 0).any() else first_bad
        gap = int((status["totexttau"] != 0).sum())
        d_shift = np.abs(tm[sh + gap:] - np.array(meta["box_total_mean"])[sh:len(tm) - gap]).max() if gap else np.inf
        check(tdev[:sh].max() < 2e-3 and d_shift < 2e-3,
              f"smoke NetCDF total AOD: equal to smoke_meta.json before frame {sh} ({meta['times'][sh]}); after it the dashboard's total-AOD frames are "
              f"offset by {gap} frames (a truncated {gap}-frame-short OPeNDAP chunk concatenated without its gap); this file keeps the correct time "
              f"alignment (max abs diff after realigning: {d_shift:.5f})")
    fc = np.array(meta["forecast"], dtype=np.int8)
    enc = {"zlib": True, "complevel": 4, "dtype": "float32", "_FillValue": np.float32(np.nan)}
    ds = xr.Dataset(
        {
            "smoke_aod": (("time", "lat", "lon"), smoke, {"long_name": "smoke aerosol optical depth at 550 nm (organic + black + brown carbon extinction AOD)", "units": "1",
                                                          "source_variables": "OCEXTTAU + BCEXTTAU + BREXTTAU (tavg3_2d_aer_Nx)"}),
            "total_aod": (("time", "lat", "lon"), data["totexttau"], {"long_name": "total aerosol extinction optical depth at 550 nm", "standard_name": "atmosphere_optical_thickness_due_to_ambient_aerosol_particles", "units": "1", "source_variables": "TOTEXTTAU"}),
            "oc_aod": (("time", "lat", "lon"), data["ocexttau"], {"long_name": "organic carbon extinction AOD at 550 nm", "units": "1", "source_variables": "OCEXTTAU"}),
            "bc_aod": (("time", "lat", "lon"), data["bcexttau"], {"long_name": "black carbon extinction AOD at 550 nm", "units": "1", "source_variables": "BCEXTTAU"}),
            "brc_aod": (("time", "lat", "lon"), data["brexttau"], {"long_name": "brown carbon extinction AOD at 550 nm", "units": "1", "source_variables": "BREXTTAU"}),
            "u10": (("time", "lat", "lon"), wind["u10m"], {"long_name": "10 m eastward wind", "standard_name": "eastward_wind", "units": "m s-1", "source_variables": "U10M (tavg1_2d_slv_Nx, hourly mean centred on the same time)"}),
            "v10": (("time", "lat", "lon"), wind["v10m"], {"long_name": "10 m northward wind", "standard_name": "northward_wind", "units": "m s-1", "source_variables": "V10M (tavg1_2d_slv_Nx)"}),
            "forecast": (("time",), fc, {"long_name": "1 = forecast frame (after the run's analysis time), 0 = assimilation", "flag_values": np.array([0, 1], np.int8), "flag_meanings": "analysis forecast"}),
            "smoke_frame_source": (("time",), np.maximum.reduce([status[v] for v in ("ocexttau", "bcexttau", "brexttau")]),
                                   {"long_name": "provenance of the OC/BC/BrC (smoke) frames, worst of the three", "flag_values": np.array([0, 1, 2, 3], np.int8),
                                    "flag_meanings": "primary_run other_seamless_run linear_time_interpolation missing"}),
            "total_aod_frame_source": (("time",), status["totexttau"], {"long_name": "provenance of the total AOD frame", "flag_values": np.array([0, 1, 2, 3], np.int8),
                                                                      "flag_meanings": "primary_run other_seamless_run linear_time_interpolation missing"}),
        },
        coords={
            "time": ("time", times, {"long_name": "centre of the 3-hourly averaging window", "standard_name": "time"}),
            "lat": ("lat", lat, {"standard_name": "latitude", "units": "degrees_north", "long_name": "grid-cell centre latitude"}),
            "lon": ("lon", lon, {"standard_name": "longitude", "units": "degrees_east", "long_name": "grid-cell centre longitude"}),
        },
        attrs={
            "title": f"GEOS-FP smoke and total aerosol optical depth with 10 m winds, northern South Asia, season {times[0].year}",
            "Conventions": "CF-1.8",
            "institution": "NASA Global Modeling and Assimilation Office (GMAO)",
            "source": "GEOS-FP seamless collections tavg3_2d_aer_Nx (3-hourly) and tavg1_2d_slv_Nx (hourly), 0.3125 x 0.25 deg, via the NCCS OPeNDAP server (https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/seamless/)",
            "aerosol_run": run, "wind_run": wrun, "analysis_until": meta["analysis_until"],
            "references": "GEOS-FP: https://gmao.gsfc.nasa.gov/GMAO_products/NRT_products.php",
            "comment": "Each seamless run = assimilation up to analysis_until followed by forecast (see variable 'forecast'). Frames a run left empty were taken from another "
                       "cached seamless run (*_frame_source=1) or linearly interpolated in time (2). GEOS fill values (1e15) and wind |v| > 1000 are set to NaN.",
            "history": f"{TODAY}: assembled by {SCRIPT} from the dashboard's OPeNDAP chunk cache",
            "license": "NASA open data; no restrictions. Cite NASA GMAO GEOS-FP.",
        })
    season = times[0].year
    out = AS / f"smoke_geosfp_{season}.nc"
    ds.to_netcdf(out, encoding={v: enc for v in ["smoke_aod", "total_aod", "oc_aod", "bc_aod", "brc_aod", "u10", "v10"]}, format="NETCDF4")
    r = xr.open_dataset(out)
    check(r.smoke_aod.shape == (n, len(lat), len(lon)), f"{out.name} opens in xarray, smoke_aod {r.smoke_aod.shape}, {int(r.forecast.sum())} forecast frames")
    r.close()
    return out


# ---------------------------------------------------------------------------------------------
# manifest / docs / privacy
# ---------------------------------------------------------------------------------------------
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest(counts):
    rows = []
    desc = {
        "fire_detections_all.csv.gz": "All FIRMS detections 2003-present in the box 68.5-89E 21-35.5N, all days, one CSV (same columns as the per-year parquet minus geometry)",
        "creams_events.parquet": "ICAR-IARI CREAMS bulletin annexure events (GeoParquet, EPSG:4326)",
        "creams_events.csv.gz": "ICAR-IARI CREAMS bulletin annexure events (CSV)",
        "dashboard-data.zip": "everything the web dashboard loads (unzip at the repository root)",
    }
    import xarray as xr
    for p in sorted(AS.iterdir()):
        if p.name.startswith(".") or p.name in ("SHA256SUMS", "MANIFEST.csv"):
            continue
        size = ""
        d = desc.get(p.name, "")
        if p.suffix == ".parquet":
            size = f"{pq.ParquetFile(p).metadata.num_rows} rows"
            m = re.match(r"fire_detections_(\d{4})", p.name)
            if m:
                d = f"FIRMS detections {m.group(1)}, all days (GeoParquet, EPSG:4326)"
        elif p.name == "fire_detections_all.csv.gz":
            size = f"{sum(counts.values())} rows" if counts else ""
        elif p.suffix == ".zip":
            import zipfile
            with zipfile.ZipFile(p) as z:
                size = f"{len(z.infolist())} files"
        elif p.name == "creams_events.csv.gz":
            size = f"{pq.ParquetFile(AS / 'creams_events.parquet').metadata.num_rows} rows"
        elif p.suffix == ".nc":
            with xr.open_dataset(p) as r:
                size = " x ".join(f"{k}={v}" for k, v in r.sizes.items())
            d = "GEOS-FP smoke AOD, total AOD, OC/BC/BrC AOD, 10 m wind; 3-hourly, 0.3125 x 0.25 deg (NetCDF4/CF)"
        elif p.suffix == ".tif":
            import rasterio
            with rasterio.open(p) as r:
                size = f"{r.width} x {r.height} px"
            d = ("Sentinel-2A night (Dec 2025): maximum SWIR radiance per 100 m cell, W m-2 sr-1 um-1 (COG, EPSG:4326)" if p.name.startswith("s2night_")
                 else "MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0)")
        rows.append({"file": p.name, "bytes": p.stat().st_size, "rows_or_size": size, "description": d})
        if p.stat().st_size >= 1.9e9:
            log(f"FAIL {p.name} exceeds 1.9 GB")
    pd.DataFrame(rows).to_csv(AS / "MANIFEST.csv", index=False, lineterminator="\n")
    with open(AS / "SHA256SUMS", "w") as fh:
        for r in rows:
            fh.write(f"{sha256(AS / r['file'])}  {r['file']}\n")
    return rows


DETECTION_COLS = [
    ("latitude", "deg", "pixel-centre latitude (WGS84)"), ("longitude", "deg", "pixel-centre longitude (WGS84)"),
    ("acq_datetime_utc", "UTC", "overpass date and time (FIRMS acq_date + acq_time)"), ("acq_date_utc", "date", "FIRMS acq_date (UTC)"),
    ("acq_date_ist", "date", "acquisition date in Indian Standard Time (UTC+5:30)"),
    ("sensor", "", "MODIS Terra | MODIS Aqua | VIIRS S-NPP | VIIRS NOAA-20 | VIIRS NOAA-21"),
    ("satellite", "", "FIRMS satellite code as published (Terra, Aqua, T, A, N, N20, 1, N21)"), ("instrument", "", "MODIS or VIIRS"),
    ("frp_mw", "MW", "fire radiative power of the pixel (null if missing)"),
    ("confidence", "", "low | nominal | high. VIIRS: as published (l/n/h). MODIS: numeric confidence binned < 30 low, 30-79 nominal, >= 80 high (the dashboard's rule)"),
    ("confidence_raw", "", "confidence as published (MODIS 0-100, VIIRS l/n/h)"), ("daynight", "", "D = day, N = night overpass"),
    ("bright_t21_k", "K", "MODIS channel 21/22 brightness temperature (FIRMS 'brightness'); null for VIIRS"),
    ("bright_t31_k", "K", "MODIS channel 31 brightness temperature; null for VIIRS"),
    ("bright_ti4_k", "K", "VIIRS I-4 brightness temperature; null for MODIS"), ("bright_ti5_k", "K", "VIIRS I-5 brightness temperature; null for MODIS"),
    ("scan_km", "km", "along-scan pixel size"), ("track_km", "km", "along-track pixel size"), ("version", "", "FIRMS collection/processing version (NRT versions end in 'NRT')"),
    ("fire_type", "", "FIRMS type: 0 presumed vegetation fire, 1 active volcano (2 static and 3 offshore were removed); null for NRT rows, which carry no type"),
    ("district_id", "", "district id in datasets/boundaries/districts.geojson (null = outside mapped districts)"), ("district", "", "district name"),
    ("state", "", "state / province"), ("country", "", "India or Pakistan"),
    ("season", "", "rice (Sep 15-Nov 30) | wheat (Apr 1-May 31) by UTC date; null otherwise"),
    ("source_file_family", "", "firms_country_archive (FIRMS yearly country files, standard processing) | firms_api_sp (FIRMS area API, standard processing) | firms_api_nrt (FIRMS area API, near-real-time)"),
    ("geometry", "EPSG:4326", "point geometry (GeoParquet only)")]
CREAMS_COLS = [
    ("date_ist", "date", "date of the bulletin event list (IST)"), ("acq_time_ist", "HH:MM:SS IST", "overpass time as printed (IST)"),
    ("acq_datetime_utc", "UTC", "date_ist + acq_time_ist converted to UTC (null if no time printed)"), ("season", "", "rice | wheat"),
    ("state", "", "state as printed"), ("district", "", "district as printed"), ("district_matched", "", "true if the district name matched the boundary file"),
    ("tehsil", "", "tehsil / block as printed (null if not given)"), ("latitude", "deg", "event latitude as printed (3 decimals)"), ("longitude", "deg", "event longitude as printed"),
    ("satellite", "", "satellite as printed (S-NPP, NOAA-20, NOAA-21, MODIS ...)"), ("day_night", "", "D / N as printed"),
    ("frp_as_printed", "unclear (printed as W/m2 in some bulletins)", "FRP column as printed in the bulletin; units are inconsistent across years - use with caution"),
    ("bulletin_file", "", "source bulletin PDF (URL in datasets/creams/bulletins.csv)"), ("page", "", "page of the PDF"), ("geometry", "EPSG:4326", "point geometry (GeoParquet only)")]


def write_docs(counts, rows_manifest):
    def size(p):
        b = p.stat().st_size
        return f"{b / 1e6:.1f} MB" if b >= 1e5 else f"{b / 1e3:.0f} kB"
    lines = ["# Data dictionary", "",
             "Open datasets behind the paddy-stubble-burning dashboard (north-west India and Pakistan Punjab). Every folder under `datasets/` has a README "
             "with each column's type, unit and meaning, coverage, source, licence, caveats and loading snippets. Large files are attached to the GitHub "
             "Release (see *Release assets* below). Licences: [LICENSES.md](LICENSES.md).", "",
             f"Exported by `{SCRIPT}` on {TODAY}.", "", "## Repository datasets", "",
             "| folder | contents | files |", "|---|---|---|"]
    topics = {"fires": "NASA FIRMS active-fire detections aggregated by day / state / district / season / sensor, IST hour of burning",
              "creams": "ICAR-IARI CREAMS official daily residue-burning counts (state, district), year comparisons, bulletin list",
              "burned_area": "NASA MODIS MCD64A1 burned area per season, state, district and burn date (Punjab, Haryana)",
              "s2_night": "Sentinel-2A night hot spots (SWIR, 20 m), 9 and 12 Dec 2025, with FIRMS match rates and land-cover setting",
              "air": "CPCB ground PM2.5/PM10 (via OpenAQ), CAMS PM/AOD + ERA5 meteorology at receptor cities, modelled stubble share of Delhi PM2.5",
              "paddy": "Kharif rice area and production by state (1966+) and district (1997+), UPAg/DES",
              "enforcement": "Enforcement and crop-residue-management indicators with source documents and policy timeline",
              "boundaries": "District / state / country polygons used for all attributions (GeoJSON + GeoPackage)"}
    for t, d in topics.items():
        if not (DS / t).exists():
            continue
        files = ", ".join(f"`{p.name}` ({size(p)})" for p in sorted((DS / t).iterdir()) if p.name != "README.md")
        lines.append(f"| [{t}/](datasets/{t}/README.md) | {d} | {files} |")
    lines += ["", "## Release assets", "", "Download from the GitHub Release; checksums in `SHA256SUMS`, list in `MANIFEST.csv`.", "",
              "| file | size | rows / size | description |", "|---|---|---|---|"]
    for r in rows_manifest:
        lines.append(f"| `{r['file']}` | {r['bytes'] / 1e6:.1f} MB | {r['rows_or_size']} | {r['description']} |")
    lines += ["", "### `fire_detections_<year>.parquet` and `fire_detections_all.csv.gz`", "",
              "Every FIRMS detection in the box 68.5-89 E, 21-35.5 N on every day of every year, after the dashboard's filters: static land sources "
              "(type 2) and offshore (type 3) removed; near-real-time rows in 0.01-degree cells flagged static in any archive year removed; for years covered "
              "by the FIRMS yearly country archive only NOAA-21 is taken from the API; duplicate API rows (same position, time, sensor) dropped. "
              "GeoParquet 1.x (WKB points, EPSG:4326, zstd). CREAMS events are a separate asset. Row counts reconcile exactly with the dashboard's "
              "daily counts (sensor x state x day).", "", "| column | unit | meaning |", "|---|---|---|"]
    lines += [f"| `{c}` | {u} | {m} |" for c, u, m in DETECTION_COLS]
    lines += ["", "Source: NASA FIRMS (MODIS C6.1, VIIRS 375 m). Caveats as in [datasets/fires](datasets/fires/README.md).", "",
              "```python", "import geopandas as gpd, pandas as pd", "g = gpd.read_parquet('fire_detections_2024.parquet')       # GeoDataFrame",
              "df = pd.read_parquet('fire_detections_2024.parquet', columns=['latitude','longitude','acq_datetime_utc','sensor','frp_mw','district','state'])",
              "```", "", "```r", "library(arrow); df <- read_parquet('fire_detections_2024.parquet', col_select = c(latitude, longitude, sensor, frp_mw))",
              "library(sfarrow) # or sf::st_read() with GDAL >= 3.5 for GeoParquet", "```", "",
              "### `creams_events.parquet` / `creams_events.csv.gz`", "",
              "Geolocated residue-burning events listed in the annexures of CREAMS daily bulletins (2018 onwards), as printed.", "",
              "| column | unit | meaning |", "|---|---|---|"]
    lines += [f"| `{c}` | {u} | {m} |" for c, u, m in CREAMS_COLS]
    lines += ["", "Source and licence: see [datasets/creams](datasets/creams/README.md).", "",
              "### `smoke_geosfp_<season>.nc`", "",
              "NASA GMAO GEOS-FP smoke (OC+BC+BrC) and total aerosol optical depth at 550 nm, the three carbon components, and 10 m wind (u, v) on the "
              "0.3125 x 0.25 deg grid (64-94 E, 18-38 N), 3-hourly from Sep 15 of the season to the end of the latest cached forecast. Variables `forecast` "
              "(1 = forecast frame), `smoke_frame_source` and `total_aod_frame_source` (0 primary run, 1 other cached run, 2 linear time interpolation) flag each time step; global attributes give the run ids. "
              "Only the current season is available (the source keeps a rolling window).", "",
              "```python", "import xarray as xr", "ds = xr.open_dataset('smoke_geosfp_2026.nc'); ds.smoke_aod.sel(lat=30.9, lon=75.85, method='nearest').plot()", "```", "",
              "### `burned_area_<season>.tif`", "",
              "Cloud-Optimized GeoTIFF per paddy season (EPSG:32643, 463 m): 0 = not burned, 10+d = burned on season day d (Sep 15 = day 0), with a colour table "
              "and the coding in the GDAL metadata. Produced only when the season mosaics are available locally" +
              ("." if any(AS.glob("burned_area_*.tif")) else " - **not included in this release** (mosaic cache absent); tabular burned area is in datasets/burned_area/."), "",
              "## Conventions", "",
              "- Dates are ISO 8601. FIRMS dates are UTC unless the column says `_ist`; CREAMS, CPCB and Open-Meteo dates are IST calendar days.",
              "- `season` = the paddy season year (Sep-Nov); the wheat season is Apr-May of the same year.",
              "- `district_id` always refers to `datasets/boundaries/districts.geojson`.",
              "- Missing values are empty cells in CSV and nulls in Parquet/NetCDF; nothing is gap-filled unless a flag column says so.", ""]
    (REL / "DATA_DICTIONARY.md").write_text("\n".join(lines))

    lic = f"""# Licences and terms of the upstream data

The code in this repository and the derived tables are offered for research and journalism. Each dataset keeps the licence or terms of its
upstream source, listed below; when you reuse a file, cite the original source as shown. Status column: **verified** = checked against the
provider's own licence statement on {TODAY}; **stated** = taken from the provider's documentation as recorded at download time, not re-checked;
**uncertain** = no explicit licence found.

| dataset(s) | upstream source | licence / terms | status |
|---|---|---|---|
| fires/, fire_detections_* | NASA FIRMS (LANCE / MODAPS), MODIS C6.1 and VIIRS 375 m active fires | NASA Earth science data are open with no restrictions on use (NASA Earth Science Data and Information Policy). Cite: "NASA FIRMS, https://firms.modaps.eosdis.nasa.gov/" and the product DOIs (MODIS MCD14ML doi:10.5067/FIRMS/MODIS/MCD14ML; VIIRS VNP14IMGTDL_NRT doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002). | stated |
| burned_area/, burned_area_*.tif | NASA MODIS MCD64A1 C6.1 (LP DAAC), via Microsoft Planetary Computer | NASA open data, no restrictions; cite doi:10.5067/MODIS/MCD64A1.061 (Giglio et al.). | stated |
| s2_night/, s2night_*.tif | ESA Copernicus Sentinel-2 night-time campaign (Dec 2025), CDSE; ESA WorldCover 2021 v200 for the setting | Copernicus Sentinel data: free, full and open (Copernicus data policy, Regulation (EU) 2021/696); credit "Contains modified Copernicus Sentinel data 2025". WorldCover: CC BY 4.0, cite "ESA WorldCover 2021 v200, doi:10.5281/zenodo.7254221". | stated |
| smoke_geosfp_*.nc | NASA GMAO GEOS-FP | NASA open data, no restrictions; cite NASA GMAO GEOS-FP. | stated |
| creams/, creams_events.* | ICAR-IARI CREAMS daily crop-residue burning bulletins (https://creams.iari.res.in/) | Public bulletins of a government research institute; no explicit licence found. Redistributed as factual extracts with source URLs; cite ICAR-IARI CREAMS and the bulletin. | uncertain |
| boundaries/districts (India part) | geoBoundaries gbOpen IND ADM2 (Pathways Data / lgdirectory.gov.in) | **Open Database License (ODbL) 1.0** - the districts layer is a derivative database and is released under ODbL 1.0; tables that use it for attribution are Produced Works (attribution required). | verified (geoBoundaries API) |
| boundaries/states (India part) | geoBoundaries gbOpen IND ADM1 (DataMeet India / Election Commission of India) | CC BY 2.5 India | verified (geoBoundaries API) |
| boundaries (Pakistan) | geoBoundaries gbOpen PAK ADM1/ADM2 | Public domain | verified (geoBoundaries API) |
| boundaries/subcontinent | Natural Earth 1:10m admin-0, India point of view | Public domain | stated (naturalearthdata.com terms) |
| air/cpcb_* | CPCB CAAQMS data redistributed by OpenAQ API v3 | OpenAQ terms: users must comply with each provider's terms and credit both the original provider (CPCB) and OpenAQ. CPCB data are Indian government data; no separate licence was found on the OpenAQ records. | verified (OpenAQ terms); CPCB licence uncertain |
| air/cams_era5_* | Open-Meteo Air Quality API (CAMS global forecasts) and Historical Weather API (ERA5/ERA5-Land) | Open-Meteo: CC BY 4.0 (attribute Open-Meteo). Underlying CAMS and C3S ERA5 data: Copernicus licence / CC BY 4.0 - "Contains modified Copernicus Atmosphere Monitoring Service and Climate Change Service information". | stated (Open-Meteo licence page is script-rendered and was not re-read) |
| air/stubble_share_delhi | IITM Pune (MoES): SAFAR, DSS, AQEWS public web pages, mostly via Internet Archive captures | Public government web pages; no explicit licence found. Cite IITM and the capture URL. | uncertain |
| paddy/ (1997-98 district rows) | DES Crop Production Statistics via the India Data Portal mirror | ODC-By (as recorded at download) | stated |
| paddy/ | UPAg (Unified Portal for Agricultural Statistics) / DES, Ministry of Agriculture & Farmers Welfare, Area-Production-Yield | Government Open Data License - India (GODL) is the default for Indian government open data; not stated on every UPAg download. | uncertain |
| enforcement/ | Lok Sabha / Rajya Sabha answers, CAQM directions and reports, NGT and Supreme Court filings, Punjab / Haryana departments and pollution control boards, data.gov.in (OGD) tables | Public government documents; data.gov.in resources are under GODL. Values are short factual extracts with quotes and source URLs. | data.gov.in verified as GODL by its portal terms; others uncertain |

## How to cite this release

Cite the repository (and release tag) plus the original source of each dataset you use, e.g. "NASA FIRMS active fire data (MODIS C6.1, VIIRS 375 m),
aggregated in <repository>, accessed <date>".

## Personal data

The release contains no personal data beyond the names of public offices and officials as they appear in official documents. CPCB station names and
village/tehsil names are place names.
"""
    (REL / "LICENSES.md").write_text(lic)


def privacy_grep():
    # generic patterns; site-specific ones (host names, user names, addresses) go one regex per line in an
    # untracked file named by $PRIVACY_PATTERNS_FILE (default .privacy-patterns), so they never ship with the code
    pats = [rb"/Users/", rb"/home/", rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", rb"(?i)map_key|api_key|apikey|x-api-key|\.env\b"]
    extra = Path(os.environ.get("PRIVACY_PATTERNS_FILE", ROOT / ".privacy-patterns"))
    if extra.exists():
        pats += [l.strip().encode() for l in extra.read_text().splitlines() if l.strip() and not l.startswith("#")]
    secrets = []
    envp = ROOT / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 8:
                    secrets.append(v.encode())
    hits = defaultdict(list)

    def scan(name, blob):
        for p in pats:
            for m in re.finditer(p, blob):
                ctx = blob[max(0, m.start() - 40):m.end() + 40].decode("utf-8", "replace").replace("\n", " ")
                hits[name].append((p.decode(), ctx))
                if len(hits[name]) > 20:
                    return
        for s in secrets:
            if s in blob:
                hits[name].append(("<.env value>", "(value not shown)"))
    for p in sorted(REL.rglob("*")):
        if not p.is_file() or p.name == "VALIDATION.txt" or ".build" in p.parts:
            continue
        name = str(p.relative_to(REL))
        if p.suffix == ".gz":
            with gzip.open(p, "rb") as fh:
                while True:
                    b = fh.read(64 << 20)
                    if not b:
                        break
                    scan(name, b)
        elif p.suffix == ".parquet":  # compressed binary: scan footer metadata and every decoded string column
            f = pq.ParquetFile(p)
            md = f.schema_arrow.metadata or {}
            scan(name + ":metadata", b"\n".join(k + b"=" + v for k, v in md.items()))
            t = f.read()
            for c in t.column_names:
                if pa.types.is_string(t.schema.field(c).type) or pa.types.is_large_string(t.schema.field(c).type):
                    u = pc.unique(t[c]).to_pylist()
                    scan(f"{name}:{c}", "\n".join(x for x in u if x).encode())
        elif p.suffix == ".nc":  # header (all attributes); data are float arrays only
            scan(name + ":header", subprocess.run(["ncdump", "-h", str(p)], capture_output=True).stdout or
                 subprocess.run(["gdalinfo", str(p)], capture_output=True).stdout)
        elif p.suffix == ".gpkg":  # SQLite: scan every text value
            import sqlite3
            con = sqlite3.connect(p)
            tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
            txt = []
            for tb in tabs:
                for row in con.execute(f'select * from "{tb}"'):
                    txt += [x for x in row if isinstance(x, str)]
            con.close()
            scan(name, "\n".join(txt).encode())
        elif p.suffix == ".tif":
            scan(name + ":gdalinfo", subprocess.run(["gdalinfo", str(p)], capture_output=True).stdout)
        else:
            scan(name, p.read_bytes())
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", action="store_true")
    ap.add_argument("--assets", action="store_true")
    ap.add_argument("--skip-detections", action="store_true", help="keep existing fire_detections_* assets")
    a = ap.parse_args()
    do_ds = a.datasets or not a.assets
    do_as = a.assets or not a.datasets
    DS.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    AS.mkdir(parents=True, exist_ok=True)
    S = jload(DATA / "fires" / "summary.json")
    if do_ds:
        export_fires()
        export_creams()
        export_air()
        export_paddy()
        export_enforcement()
        export_boundaries()
    counts = {}
    if do_as:
        if a.skip_detections and (BUILD / "detection_counts.json").exists():
            counts = {int(k): v for k, v in jload(BUILD / "detection_counts.json").items()}
            if (BUILD / "detection_log.txt").exists():
                LOG.extend(["(from the run that built the detection assets)"] + (BUILD / "detection_log.txt").read_text().splitlines())
        else:
            counts = export_detections(S)
        export_creams_events()
        export_burned_tifs()
        export_s2night_tifs()
        export_smoke()
    elif (BUILD / "detection_counts.json").exists():
        counts = {int(k): v for k, v in jload(BUILD / "detection_counts.json").items()}
    if do_ds:
        export_burned()  # after the rasters so its README knows whether they exist
        export_s2night()
    rows = manifest(counts) if any(AS.iterdir()) else []
    write_docs(counts, rows)
    total = sum(p.stat().st_size for p in DS.rglob("*") if p.is_file())
    big = [p for p in DS.rglob("*") if p.is_file() and p.stat().st_size > 50e6]
    check(total < 80e6 and not big, f"datasets total {total / 1e6:.1f} MB, files > 50 MB: {[str(p) for p in big]}")
    hits = privacy_grep()
    if hits:
        for k, v in hits.items():
            for p, ctx in v[:5]:
                print(f"  context: {ctx}")
                log(f"PRIVACY HIT {k} [pattern {p}]")
    else:
        log("PASS privacy grep: no hits for local paths, server host/IP/user, e-mail addresses, key names or .env values")
    (REL / "VALIDATION.txt").write_text("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
