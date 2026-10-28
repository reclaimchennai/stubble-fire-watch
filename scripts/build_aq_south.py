#!/usr/bin/env python3
"""Air-quality datasets for "do north Indian farm fires affect Chennai's air?".

Usage:
    .venv/bin/python scripts/build_aq_south.py              # build from caches, fetching what is missing
    .venv/bin/python scripts/build_aq_south.py --pol-export # also refresh the pol.reclaimchennai.city export
                                                            # (read-only psql on the server, then scp)
Output: data/db/extras/aq_south.json      Cache: data/raw/aq_south/

Parts
  1. CPCB CAAQMS ground PM2.5/PM10 via OpenAQ v3 for Chennai and en-route receptors (station selection
     by name, QA and merging reused from build_aq_ground.py, plus a flat-line rule), daily 2016-..
     Delhi copied from aq_ground.json (Sep-Jan windows only there).
  2. Chennai hourly: pol.reclaimchennai.city Postgres (`aqi` project) export -> aqi.in low-cost sensors,
     TNPCB/CPCB values as scraped by pol (these are CPCB AQI SUB-INDICES of running means; inverted here),
     AirNow US consulate; plus true hourly CAAQMS concentrations from OpenAQ v3, CAMS and ERA5 hourly.
  3. Open-Meteo CAMS (Aug 2022+) and ERA5 archive (2016+) daily context per city.

Keys come from .env at runtime (OPENAQ_API_KEY) and are never printed or written anywhere.
"""
import datetime as dt
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_aq_ground as G  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "aq_south"
OUT = ROOT / "data" / "db" / "extras" / "aq_south.json"
AQ_GROUND = ROOT / "data" / "db" / "extras" / "aq_ground.json"
G.RAW = RAW  # reuse build_aq_ground's cached OpenAQ helpers, but cache into aq_south/
TODAY = dt.date.today()
LAST_DAY = TODAY - dt.timedelta(days=1)  # last complete IST day
FIRST_DAY = dt.date(2016, 1, 1)
POL_START = "2026-08-31"
# the pol (aqi) project's database runs on the same host as the pipeline; set POL_HOST=user@host in .env to export
# over ssh from elsewhere (e.g. a laptop)
SERVER = os.environ.get("POL_HOST") or next((l.split("=", 1)[1].strip().strip("'\"") for l in
    (ROOT / ".env").read_text().splitlines() if l.startswith("POL_HOST=")), "") if (ROOT / ".env").exists() else ""
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", SERVER] if SERVER else ["sh", "-c"]
log = G.log

CITIES = {  # name: (lat, lon, state, role, OpenAQ city words in station names)
    "Chennai": (13.0827, 80.2707, "Tamil Nadu", "target", ["chennai"]),
    "Puducherry": (11.9416, 79.8083, "Puducherry", "coastal receptor (south of Chennai)", ["puducherry"]),
    "Vijayawada": (16.5062, 80.6480, "Andhra Pradesh", "coastal-plain receptor (incl. Amaravati)",
                   ["vijayawada", "amaravati"]),
    "Visakhapatnam": (17.6868, 83.2185, "Andhra Pradesh", "coastal receptor", ["visakhapatnam"]),
    "Bhubaneswar": (20.2961, 85.8245, "Odisha", "coastal receptor", ["bhubaneswar"]),
    "Kolkata": (22.5726, 88.3639, "West Bengal", "IGP outflow / coastal receptor (incl. Howrah)",
                ["kolkata", "howrah"]),
    "Hyderabad": (17.3850, 78.4867, "Telangana", "Deccan receptor", ["hyderabad"]),
    "Bengaluru": (12.9716, 77.5946, "Karnataka", "non-coastal southern control", ["bengaluru"]),
    "Delhi": (28.6139, 77.2090, "Delhi", "source region reference (ground from aq_ground.json)", []),
}
SITE_ALIAS = {"victoriamemorial": "victoria", "saneguravahalli": "saneguravahalli"}
MAX_KM = 35


def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin(a * p) * math.sin(c * p) + math.cos(a * p) * math.cos(c * p) * math.cos((b - d) * p)
    return 6371 * math.acos(max(-1.0, min(1.0, x)))


def r(x, nd):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    v = round(float(x), nd)
    return int(v) if nd == 0 else v


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


DATES = [d.isoformat() for d in daterange(FIRST_DAY, LAST_DAY)]


# ======================================================================================
# Part 1: CPCB via OpenAQ
# ======================================================================================
def classify(loc):
    """(city, site, agency) for a southern/eastern CAAQMS location, or None."""
    name = loc["name"].strip()
    co = loc.get("coordinates") or {}
    if "," in name:
        site, rest = name.split(",", 1)
        cityraw, _, agency = rest.partition(" - ")
        c = cityraw.strip().lower()
        city = next((k for k, v in CITIES.items() if c in v[4]), None)
    else:  # legacy 2016-18 ids: "Site - AGENCY", city by distance
        site, _, agency = name.partition(" - ")
        city = None
        if co.get("latitude") is not None:
            best = min(((km(co["latitude"], co["longitude"], v[0], v[1]), k) for k, v in CITIES.items()
                        if k != "Delhi"))
            city = best[1] if best[0] < 20 else None
    if city is None:
        return None
    if co.get("latitude") is not None and km(co["latitude"], co["longitude"], *CITIES[city][:2]) > MAX_KM:
        return None
    return city, site.strip(), agency.strip()


def select_stations(api):
    locs = []
    for pid in G.PROVIDERS:
        p = RAW / f"locations_provider{pid}.json"
        res = G.cache_read(p, max_age_days=7)
        if res is None:
            res = api.get_all("locations", {"providers_id": pid})
            G.cache_write(p, res)
        locs += res
    stations = {}
    for loc in locs:
        cl = classify(loc)
        if not cl:
            continue
        city, site, agency = cl
        s = G.norm(site)
        key = f"{city}|{SITE_ALIAS.get(s, s)}"  # agency ignored: legacy KSPCB/WBSPCB ids = same sites
        st = stations.setdefault(key, {"city": city, "site": site, "agencies": set(), "locations": []})
        st["agencies"].add(agency)
        st["locations"].append(loc)
    return stations


QA_EXTRA = {
    "flatline": "sensor-days whose sub-daily min == max (stuck value, e.g. Manali PM2.5 12.3 ug/m3 for "
                "2026-10-17..20) are dropped; not in build_aq_ground.py's QA",
    "city_assignment": f"by the city named in the CPCB station name (Kolkata incl. Howrah, Vijayawada incl. "
                       f"Amaravati Secretariat); legacy nameless ids by distance (<20 km); any station >{MAX_KM} km "
                       "from the city centre dropped (e.g. Barrackpore, Cuttack, Chengalpattu, Hosur, Cuddalore, "
                       "Gummidipoondi not included)",
    "station_merge_south": "stations merged on city + site name only (agency ignored), because legacy 2016-18 "
                           "ids carry a different agency label (KSPCB vs CPCB, WBSPCB vs WBPCB) for the same site",
}


def openaq_daily(api, qa_stats):
    stations = select_stations(api)
    log(f"OpenAQ: {len(stations)} candidate stations")
    csd = defaultdict(lambda: {"pm25": defaultdict(dict), "pm10": defaultdict(dict)})
    city_st = defaultdict(list)
    for key, st in sorted(stations.items()):
        per = {"pm25": {}, "pm10": {}}
        sids = []
        for loc in st["locations"]:
            for s in G.location_sensors(api, loc["id"]):
                prm = s["parameter"]["name"]
                if prm not in per or s["parameter"].get("units") != "µg/m³":
                    continue
                first, last = G.d10(s.get("datetimeFirst")), G.d10(s.get("datetimeLast"))
                if not first or not last:
                    continue
                sids.append(s["id"])
                recs = G.sensor_day_records(G.fetch_sensor_days(api, s["id"], first, last))
                good = []
                for rec in recs:
                    if (rec["v"] is not None and rec["min"] is not None and rec["max"] is not None
                            and rec["max"] - rec["min"] < 1e-9):
                        qa_stats[prm]["flatline"] += 1
                        continue
                    good.append(rec)
                for d, rec in G.qa_filter(good, qa_stats[prm]).items():
                    prev = per[prm].get(d)
                    if prev is None or rec["n"] > prev["n"]:
                        per[prm][d] = rec
        if not per["pm25"] and not per["pm10"]:
            continue
        for prm in per:
            for d, rec in per[prm].items():
                csd[st["city"]][prm][d][key] = rec["v"]
        d25, d10 = sorted(per["pm25"]), sorted(per["pm10"])
        city_st[st["city"]].append({
            "id": key, "name": f"{st['site']}, {st['city']}", "agency": "/".join(sorted(a for a in st["agencies"] if a)),
            "lat": round(statistics.mean(l["coordinates"]["latitude"] for l in st["locations"]), 4),
            "lon": round(statistics.mean(l["coordinates"]["longitude"] for l in st["locations"]), 4),
            "first_pm25": d25[0] if d25 else None, "last_pm25": d25[-1] if d25 else None,
            "n_days_pm25": len(d25), "n_days_pm10": len(d10),
            "openaq_location_ids": sorted(l["id"] for l in st["locations"]), "openaq_sensor_ids": sorted(sids)})
    out = {}
    for city, data in csd.items():
        cols = {"pm25": [], "pm10": [], "n_stations": [], "n_stations_pm10": []}
        for d in DATES:
            for prm, nk in (("pm25", "n_stations"), ("pm10", "n_stations_pm10")):
                v = list(data[prm].get(d, {}).values())
                cols[prm].append(round(sum(v) / len(v), 1) if v else None)
                cols[nk].append(len(v))
        out[city] = (cols, sorted(city_st[city], key=lambda s: s["name"]))
    return out, stations


def delhi_from_aq_ground():
    j = json.loads(AQ_GROUND.read_text())
    D = j["cities"]["Delhi"]["daily"]
    idx = {d: i for i, d in enumerate(D["date"])}
    cols = {k: [] for k in ("pm25", "pm10", "n_stations", "n_stations_pm10")}
    for d in DATES:
        i = idx.get(d)
        for k in cols:
            cols[k].append(D[k][i] if i is not None else (0 if k.startswith("n_") else None))
    return cols, j["cities"]["Delhi"]["stations"], j["sources"]["retrieved"]


# ======================================================================================
# Part 3: Open-Meteo CAMS + ERA5
# ======================================================================================
def get_json(url, params, cache, max_age_days=None):
    c = G.cache_read(cache, max_age_days)
    if c is not None:
        return c
    q = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": "fire-dashboard/1.0"}),
                                        timeout=300) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                log(f"  Open-Meteo HTTP {e.code}; retry")
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            log(f"  Open-Meteo {type(e).__name__}; retry")
            time.sleep(15 * (attempt + 1))
    else:
        raise RuntimeError("giving up on " + url)
    G.cache_write(cache, data)
    time.sleep(1.5)
    return data


AQ_VARS = "pm2_5,pm10,carbon_monoxide,aerosol_optical_depth,dust"
CAMS_START = dt.date(2022, 8, 1)
CAMS_GLITCH = ("2022-12-13T12:00", "2022-12-16T06:00")  # near-zero archive glitch (see build_db_extras.py)
MIN_HOURS = 18


def daily_mean(times, vals, hour=None):
    b = defaultdict(list)
    for t, v in zip(times, vals):
        if hour is not None and int(t[11:13]) != hour:
            continue
        if v is not None:
            b[t[:10]].append(v)
    if hour is not None:
        return {d: v[0] for d, v in b.items()}
    return {d: sum(v) / len(v) for d, v in b.items() if len(v) >= MIN_HOURS}


def model_city(city, lat, lon, qa):
    slug = city.lower()
    fresh = 1  # recent chunks refetched daily
    aq = get_json("https://air-quality-api.open-meteo.com/v1/air-quality", {
        "latitude": lat, "longitude": lon, "hourly": AQ_VARS, "start_date": CAMS_START.isoformat(),
        "end_date": LAST_DAY.isoformat(), "timezone": "Asia/Kolkata"},
        RAW / "openmeteo" / f"cams_{slug}_{CAMS_START}_{LAST_DAY}.json")
    h = aq["hourly"]
    bad = 0
    for i, t in enumerate(h["time"]):
        if CAMS_GLITCH[0] <= t <= CAMS_GLITCH[1] and h["pm2_5"][i] is not None and h["pm2_5"][i] < 3:
            bad += 1
            for k in AQ_VARS.split(","):
                h[k][i] = None
    qa["cams_glitch_hours_nulled"][city] = bad
    cams = {k: daily_mean(h["time"], h[v]) for k, v in (
        ("cams_pm25", "pm2_5"), ("cams_pm10", "pm10"), ("cams_co", "carbon_monoxide"),
        ("cams_aod", "aerosol_optical_depth"), ("cams_dust", "dust"))}
    met, bt, bv = defaultdict(dict), [], []
    for y0 in range(FIRST_DAY.year, LAST_DAY.year + 1, 2):
        s = dt.date(y0, 1, 1)
        e = min(dt.date(y0 + 1, 12, 31), LAST_DAY)
        recent = e >= LAST_DAY - dt.timedelta(days=10)
        js = get_json("https://archive-api.open-meteo.com/v1/archive", {
            "latitude": lat, "longitude": lon, "start_date": s.isoformat(), "end_date": e.isoformat(),
            "daily": "wind_speed_10m_mean,wind_direction_10m_dominant,precipitation_sum",
            "hourly": "boundary_layer_height", "wind_speed_unit": "ms", "timezone": "Asia/Kolkata"},
            RAW / "openmeteo" / f"era5_{slug}_{s}_{'recent' if recent else e}.json", fresh if recent else None)
        dd = js["daily"]
        for i, t in enumerate(dd["time"]):
            met[t]["wind"] = dd["wind_speed_10m_mean"][i]
            met[t]["wdir"] = dd["wind_direction_10m_dominant"][i]
            met[t]["precip"] = dd["precipitation_sum"][i]
        bt += js["hourly"]["time"]
        bv += js["hourly"]["boundary_layer_height"]
    blh, blh14 = daily_mean(bt, bv), daily_mean(bt, bv, hour=14)
    cols = {}
    for k, nd in (("cams_pm25", 1), ("cams_pm10", 1), ("cams_co", 0), ("cams_aod", 3), ("cams_dust", 1)):
        cols[k] = [r(cams[k].get(d), nd) for d in DATES]
    cols["wind"] = [r(met.get(d, {}).get("wind"), 1) for d in DATES]
    cols["wdir"] = [r(met.get(d, {}).get("wdir"), 0) for d in DATES]
    cols["precip"] = [r(met.get(d, {}).get("precip"), 1) for d in DATES]
    cols["blh"] = [r(blh.get(d), 0) for d in DATES]
    cols["blh14"] = [r(blh14.get(d), 0) for d in DATES]
    return cols, [aq.get("latitude"), aq.get("longitude")], [js.get("latitude"), js.get("longitude")]


# ======================================================================================
# Part 2: Chennai hourly
# ======================================================================================
POL_SQL = r"""
SET default_transaction_read_only = on;
\copy (SELECT uid, location_name, latitude, longitude, city, source FROM locations WHERE source IN ('aqi.in','tnpcb','cpcb','airnow') AND latitude BETWEEN 12.5 AND 13.6 AND longitude BETWEEN 79.5 AND 80.5 ORDER BY source, uid) TO STDOUT WITH CSV HEADER
\echo ===SPLIT===
\copy (SELECT r.uid, to_char(date_trunc('hour', COALESCE(r.source_updated_at, r.station_updated_at) + interval '5 hours 30 minutes'), 'YYYY-MM-DD"T"HH24') AS hour_ist, count(*) AS n, count(DISTINCT r.pm25) AS n_distinct_pm25, round(avg(r.pm25)::numeric,2) pm25, round(min(r.pm25)::numeric,2) pm25_min, round(max(r.pm25)::numeric,2) pm25_max, round(avg(r.pm10)::numeric,2) pm10, round(max(r.pm10)::numeric,2) pm10_max, round(avg(r.co)::numeric,3) co, round(avg(r.no2)::numeric,2) no2, round(avg(r.so2)::numeric,2) so2, round(avg(r.ozone)::numeric,2) o3, round(avg(r.wind_kph)::numeric,2) wind_kph, round(avg(r.wind_kph*sin(radians(r.wind_degree)))::numeric,3) wu, round(avg(r.wind_kph*cos(radians(r.wind_degree)))::numeric,3) wv, round(max(r.precip_mm)::numeric,2) precip_mm_max, round(avg(r.precip_mm)::numeric,2) precip_mm, round(avg(r.temp_c)::numeric,2) temp_c, round(avg(r.humidity)::numeric,1) rh FROM readings r JOIN locations l ON l.uid = r.uid WHERE l.source IN ('aqi.in','tnpcb','cpcb','airnow') AND l.latitude BETWEEN 12.5 AND 13.6 AND l.longitude BETWEEN 79.5 AND 80.5 GROUP BY 1, 2 ORDER BY 1, 2) TO STDOUT WITH CSV HEADER
"""


def pol_export():
    """Read-only export on the server (default_transaction_read_only, no container restarts), then scp."""
    remote = "projects/fire/data/raw/aq_south"
    cmd = (f"mkdir -p ~/{remote} && cd ~/projects/aqi && docker compose exec -T db psql -X -q -v ON_ERROR_STOP=1 "
           f"-U postgres -d aqi_db > ~/{remote}/pol_export.raw && cd ~/{remote} && "
           "awk '/===SPLIT===/{f=1;next} !f' pol_export.raw > pol_stations.csv && "
           "awk 'f;/===SPLIT===/{f=1}' pol_export.raw > pol_station_hourly.csv && rm pol_export.raw")
    subprocess.run(SSH + [cmd], input=POL_SQL, text=True, check=True)
    RAW.mkdir(parents=True, exist_ok=True)
    if not SERVER:          # ran in place: the files are already under data/raw/aq_south
        return
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{SERVER}:{remote}/pol_stations.csv",
                    f"{SERVER}:{remote}/pol_station_hourly.csv", str(RAW) + "/"], check=True)


# CPCB National AQI breakpoints: (conc_lo, conc_hi, idx_lo, idx_hi)
BP = {
    "pm25": [(0, 30, 0, 50), (31, 60, 51, 100), (61, 90, 101, 200), (91, 120, 201, 300), (121, 250, 301, 400),
             (251, 380, 401, 500)],
    "pm10": [(0, 50, 0, 50), (51, 100, 51, 100), (101, 250, 101, 200), (251, 350, 201, 300), (351, 430, 301, 400),
             (431, 510, 401, 500)],
    "no2": [(0, 40, 0, 50), (41, 80, 51, 100), (81, 180, 101, 200), (181, 280, 201, 300), (281, 400, 301, 400),
            (401, 520, 401, 500)],
    "co": [(0, 1.0, 0, 50), (1.1, 2.0, 51, 100), (2.1, 10, 101, 200), (10.1, 17, 201, 300), (17.1, 34, 301, 400),
           (34.1, 51, 401, 500)],
}


def inv_subindex(prm, si):
    """CPCB sub-index -> concentration (linear inside the band; band edges treated as continuous)."""
    if si is None or si != si or si < 0:
        return None
    bands = BP[prm]
    for i, (clo, chi, ilo, ihi) in enumerate(bands):
        if si <= ihi:
            clo_c = 0 if i == 0 else bands[i - 1][1]
            ilo_c = 0 if i == 0 else bands[i - 1][3]
            return clo_c + (chi - clo_c) * (si - ilo_c) / (ihi - ilo_c)
    return None


HOURLY_RANGE = {"pm25": (0, 1000), "pm10": (0, 2000)}
CORE_EXCLUDE = {"TNPCB_ANTHONI_PILLAI_NAGAR"}  # Gummidipoondi industrial site, 37 km N: table only


def station_day_coverage(df, col, min_hours=12):
    """Keep station-hours only on station-days with >= min_hours valid hours (>= 50% of 24)."""
    ok = df[col].notna()
    cnt = df[ok].groupby(["uid", "day"])[col].transform("size")
    keep = pd.Series(False, index=df.index)
    keep[cnt.index] = cnt >= min_hours
    dropped = int((ok & ~keep).sum())
    df.loc[~keep, col] = None
    return dropped


def flatline(df, col, run=6):
    """Null runs of >= `run` consecutive identical hourly values per station."""
    n = 0
    for uid, g in df.sort_values("hour").groupby("uid"):
        v = g[col]
        grp = (v != v.shift()).cumsum()
        sz = v.groupby(grp).transform("size")
        bad = g.index[(sz >= run) & v.notna()]
        n += len(bad)
        df.loc[bad, col] = None
    return n


def source_mean(df, cols, extra=None):
    g = df.groupby("hour")
    out = {"time": sorted(df["hour"].unique())}
    for c, nd in cols:
        s = g[c].mean()
        out[c] = [r(s.get(t), nd) for t in out["time"]]
    nk = cols[0][0]
    cnt = g[nk].count()
    out["n_stations"] = [int(cnt.get(t, 0)) for t in out["time"]]
    if extra:
        for k, f in extra.items():
            s = f(g)
            out[k] = [r(s.get(t), 1) for t in out["time"]]
    # drop hours where nothing survived
    keep = [i for i in range(len(out["time"])) if any(out[c][i] is not None for c, _ in cols)]
    return {k: [v[i] for i in keep] for k, v in out.items()}


def pol_hourly(qa):
    st = pd.read_csv(RAW / "pol_stations.csv", dtype={"uid": str})
    h = pd.read_csv(RAW / "pol_station_hourly.csv", dtype={"uid": str})
    h = h.merge(st[["uid", "source"]], on="uid")
    h["hour"] = h["hour_ist"]
    out, sq = {}, {}
    # ---------- aqi.in low-cost sensors
    a = h[h.source == "aqi.in"].copy()
    a["day"] = a["hour"].str[:10]
    q = {"station_hours_raw": int(len(a))}
    for c in ("pm25", "pm10"):
        lo, hi = HOURLY_RANGE[c]
        bad = a[c].notna() & ((a[c] <= lo) | (a[c] > hi))
        q[f"{c}_out_of_range"] = int(bad.sum())
        a.loc[bad, c] = None
    inv = a.pm25.notna() & a.pm10.notna() & (a.pm25 > a.pm10)
    q["pm25_gt_pm10_hours_dropped"] = int(inv.sum())
    a.loc[inv, ["pm25", "pm10"]] = None
    q["pm25_flatline_ge6h"] = flatline(a, "pm25")
    # network-outlier stations: median ratio to the hourly network median outside [0.4, 2.5]
    med = a.groupby("hour").pm25.transform("median")
    ratio = (a.pm25 / med).groupby(a.uid).median()
    outl = sorted(ratio[(ratio < 0.4) | (ratio > 2.5)].index)
    q["network_outlier_stations_excluded"] = {u: round(float(ratio[u]), 2) for u in outl}
    a.loc[a.uid.isin(outl), ["pm25", "pm10"]] = None
    for c in ("pm25", "pm10"):
        q[f"{c}_hours_dropped_lt50pct_day"] = station_day_coverage(a, c)
    a["wind_ms"] = a.wind_kph / 3.6
    a.loc[(a.wind_kph < 0) | (a.wind_kph > 300), "wind_ms"] = None
    a["precip"] = a.precip_mm.where((a.precip_mm >= 0) & (a.precip_mm <= 500))
    o = source_mean(a, [("pm25", 1), ("pm10", 1)])
    gw = a.groupby("hour")
    wu, wv, ws, pr = gw.wu.mean(), gw.wv.mean(), gw.wind_ms.mean(), gw.precip.mean()
    o["wind"] = [r(ws.get(t), 1) for t in o["time"]]
    o["wdir"] = [r((math.degrees(math.atan2(wu.get(t), wv.get(t))) % 360) if pd.notna(wu.get(t)) else None, 0)
                 for t in o["time"]]
    o["precip"] = [r(pr.get(t), 2) for t in o["time"]]
    out["aqi_in_lowcost"] = o
    q["stations"] = int(a.uid.nunique())
    sq["aqi_in_lowcost"] = q
    # ---------- TNPCB/CPCB as scraped by pol: sub-indices of running means -> concentrations
    rf = h[h.source.isin(["tnpcb", "cpcb"])].copy()
    # pol's timestamp is the END of the averaging hour (matches OpenAQ datetimeTo): shift to hour-start
    rf["hour"] = (pd.to_datetime(rf["hour_ist"]) - pd.Timedelta(hours=1)).dt.strftime("%Y-%m-%dT%H")
    rf["day"] = rf["hour"].str[:10]
    q = {"station_hours_raw": int(len(rf))}
    for c in ("pm25", "pm10", "no2", "co"):
        rf[c] = rf[c].map(lambda v: inv_subindex(c, v))
    q["pm25_flatline_ge12h"] = flatline(rf, "pm25", run=12)
    for c in ("pm25", "pm10", "co", "no2"):
        q[f"{c}_hours_dropped_lt50pct_day"] = station_day_coverage(rf, c)
    core = rf[~rf.uid.isin(CORE_EXCLUDE)]
    o = source_mean(core, [("pm25", 1), ("pm10", 1), ("co", 2), ("no2", 1)])
    out["tnpcb_cpcb_pol_runningmean"] = {
        "time": o["time"], "pm25_24h": o["pm25"], "pm10_24h": o["pm10"], "co_8h": o["co"], "no2_24h": o["no2"],
        "n_stations": o["n_stations"]}
    q["stations_in_mean"] = sorted(core.uid.unique())
    sq["tnpcb_cpcb_pol_runningmean"] = q
    # ---------- AirNow US consulate (hourly PM2.5 concentration)
    an = h[h.source == "airnow"].copy()
    if len(an):
        an["day"] = an["hour"].str[:10]
        q = {"station_hours_raw": int(len(an)), "pm25_hours_dropped_lt50pct_day": station_day_coverage(an, "pm25")}
        out["airnow_us_consulate"] = source_mean(an, [("pm25", 1)])
        sq["airnow_us_consulate"] = q
    stations = []
    cnt = h.groupby("uid").agg(n_hours=("hour", "size"), first=("hour", "min"), last=("hour", "max"))
    for _, s in st.iterrows():
        c = cnt.loc[s.uid] if s.uid in cnt.index else None
        stations.append({
            "uid": s.uid, "name": s.location_name, "lat": r(s.latitude, 4), "lon": r(s.longitude, 4),
            "source": s.source, "kind": "low-cost sensor (aqi.in)" if s.source == "aqi.in" else
            ("US consulate BAM (AirNow via OpenAQ)" if s.source == "airnow" else "CAAQMS reference monitor"),
            "n_hours": int(c.n_hours) if c is not None else 0, "first_hour": c["first"] if c is not None else None,
            "last_hour": c["last"] if c is not None else None,
            "qa_excluded": (s.uid in sq["aqi_in_lowcost"]["network_outlier_stations_excluded"]) or s.uid in CORE_EXCLUDE})
    return out, sq, stations


OA_HOURLY_PARAMS = {"pm25": 1, "pm10": 1, "co": 2, "no2": 1, "wind_speed": 1, "wind_direction": 0}


def openaq_chennai_hourly(api, stations, qa):
    """True hourly CAAQMS concentrations for Chennai stations since POL_START (OpenAQ /sensors/{id}/hours)."""
    rows = []
    for key, st in stations.items():
        if st["city"] != "Chennai":
            continue
        for loc in st["locations"]:
            if G.d10(loc.get("datetimeLast")) is None or G.d10(loc.get("datetimeLast")) < POL_START:
                continue
            for s in G.location_sensors(api, loc["id"]):
                prm = s["parameter"]["name"]
                if prm not in OA_HOURLY_PARAMS or (G.d10(s.get("datetimeLast")) or "") < POL_START:
                    continue
                p = RAW / "hours" / f"sensor_{s['id']}_{POL_START}.json"
                res = G.cache_read(p, max_age_days=0.5)
                if res is None:
                    res = api.get_all(f"sensors/{s['id']}/hours", {
                        "datetime_from": f"{POL_START}T00:00:00+05:30",
                        "datetime_to": f"{TODAY.isoformat()}T00:00:00+05:30"})
                    G.cache_write(p, res)
                for x in res:
                    rows.append({"uid": key, "prm": prm, "unit": s["parameter"].get("units"),
                                 "hour": x["period"]["datetimeFrom"]["local"][:13], "v": x.get("value"),
                                 "n": (x.get("coverage") or {}).get("observedCount") or 0})
    df = pd.DataFrame(rows)
    q = {"station_param_hours_raw": int(len(df))}
    df = df[df.v.notna()]
    lohi = {"pm25": (0, 1000), "pm10": (0, 2000), "co": (0, 50), "no2": (0, 1000), "wind_speed": (0, 60),
            "wind_direction": (0, 360)}
    bad = df.apply(lambda x: not (lohi[x.prm][0] < x.v <= lohi[x.prm][1]) if x.prm != "wind_direction"
                   else not (0 <= x.v <= 360), axis=1)
    q["out_of_range"] = int(bad.sum())
    df = df[~bad]
    low = df.n < 2  # 15-min feeds: need >= 2 of 4 records in the hour
    q["hours_lt2_records"] = int(low.sum())
    df = df[~low]
    w = df.pivot_table(index=["uid", "hour"], columns="prm", values="v", aggfunc="mean").reset_index()
    w["day"] = w["hour"].str[:10]
    for c in ("pm25", "pm10", "co", "no2"):
        if c in w:
            q[f"{c}_flatline_ge4h"] = flatline(w, c, run=4)
            q[f"{c}_hours_dropped_lt50pct_day"] = station_day_coverage(w, c)
    w["wu"] = w.wind_speed * w.wind_direction.map(lambda d: math.sin(math.radians(d)))
    w["wv"] = w.wind_speed * w.wind_direction.map(lambda d: math.cos(math.radians(d)))
    o = source_mean(w, [("pm25", 1), ("pm10", 1), ("co", 2), ("no2", 1)])
    g = w.groupby("hour")
    ws, wu, wv = g.wind_speed.mean(), g.wu.mean(), g.wv.mean()
    o["wind"] = [r(ws.get(t), 1) for t in o["time"]]
    o["wdir"] = [r((math.degrees(math.atan2(wu.get(t), wv.get(t))) % 360) if pd.notna(wu.get(t)) else None, 0)
                 for t in o["time"]]
    q["stations"] = sorted(w.uid.unique())
    q["units_as_labelled_by_openaq"] = sorted({f"{p}:{u}" for p, u in zip(df.prm, df.unit)})
    qa["openaq_cpcb_hourly"] = q
    return o


def model_chennai_hourly():
    lat, lon = CITIES["Chennai"][:2]
    aq = get_json("https://air-quality-api.open-meteo.com/v1/air-quality", {
        "latitude": lat, "longitude": lon, "hourly": "pm2_5,pm10,carbon_monoxide,aerosol_optical_depth",
        "start_date": POL_START, "end_date": TODAY.isoformat(), "timezone": "Asia/Kolkata"},
        RAW / "openmeteo" / f"cams_hourly_chennai_{POL_START}.json", 0.5)
    h = aq["hourly"]
    cams = {"time": [t[:13] for t in h["time"]], "pm25": [r(v, 1) for v in h["pm2_5"]],
            "pm10": [r(v, 1) for v in h["pm10"]], "co": [r(v, 0) for v in h["carbon_monoxide"]],
            "aod": [r(v, 3) for v in h["aerosol_optical_depth"]]}
    met = get_json("https://archive-api.open-meteo.com/v1/archive", {
        "latitude": lat, "longitude": lon, "start_date": POL_START, "end_date": LAST_DAY.isoformat(),
        "hourly": "wind_speed_10m,wind_direction_10m,precipitation,boundary_layer_height",
        "wind_speed_unit": "ms", "timezone": "Asia/Kolkata"},
        RAW / "openmeteo" / f"era5_hourly_chennai_{POL_START}.json", 0.5)
    m = met["hourly"]
    era = {"time": [t[:13] for t in m["time"]], "wind": [r(v, 1) for v in m["wind_speed_10m"]],
           "wdir": [r(v, 0) for v in m["wind_direction_10m"]], "precip": [r(v, 1) for v in m["precipitation"]],
           "blh": [r(v, 0) for v in m["boundary_layer_height"]]}
    for o in (cams, era):  # trim trailing all-null hours
        keys = [k for k in o if k != "time"]
        while o["time"] and all(o[k][-1] is None for k in keys):
            for k in o:
                o[k].pop()
    return cams, era


# ======================================================================================
def main():
    if "--pol-export" in sys.argv or not (RAW / "pol_station_hourly.csv").exists():
        log("pol export (read-only) ...")
        pol_export()
    api = G.OpenAQ()
    qa_stats = {p: defaultdict(int) for p in ("pm25", "pm10")}
    ground, stations = openaq_daily(api, qa_stats)
    dcols, dstations, dret = delhi_from_aq_ground()
    model_qa = {"cams_glitch_hours_nulled": {}}
    cities, coverage = {}, {}
    for city, (lat, lon, state, role, _) in CITIES.items():
        log(f"model context {city}")
        mcols, gaq, gmet = model_city(city, lat, lon, model_qa)
        if city == "Delhi":
            gcols, gst = dcols, dstations
        elif city in ground:
            gcols, gst = ground[city]
        else:
            gcols = {"pm25": [None] * len(DATES), "pm10": [None] * len(DATES), "n_stations": [0] * len(DATES),
                     "n_stations_pm10": [0] * len(DATES)}
            gst = []
        daily = {"date": DATES, **gcols, **mcols}
        cities[city] = {"lat": lat, "lon": lon, "state": state, "role": role, "grid_lat_lon_cams": gaq,
                        "grid_lat_lon_era5": gmet, "stations": gst, "daily": daily}
        cov = {}
        for y in range(FIRST_DAY.year, LAST_DAY.year + 1):
            ix = [i for i, d in enumerate(DATES) if d.startswith(str(y))]
            cov[str(y)] = {
                "days": len(ix),
                "ground_pm25_days": sum(daily["pm25"][i] is not None for i in ix),
                "ground_pm10_days": sum(daily["pm10"][i] is not None for i in ix),
                "mean_n_stations_pm25": r(statistics.mean([daily["n_stations"][i] for i in ix if daily["n_stations"][i]])
                                          if any(daily["n_stations"][i] for i in ix) else 0, 1),
                "cams_days": sum(daily["cams_pm25"][i] is not None for i in ix),
                "era5_days": sum(daily["wind"][i] is not None for i in ix),
                "oct_nov_ground_pm25_days": sum(daily["pm25"][i] is not None for i in ix if DATES[i][5:7] in ("10", "11")),
                "oct_nov_cams_days": sum(daily["cams_pm25"][i] is not None for i in ix if DATES[i][5:7] in ("10", "11"))}
        coverage[city] = cov

    log("Chennai hourly ...")
    pol, pol_qa, pol_stations = pol_hourly(model_qa)
    hourly_qa = dict(pol_qa)
    oa_h = openaq_chennai_hourly(api, stations, hourly_qa)
    cams_h, era_h = model_chennai_hourly()
    chennai_hourly = {"openaq_cpcb_caaqms": oa_h, **pol, "cams_model": cams_h, "era5": era_h}

    sources = {
        "ground_openaq": {
            "name": "CPCB / state PCB CAAQMS via OpenAQ v3 (providers 'CPCB' 168 and 'caaqm' 163)",
            "api": G.OPENAQ, "endpoints": ["/locations?providers_id=", "/locations/{id}/sensors",
                                           "/sensors/{id}/days", "/sensors/{id}/hours (Chennai hourly only)"],
            "fields": "daily.pm25, daily.pm10 (ug/m3, city mean of station daily means, IST day), "
                      "daily.n_stations, daily.n_stations_pm10",
            "qa_rules": {**G.QA, **QA_EXTRA},
            "qa_counts_sensor_days": {p: dict(v) for p, v in qa_stats.items()},
            "openaq_gap": "OpenAQ has no CPCB data for ANY station from ~2022-10-31 (last caaqm records) to "
                          "2025-02-18 (re-registered ids start 2025-02-19): 2022-11..2025-02 is null for every "
                          "city. Not station downtime. Use the cams_* columns (from 2022-08) as model context "
                          "for that gap - CAMS is a ~0.4 deg forecast model, biased low in Indian cities",
            "license_note": "CPCB data redistributed by OpenAQ; cite CPCB and OpenAQ",
            "retrieved": TODAY.isoformat()},
        "ground_delhi": {"copied_from": "data/db/extras/aq_ground.json (cities.Delhi)", "retrieved": dret,
                         "note": "aq_ground.json carries Delhi daily only for Sep 1 - Jan 31 windows; other days "
                                 "are null with n_stations 0 here"},
        "cams": {
            "provider": "Open-Meteo Air Quality API (https://air-quality-api.open-meteo.com/v1/air-quality), keyless",
            "model": "CAMS global atmospheric composition forecasts (~0.4 deg) as archived by Open-Meteo; model "
                     "output, not measurements. Archive starts 2022-08 (earlier days null)",
            "fields": {"cams_pm25": "ug/m3", "cams_pm10": "ug/m3", "cams_co": "carbon monoxide, ug/m3 "
                       "(biomass-smoke tracer; CO lifetime ~1-2 months lets it carry long-range smoke)",
                       "cams_aod": "aerosol optical depth 550 nm", "cams_dust": "ug/m3"},
            "aggregation": f"daily mean of hourly IST values; null if < {MIN_HOURS} valid hours",
            "qa": {"rule": f"hours {CAMS_GLITCH[0]}..{CAMS_GLITCH[1]} IST with pm2_5 < 3 set null (archive glitch)",
                   "hours_nulled": model_qa["cams_glitch_hours_nulled"]}},
        "era5": {
            "provider": "Open-Meteo Historical Weather API (https://archive-api.open-meteo.com/v1/archive), keyless",
            "model": "ERA5 reanalysis (Open-Meteo best-match; last ~5 days filled from ECMWF IFS analysis)",
            "fields": {"wind": "m/s daily mean 10 m", "wdir": "deg, dominant 10 m direction wind blows FROM",
                       "precip": "mm daily sum", "blh": "m, daily mean boundary_layer_height",
                       "blh14": "m, boundary_layer_height at 14:00 IST"},
            "gaps": "boundary_layer_height null 2024-01 in the Open-Meteo archive (see aq_met.json)"},
        "chennai_hourly": {
            "time_basis": "IST, label = start of the hour (YYYY-MM-DDTHH); each source has its own time array",
            "window": f"{POL_START} to {TODAY.isoformat()} (pol.reclaimchennai.city database start)",
            "openaq_cpcb_caaqms": "TRUE hourly concentrations of Chennai CAAQMS (CPCB+TNPCB) from OpenAQ "
                                  "/sensors/{id}/hours: pm25, pm10 ug/m3; co mg/m3 (OpenAQ labels it ppb but values "
                                  "are CPCB's mg/m3); no2 as reported (CPCB ug/m3, OpenAQ label ppb); wind m/s; wdir "
                                  "vector-mean. Mean over stations; n_stations counts pm25. Best reference series",
            "aqi_in_lowcost": "aqi.in low-cost optical sensors (pol DB, ~78 sites in the CMA): pm25, pm10 ug/m3 "
                              "(uncorrected, no RH correction); wind m/s, wdir, precip mm - these weather fields "
                              "repeat across sites (weather-API values attached by aqi.in, not measured on site)",
            "tnpcb_cpcb_pol_runningmean": "pol DB 'tnpcb'/'cpcb' rows are scraped from tnpcb.gov.in/aqi.php, "
                                          "whose pollutant 'avg' values are CPCB AQI SUB-INDICES of running means "
                                          "(verified against OpenAQ hourly at Manali: 24-h mean PM, 8-h mean CO), "
                                          "not hourly concentrations. Inverted here with CPCB breakpoints to "
                                          "pm25_24h/pm10_24h/no2_24h (ug/m3), co_8h (mg/m3). Smoothed; lagging; "
                                          "Gummidipoondi (Anthoni Pillai Nagar) excluded from the mean",
            "airnow_us_consulate": "US Consulate Chennai PM2.5 (AirNow via OpenAQ, in pol DB), only 2026-10-09..15",
            "cams_model": "CAMS hourly at Chennai (pm25, pm10 ug/m3, co ug/m3, aod)",
            "era5": "ERA5 hourly at Chennai (wind m/s, wdir deg from, precip mm, blh m)",
            "qa_rules": {"range": "hourly pm25 <=0 or >1000, pm10 <=0 or >2000 dropped",
                         "pm25_gt_pm10": "aqi.in hours with pm25 > pm10 dropped (optical-sensor artefact)",
                         "flatline": ">= 6 identical consecutive hourly values (>= 4 for OpenAQ) dropped",
                         "network_outlier": "aqi.in stations whose median ratio to the hourly network median is "
                                            "< 0.4 or > 2.5 excluded",
                         "coverage": "station-days with < 12 valid hours (50%) for a variable dropped for that "
                                     "variable",
                         "openaq_min_records": "OpenAQ hours with < 2 sub-hourly records dropped"},
            "qa_counts": hourly_qa},
        "pol_export": {"server": "pol.reclaimchennai.city `aqi` Docker project, Postgres aqi_db (read-only "
                                 "transaction, \\copy to CSV)", "cache": "data/raw/aq_south/pol_*.csv",
                       "time_columns": "COALESCE(source_updated_at, station_updated_at) (UTC) bucketed by IST hour"},
    }
    out = {"sources": sources, "retrieved": TODAY.isoformat(), "coverage": coverage, "cities": cities,
           "chennai_hourly": chennai_hourly, "chennai_stations": pol_stations}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False, ensure_ascii=False))
    log(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.2f} MB), {api.n} OpenAQ requests")


if __name__ == "__main__":
    main()
