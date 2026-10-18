#!/usr/bin/env python3
"""Ground-truth air quality (CPCB CAAQMS via OpenAQ v3) and stubble-share datasets.

Usage:
    .venv/bin/python scripts/build_aq_ground.py aq        # Part 1 -> data/db/extras/aq_ground.json
    .venv/bin/python scripts/build_aq_ground.py stubble   # Part 2 -> data/db/extras/stubble_share.json
    .venv/bin/python scripts/build_aq_ground.py all

API keys are read at runtime from .env (OPENAQ_API_KEY, DATAGOV_API_KEY). They are never
printed, logged or written to any cache/output file (cache files store response bodies only).
Every HTTP response is cached under data/raw/aq_ground/ so re-runs are incremental.
"""
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "aq_ground"
OUT_AQ = ROOT / "data" / "db" / "extras" / "aq_ground.json"
OUT_STUBBLE = ROOT / "data" / "db" / "extras" / "stubble_share.json"
OPENAQ = "https://api.openaq.org/v3"
TODAY = dt.date.today()


def load_key(name):
    v = os.environ.get(name)
    if v:
        return v
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------------------
# OpenAQ client: throttled (<=60/min and <=2000/h => one request per >=1.85 s), cached.
# --------------------------------------------------------------------------------------
class OpenAQ:
    MIN_GAP = 1.85

    def __init__(self):
        key = load_key("OPENAQ_API_KEY")
        if not key:
            sys.exit("OPENAQ_API_KEY missing from environment/.env")
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = key
        self.s.headers["User-Agent"] = "fire-dashboard aq_ground builder"
        self.last = 0.0
        self.n = 0

    def get(self, path, params):
        for attempt in range(8):
            wait = self.MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(f"{OPENAQ}/{path}", params=params, timeout=90)
            except requests.RequestException as e:
                log(f"  net error {type(e).__name__}; retry")
                time.sleep(10 * (attempt + 1))
                continue
            self.n += 1
            if r.status_code == 200:
                rem = r.headers.get("X-Ratelimit-Remaining")
                if rem is not None and rem.isdigit() and int(rem) <= 2:
                    time.sleep(int(r.headers.get("X-Ratelimit-Reset", "30") or 30) + 1)
                return r.json()
            if r.status_code == 429:
                reset = r.headers.get("X-Ratelimit-Reset", "60")
                t = int(reset) + 2 if reset.isdigit() else 60
                log(f"  429 rate limited; sleeping {t}s")
                time.sleep(t)
                continue
            if r.status_code in (404, 422):
                return {"results": [], "error_status": r.status_code}
            log(f"  HTTP {r.status_code} on {path}; retry")
            time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"giving up on {path}")

    def get_all(self, path, params, limit=1000):
        out, page = [], 1
        while True:
            j = self.get(path, dict(params, limit=limit, page=page))
            res = j.get("results", [])
            out += res
            if len(res) < limit:
                return out
            page += 1


def cache_read(p, max_age_days=None):
    if not p.exists():
        return None
    if max_age_days is not None and time.time() - p.stat().st_mtime > max_age_days * 86400:
        return None
    return json.loads(p.read_text())


def cache_write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(p)


# --------------------------------------------------------------------------------------
# Part 1: station selection
# --------------------------------------------------------------------------------------
PROVIDERS = {163: "caaqm", 168: "CPCB"}  # both are CPCB CAAQMS feeds in OpenAQ

CITY_CENTER = {
    "Delhi": (28.6139, 77.2090), "Amritsar": (31.634, 74.8723), "Ludhiana": (30.901, 75.8573),
    "Patiala": (30.3398, 76.3869), "Bathinda": (30.211, 74.9455), "Jalandhar": (31.326, 75.5762),
    "Khanna": (30.705, 76.2219), "Mandi Gobindgarh": (30.6669, 76.2933), "Rupnagar": (30.9661, 76.5331),
    "Chandigarh": (30.7333, 76.7794), "Lucknow": (26.8467, 80.9462), "Kanpur": (26.4499, 80.3319),
}
PUNJAB_CITIES = {"Amritsar", "Ludhiana", "Patiala", "Bathinda", "Jalandhar", "Khanna",
                 "Mandi Gobindgarh", "Rupnagar"}
STATE = {c: "Punjab" for c in PUNJAB_CITIES}
STATE.update({"Delhi": "Delhi", "Chandigarh": "Chandigarh", "Lucknow": "Uttar Pradesh",
              "Kanpur": "Uttar Pradesh"})
# legacy OpenAQ location names without a city part (assigned by coordinates, checked by hand)
NAMELESS = {16: ("Delhi", "Civil Lines", "DPCC"), 15: ("Delhi", "IGI Airport", "IMD"),
            2463: ("Lucknow", "Central School", "CPCB"), 354: ("Lucknow", "Lalbagh", "CPCB"),
            2595: ("Kanpur", "Nehru Nagar", "UPPCB")}
SITE_ALIAS = {"incometaxoffice": "ito", "delhitechnologicaluniversity": "dtu",
              "lalbaghdnpark": "lalbagh", "igiairportt3": "igiairport"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def classify(loc):
    """Return (city, site, agency) or None."""
    if loc["id"] in NAMELESS:
        return NAMELESS[loc["id"]]
    name = loc["name"].strip()
    if "," not in name:
        return None
    site, rest = name.split(",", 1)
    m = re.match(r"(.*?)\s*-\s*([A-Za-z ]+)\s*$", rest)
    cityraw, agency = (m.group(1), m.group(2).strip()) if m else (rest, "")
    cityraw = cityraw.strip()
    c = cityraw.lower()
    city = None
    if "delhi" in c:
        city = "Delhi"
    elif "gobindgarh" in c:
        city = "Mandi Gobindgarh"
    else:
        for k in list(CITY_CENTER) + []:
            if k.lower() == c:
                city = k
        if city is None and agency == "HSPCB" or (agency == "IMD" and c == "gurugram"):
            city = cityraw.title().replace("Yamuna Nagar", "Yamuna Nagar")
            STATE.setdefault(city, "Haryana")
    if city is None:
        return None
    return city, site.strip(), agency


def station_key(city, site, agency, existing):
    s = SITE_ALIAS.get(norm(site), norm(site))
    k = f"{city}|{s}|{agency}"
    return k


def select_stations(api):
    locs = []
    for pid in PROVIDERS:
        p = RAW / f"locations_provider{pid}.json"
        res = cache_read(p, max_age_days=7)
        if res is None:
            res = api.get_all("locations", {"providers_id": pid})
            cache_write(p, res)
        locs += res
    stations = {}
    for loc in locs:
        cl = classify(loc)
        if not cl:
            continue
        city, site, agency = cl
        key = station_key(city, site, agency, stations)
        st = stations.setdefault(key, {"city": city, "site": site, "agency": agency, "locations": []})
        st["locations"].append(loc)
    # merge agency-less / agency-variant duplicates where the same site has only one agency
    return stations


def location_sensors(api, loc_id):
    p = RAW / "sensors" / f"location_{loc_id}.json"
    res = cache_read(p, max_age_days=7)
    if res is None:
        res = api.get_all(f"locations/{loc_id}/sensors", {})
        cache_write(p, res)
    return res


def d10(x):
    return (x or {}).get("local", "")[:10] or None


def fetch_sensor_days(api, sid, first, last):
    """Cache days per sensor-year. Re-fetch a year only if the sensor now extends past what
    was cached when that year was not yet complete."""
    y0, y1 = int(first[:4]), int(last[:4])
    out = []
    for y in range(y0, y1 + 1):
        p = RAW / "days" / f"sensor_{sid}_{y}.json"
        c = cache_read(p)
        need = c is None
        if c is not None:
            fetched = c["fetched"][:10]
            if fetched < f"{y + 1}-01-02" and last > c.get("sensor_last_at_fetch", "") and last >= fetched[:10]:
                need = True
        if need:
            df = max(f"{y}-01-01", first)
            dtt = min(f"{y + 1}-01-01", (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat())
            res = api.get_all(f"sensors/{sid}/days", {"date_from": df, "date_to": dtt}, limit=1000)
            c = {"sensor": sid, "year": y, "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "sensor_last_at_fetch": last, "results": res}
            cache_write(p, c)
            log(f"  sensor {sid} {y}: {len(res)} days (req #{api.n})")
        out += c["results"]
    return out


QA = {
    "completeness": "a daily aggregate is kept only if OpenAQ's coverage.observedCount >= 75% of the "
                    "sensor's native records/day (24 for hourly feeds, 96 for 15-min feeds; native rate "
                    "= median observedCount of that sensor-year, i.e. the equivalent of >=18 of 24 hourly "
                    "values) AND the observations span >= 17 h of the local day",
    "range": "days with daily mean <= 0 or > 1500 ug/m3 dropped; days whose max sub-daily value > 1500 "
             "ug/m3 also dropped (mean contaminated by an impossible value)",
    "no_filling": "no interpolation or gap filling; missing days are null",
    "station_merge": "OpenAQ often lists the same CPCB station several times (providers 'CPCB' and "
                     "'caaqm', legacy 2016-18 ids, re-registered 2025 ids). These are merged per "
                     "station (same site name + city + agency); per day the qualifying sensor with the "
                     "most records is used",
    "city_daily": "city value = arithmetic mean of qualifying station daily means that local day "
                  "(Asia/Kolkata); n_stations = number of stations averaged",
    "monthly": "monthly mean = mean of available city-daily values in the month; n_days reported; "
               "months with < 10 valid days are reported with value null",
}


def sensor_day_records(raw):
    recs = []
    for r in raw:
        cov = r.get("coverage") or {}
        summ = r.get("summary") or {}
        day = (r["period"]["datetimeFrom"]["local"])[:10]
        try:
            f = dt.datetime.fromisoformat(cov["datetimeFrom"]["utc"].replace("Z", "+00:00"))
            t = dt.datetime.fromisoformat(cov["datetimeTo"]["utc"].replace("Z", "+00:00"))
            span = (t - f).total_seconds() / 3600
        except Exception:
            span = None
        recs.append({"day": day, "v": r.get("value"), "n": cov.get("observedCount") or 0,
                     "span": span, "min": summ.get("min"), "max": summ.get("max")})
    return recs


def qa_filter(recs, stats):
    by_year = defaultdict(list)
    for r in recs:
        by_year[r["day"][:4]].append(r["n"])
    native = {y: (96 if statistics.median(ns) > 48 else 24) for y, ns in by_year.items()}
    keep = {}
    for r in recs:
        stats["total"] += 1
        v = r["v"]
        if v is None:
            stats["null"] += 1
            continue
        if r["n"] < 0.75 * native[r["day"][:4]] or (r["span"] is not None and r["span"] < 17):
            stats["low_coverage"] += 1
            continue
        if v <= 0 or v > 1500:
            stats["mean_out_of_range"] += 1
            continue
        if r["max"] is not None and r["max"] > 1500:
            stats["max_gt_1500"] += 1
            continue
        if r["min"] is not None and r["min"] <= 0:
            stats["kept_with_nonpositive_subdaily_min"] += 1
        stats["kept"] += 1
        prev = keep.get(r["day"])
        if prev is None or r["n"] > prev["n"]:
            keep[r["day"]] = r
    return keep


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def build_aq():
    api = OpenAQ()
    stations = select_stations(api)
    log(f"{len(stations)} candidate stations")
    qa_stats = {p: defaultdict(int) for p in ("pm25", "pm10")}
    city_station_daily = defaultdict(lambda: {"pm25": defaultdict(dict), "pm10": defaultdict(dict)})
    city_stations = defaultdict(list)
    for key, st in sorted(stations.items()):
        per_param = {"pm25": {}, "pm10": {}}
        sensor_ids, loc_ids = [], []
        for loc in st["locations"]:
            loc_ids.append(loc["id"])
            for s in location_sensors(api, loc["id"]):
                prm = s["parameter"]["name"]
                if prm not in per_param or s["parameter"].get("units") != "µg/m³":
                    continue
                first, last = d10(s.get("datetimeFirst")), d10(s.get("datetimeLast"))
                if not first or not last:
                    continue
                sensor_ids.append(s["id"])
                raw = fetch_sensor_days(api, s["id"], first, last)
                kept = qa_filter(sensor_day_records(raw), qa_stats[prm])
                for d, r in kept.items():
                    prev = per_param[prm].get(d)
                    if prev is None or r["n"] > prev["n"]:
                        per_param[prm][d] = r
        if not per_param["pm25"] and not per_param["pm10"]:
            continue
        for prm in per_param:
            for d, r in per_param[prm].items():
                city_station_daily[st["city"]][prm][d][key] = r["v"]
        days25 = sorted(per_param["pm25"])
        days10 = sorted(per_param["pm10"])
        lat = statistics.mean(l["coordinates"]["latitude"] for l in st["locations"])
        lon = statistics.mean(l["coordinates"]["longitude"] for l in st["locations"])
        city_stations[st["city"]].append({
            "id": key, "name": f"{st['site']}, {st['city']}" + (f" - {st['agency']}" if st["agency"] else ""),
            "lat": round(lat, 4), "lon": round(lon, 4),
            "first": days25[0] if days25 else None, "last": days25[-1] if days25 else None,
            "first_pm10": days10[0] if days10 else None, "last_pm10": days10[-1] if days10 else None,
            "n_days_pm25": len(days25), "n_days_pm10": len(days10),
            "openaq_location_ids": sorted(loc_ids), "openaq_sensor_ids": sorted(sensor_ids)})

    cities = {}
    for city, data in sorted(city_station_daily.items()):
        allday = sorted(set(data["pm25"]) | set(data["pm10"]))
        if not allday:
            continue

        def cval(prm, d):
            vals = list(data[prm].get(d, {}).values())
            return (round(sum(vals) / len(vals), 1), len(vals)) if vals else (None, 0)

        # winter daily series: Sep 1 - Jan 31 for every winter with any data
        winters = sorted({(int(d[:4]) if int(d[5:7]) >= 9 else int(d[:4]) - 1)
                          for d in allday if int(d[5:7]) >= 9 or int(d[5:7]) == 1})
        D = {"date": [], "pm25": [], "pm10": [], "n_stations": [], "n_stations_pm10": []}
        for w in winters:
            for d in daterange(dt.date(w, 9, 1), min(dt.date(w + 1, 1, 31), TODAY)):
                ds = d.isoformat()
                v25, n25 = cval("pm25", ds)
                v10, n10 = cval("pm10", ds)
                D["date"].append(ds); D["pm25"].append(v25); D["pm10"].append(v10)
                D["n_stations"].append(n25); D["n_stations_pm10"].append(n10)
        # monthly means, all months
        mon = defaultdict(lambda: {"pm25": [], "pm10": [], "ns": []})
        for d in allday:
            v25, n25 = cval("pm25", d)
            v10, _ = cval("pm10", d)
            m = mon[d[:7]]
            if v25 is not None:
                m["pm25"].append(v25); m["ns"].append(n25)
            if v10 is not None:
                m["pm10"].append(v10)
        M = {"month": [], "pm25": [], "pm10": [], "n_days_pm25": [], "n_days_pm10": [], "mean_n_stations": []}
        for k in sorted(mon):
            m = mon[k]
            M["month"].append(k)
            M["pm25"].append(round(statistics.mean(m["pm25"]), 1) if len(m["pm25"]) >= 10 else None)
            M["pm10"].append(round(statistics.mean(m["pm10"]), 1) if len(m["pm10"]) >= 10 else None)
            M["n_days_pm25"].append(len(m["pm25"])); M["n_days_pm10"].append(len(m["pm10"]))
            M["mean_n_stations"].append(round(statistics.mean(m["ns"]), 1) if m["ns"] else 0)
        # stations per year (pm25)
        spy = defaultdict(set)
        for d, sv in data["pm25"].items():
            for k in sv:
                spy[d[:4]].add(k)
        lat, lon = CITY_CENTER.get(city) or (
            round(statistics.mean(s["lat"] for s in city_stations[city]), 4),
            round(statistics.mean(s["lon"] for s in city_stations[city]), 4))
        cities[city] = {
            "state": STATE.get(city, "Haryana"), "lat": lat, "lon": lon,
            "stations": sorted(city_stations[city], key=lambda s: s["name"]),
            "stations_with_pm25_per_year": {y: len(v) for y, v in sorted(spy.items())},
            "daily": D, "monthly": M}
        log(f"{city}: {len(city_stations[city])} stations, {len(allday)} days")

    out = {
        "sources": {
            "name": "CPCB CAAQMS (Continuous Ambient Air Quality Monitoring Stations), via OpenAQ v3",
            "api": OPENAQ, "endpoints": ["/locations?providers_id=163,168", "/locations/{id}/sensors",
                                         "/sensors/{id}/days"],
            "openaq_providers": {str(k): v for k, v in PROVIDERS.items()},
            "license_note": "CPCB data redistributed by OpenAQ; cite CPCB and OpenAQ",
            "retrieved": TODAY.isoformat(),
            "time_basis": "local calendar day, Asia/Kolkata (OpenAQ daily aggregates)",
            "units": "ug/m3 (daily mean of sub-daily values)",
            "qa_rules": QA,
            "qa_counts_sensor_days": {p: dict(v) for p, v in qa_stats.items()},
            "daily_window": "Sep 1 - Jan 31 of every winter with data (winter labelled by the Sep year); "
                            "null where no qualifying station data",
            "caveats": [
                "OpenAQ has NO CPCB data for any selected station between 2022-10 (last data ~mid/late "
                "Oct 2022) and 2025-02-18: winters 2022-23 (only Sep-Oct 2022), 2023-24 and 2024-25 are "
                "missing. This is an OpenAQ ingestion gap, not station downtime (CPCB's own portal has it)",
                "other ingestion gaps: Jan-Jun 2019 almost empty, Feb-Mar 2018 handover from legacy ids, "
                "and isolated missing days even in peak season (e.g. 2019-11-01..03 absent in OpenAQ)",
                "OpenAQ ids: legacy ids cover ~2016-02..2018-02, provider 'caaqm' ~2018-03..2022-10, "
                "provider 'CPCB' 2018/2019..2022 and re-registered sensors from 2025-02; merged per station",
                "coverage.observedCount is a count of raw records (feeds mix 15-min and hourly), not of "
                "distinct hours, hence the additional >=17 h span requirement",
                "station counts change over time (Delhi network grew from ~6-10 in 2016-17 to ~40 "
                "from 2018-12); city means are not network-constant - use n_stations",
                "Haryana/Punjab stations mostly start 2018-2019; Chandigarh CAAQMS from 2021-08",
            ],
        },
        "cities": cities,
    }
    OUT_AQ.parent.mkdir(parents=True, exist_ok=True)
    OUT_AQ.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    log(f"wrote {OUT_AQ} ({OUT_AQ.stat().st_size / 1e6:.2f} MB), {api.n} API requests this run")



# ======================================================================================
# Part 2: stubble-burning share of Delhi PM2.5 (IITM DSS / AQEWS and SAFAR)
# ======================================================================================
WB = "http://web.archive.org"
EWS = "https://ews.tropmet.res.in"
SAFAR_HOME = "http://safar.tropmet.res.in/"
_last_web = [0.0]


def web_get(url, cache_path, timeout=120):
    """Polite (>=1 s between requests) cached GET; returns text or None."""
    if cache_path.exists():
        return cache_path.read_text(errors="replace")
    for attempt in range(4):
        wait = 1.2 - (time.time() - _last_web[0])
        if wait > 0:
            time.sleep(wait)
        _last_web[0] = time.time()
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "fire-dashboard research (polite)"})
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(r.content)
            return r.content.decode("utf-8", "replace")
        if r.status_code in (404, 403):
            return None
        time.sleep(5 * (attempt + 1))
    return None


def cdx(url_pat, match="exact", cache_name=None):
    p = RAW / "wayback" / f"cdx_{cache_name}.json"
    j = cache_read(p, max_age_days=7)
    if j is None:
        txt = None
        for attempt in range(3):
            try:
                _last_web[0] = time.time()
                r = requests.get(f"{WB}/cdx/search/cdx", params={
                    "url": url_pat, "matchType": match, "output": "json",
                    "filter": "statuscode:200", "collapse": "digest"}, timeout=300)
                if r.status_code == 200:
                    txt = r.text
                    break
            except requests.RequestException:
                pass
            time.sleep(10)
        if txt is None:
            log(f"  CDX failed for {url_pat}; not cached")
            return []
        j = json.loads(txt) if txt.strip() else []
        cache_write(p, j)
    return [dict(zip(j[0], row)) for row in j[1:]] if j else []


def chartjs(html_text):
    """Parse inline Chart.js objects: {name: (labels, {dataset_label: [values]})}."""
    out = {}
    for m in re.finditer(r"(?:const|var|let)\s+(\w+)\s*=\s*\{\s*labels:\s*\[(.*?)\]\s*,\s*datasets:\s*\[(.*?)\]\s*\}\s*;",
                         html_text, re.S):
        labs = re.findall(r"[\"']([^\"']+)[\"']", m.group(2))
        ds = {}
        for a, b in re.findall(r"label:\s*'([^']+)'.*?data:\s*\[([^\]]*)\]", m.group(3), re.S):
            vals = []
            for x in b.split(","):
                x = x.strip()
                if not x:
                    continue
                try:
                    vals.append(None if x == "null" else float(x))
                except ValueError:
                    vals.append(None)
            ds[a] = vals
        out[m.group(1)] = (labs, ds)
    return out


def ts_iso(ts):
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}Z"


def dss_daily_from_page(html_text):
    """DSS index.php 'data2' = Daily Mean contribution from Stubble/Biomass Burning to PM2.5 in Delhi."""
    c = chartjs(html_text).get("data2")
    if not c:
        return {}
    labs, ds = c
    key = next((k for k in ds if re.search(r"stubble|biomass", k, re.I)), None)
    if not key:
        return {}
    res = {}
    for lab, v in zip(labs, ds[key]):
        if v is not None and re.match(r"\d{4}-\d{2}-\d{2}", lab):
            res.setdefault(lab[:10], v)  # first occurrence of a repeated date
    return res


def obfire_from_page(html_text):
    """ob_fire_count.php: data2 'PM2.5' fire contribution; 'VIIRS YYYY' daily fire counts."""
    ch = chartjs(html_text)
    share, viirs = {}, {}
    if "data2" in ch:
        labs, ds = ch["data2"]
        for lab, v in zip(labs, ds.get("PM2.5", [])):
            if v is not None and re.match(r"\d{4}-\d{2}-\d{2}", lab):
                share.setdefault(lab[:10], v)
    for name, (labs, ds) in ch.items():
        for k, vals in ds.items():
            m = re.fullmatch(r"VIIRS (\d{4})", k.strip())
            if not m or len(vals) != len(labs):
                continue  # only use counts whose own date labels are published alongside
            for lab, v in zip(labs, vals):
                if v is not None and lab[:4] == m.group(1):
                    viirs.setdefault(lab[:10], v)
    return share, viirs


def collect_ews(page, parse, live_name):
    """Union of all Wayback captures + today's live page. Later captures win; provenance per value."""
    caps = []
    for pat in page["cdx"]:
        caps += cdx(pat, cache_name=re.sub(r"\W+", "_", pat))
    seen = set()
    caps = sorted((c for c in caps if c["timestamp"] not in seen and not seen.add(c["timestamp"])),
                  key=lambda c: c["timestamp"])
    pages = []
    for c in caps:
        if c["timestamp"] < page.get("min_ts", "0"):
            continue
        url = f"{WB}/web/{c['timestamp']}id_/{c['original']}"
        txt = web_get(url, RAW / "wayback" / live_name / f"{c['timestamp']}.html")
        if txt:
            pages.append((c["timestamp"], f"{WB}/web/{c['timestamp']}/{c['original']}", ts_iso(c["timestamp"]), txt))
    live_url = page["live"]
    txt = web_get(live_url, RAW / "live" / f"{live_name}_{TODAY.isoformat()}.html", timeout=60)
    if txt:
        pages.append(("99999999999999", live_url, TODAY.isoformat(), txt))
    merged, conflicts = {}, []
    for ts, url, retrieved, txt in pages:
        parsed = parse(txt)
        parsed = parsed if isinstance(parsed, tuple) else (parsed,)
        for i, series in enumerate(parsed):
            m = merged.setdefault(i, {})
            for d, v in series.items():
                if d in m and abs(m[d]["v"] - v) > 1e-6:
                    conflicts.append({"series_index": i, "date": d, "old": m[d]["v"], "old_url": m[d]["url"], "new": v, "new_url": url})
                m[d] = {"v": v, "url": url, "retrieved": retrieved}
    return merged, conflicts, len(pages)


# SAFAR: values transcribed from Wayback captures of the safar.tropmet.res.in homepage marquee
# ("SHORT RANGE FORECAST ... Delhi"). Each entry is re-verified at build time: the quoted text must
# occur verbatim in the cached capture, else the entry is dropped.
#   date_basis: 'explicit'  - the date is stated in the text
#               'capture'   - text says 'today'; the bulletin carries no issue date. Date assigned as the
#                             IST date of the earliest capture of that text, minus one day if that capture
#                             was before 12:00 IST (the morning captures checked show the previous day's
#                             bulletin, e.g. 20191204040400 says 'on the 3rd December morning').
#               'capture-1' - text says 'yesterday' relative to a 'capture'-dated bulletin
SAFAR_ENTRIES = [
    # ts, date, share_pct, fire_count_in_same_bulletin, date_basis, kind, quote
    ("20191112130132", "2019-11-13", 22, None, "explicit", "forecast",
     "the share of biomass in Delhi's air quality is simulated to be 22% for 13th November"),
    ("20191120121729", None, 14, 1000, "capture", "estimate", "it will bring moderate impact of ~14% today"),
    ("20191121153955", None, 5, 1011, "capture", "estimate", "today stubble % share estimated as just 5%"),
    ("20191204040400", "2019-12-04", 8, None, "explicit", "forecast",
     "PM2.5 % contribution is predicted to touch 8% on 4th December"),
    ("20191204120350", "2019-12-05", 9, None, "explicit", "forecast",
     "Biomass fire PM2.5 % contribution is predicted to touch 9% on 5th December"),
    ("20201017175702", None, 19, 882, "capture", "estimate", "stubble contribution in PM2.5 estimated is around 19% for today"),
    ("20201020021923", None, 10, 1090, "capture", "estimate", "stubble burning share in PM2.5 is 10% for today"),
    ("20201101032911", None, 32, 3471, "capture", "estimate", "is estimated as ~32% for today"),
    ("20201101094504", None, 40, 3216, "capture", "estimate", "is estimated as ~40% for today, the highest of the season"),
    ("20201103221515", None, 10, 3068, "capture", "estimate", "is estimated as ~10% for today"),
    ("20201115200555", None, 4, 350, "capture", "estimate", "estimated as around 4 % for today"),
    ("20201117232107", None, 3, 98, "capture", "estimate", "estimated marginal ~3 % for today"),
    ("20201127120849", None, 2, 136, "capture", "estimate", "estimated as ~2 % for today"),
    ("20201202220710", None, 3, 316, "capture", "estimate", "is estimated as ~3 % for today"),
    ("20211018032033", None, 2, 815, "capture", "estimate", "the stubble contribution today will be just 2%"),
    ("20211018032033", None, 14, None, "capture-1", "estimate", "down from 14% yesterday"),
    ("20211030002421", None, 20, 1112, "capture", "estimate", "has increased to 20% today (Fire count 1112)"),
    ("20211105104001", None, 36, None, "capture", "estimate", "the share of stubble emissions has peaked today at 36%"),
    ("20211108141955", None, 30, None, "capture", "estimate", "share of crop residue burning is 30% in PM2.5"),
    ("20221029162141", None, 21, None, "capture", "estimate", "Share of stubble burning emissions in Delhi"),
    ("20221104193657", None, 30, 3068, "capture", "estimate", "Stubble emissions with a fire count of 3068 contribute 30% to PM2.5"),
]
# explicitly dated SAFAR effective/multi-satellite fire counts (no share stated for that date)
SAFAR_FIRE_ENTRIES = [
    ("20191105063046", "2019-11-04", 4962, "biomass fire counts observed on 4th Nov were highest of the season (4962)"),
    ("20191112130132", "2019-11-11", 740, "are 740 on 11th November"),
    ("20191204040400", "2019-12-02", 332, "is estimated on 02nd December as per SAFAR multi-satellite product and is 332"),
    ("20191204120350", "2019-12-03", 250, "estimated 250 counts on 03rd December"),
    ("20201013160950", "2020-10-12", 675, "SAFAR synergized fire count estimated as 675 on 12th October"),
]


# original URL of each capture (CDX 'original'); default SAFAR_HOME
SAFAR_ORIG = {
    "20191121153955": "http://safar.tropmet.res.in/?menu_id=264",
    "20191204120350": "http://safar.tropmet.res.in/index.php?menu_id=1",
    "20201020021923": "http://safar.tropmet.res.in/?menu_id=264",
    "20201117232107": "http://safar.tropmet.res.in/index.php?menu_id=1",
    "20201127120849": "http://safar.tropmet.res.in/index.php?menu_id=1",
    "20201202220710": "http://safar.tropmet.res.in/index.php?menu_id=1",
    "20211018032033": "http://safar.tropmet.res.in/?menu_id=264",
}


def capture_issue_date(ts):
    t = dt.datetime.strptime(ts, "%Y%m%d%H%M%S") + dt.timedelta(hours=5, minutes=30)
    d = t.date()
    return d - dt.timedelta(days=1) if t.hour < 12 else d


def safar_text(ts):
    txt = web_get(f"{WB}/web/{ts}id_/{SAFAR_ORIG.get(ts, SAFAR_HOME)}", RAW / "wayback" / "safar_home" / f"{ts}.html")
    if not txt:
        return ""
    txt = re.sub(r"<!--.*?-->", "", txt, flags=re.S)
    import html as _h
    txt = _h.unescape(re.sub(r"<[^>]+>", " ", txt))
    return re.sub(r"\s+", " ", txt.replace("\u2019", "'").replace("\u2018", "'"))


def build_stubble():
    sources, series, notes = [], {}, {}
    # ---- IITM DSS daily stubble share
    dss_page = {"cdx": ["ews.tropmet.res.in/dss/", "ews.tropmet.res.in/dss/index.php"],
                "live": f"{EWS}/dss/index.php", "min_ts": "20230901000000"}
    merged, conflicts, npages = collect_ews(dss_page, dss_daily_from_page, "dss_index")
    m = merged.get(0, {})
    ds = sorted(m)
    series["IITM_DSS"] = {"date": ds, "share_pct": [m[d]["v"] for d in ds], "fire_count": [None] * len(ds),
                          "url": [m[d]["url"] for d in ds], "retrieved": [m[d]["retrieved"] for d in ds]}
    notes["IITM_DSS"] = {"pages_parsed": npages, "conflicting_values_between_captures": conflicts[:50],
                         "n_conflicts": len(conflicts)}
    sources.append({
        "series": "IITM_DSS", "name": "IITM Decision Support System for Air Quality Management in Delhi",
        "page": f"{EWS}/dss/index.php",
        "field": "inline Chart.js object 'data2' - 'Daily Mean contribution from Stubble Burning to the PM2.5 "
                 "mass concentration in Delhi' (dataset label 'Biomass Burning' in 2023, 'Stubble Burning' "
                 "2024+); units % of Delhi PM2.5",
        "access": "no API; values parsed from page HTML. Past seasons from Internet Archive Wayback captures "
                  "(each value's capture URL given); current season from the live page",
        "method": "WRF-Chem based DSS; the day-0 value is the model run for the previous day; forward days in "
                  "the page's 4-day forecast (not stored here) use climatological fire emissions",
        "coverage_note": "2021-2022 DSS pages carried only hourly 5-day forecast stacks, no daily series - "
                         "not included (would require averaging = derivation)",
        "fire_count": "not published with this series (null)"})
    # ---- IITM AQEWS ob_fire_count.php (fire contribution to PM2.5, and VIIRS daily fire counts)
    ob_page = {"cdx": ["ews.tropmet.res.in/ob_fire_count.php"], "live": f"{EWS}/ob_fire_count.php",
               "min_ts": "20220901000000"}
    merged, conflicts, npages = collect_ews(ob_page, obfire_from_page, "ob_fire_count")
    m = merged.get(0, {})
    ds = sorted(m)
    series["IITM_AQEWS_fire_contribution"] = {
        "date": ds, "share_pct": [m[d]["v"] for d in ds], "fire_count": [None] * len(ds),
        "url": [m[d]["url"] for d in ds], "retrieved": [m[d]["retrieved"] for d in ds]}
    c0 = [c for c in conflicts if c["series_index"] == 0]
    c1 = [c for c in conflicts if c["series_index"] == 1]
    notes["IITM_AQEWS_fire_contribution"] = {"pages_parsed": npages, "n_conflicts": len(c0),
                                             "conflicting_values_between_captures": c0[:50]}
    notes["IITM_AQEWS_VIIRS_fire_count"] = {
        "n_conflicts": len(c1), "conflicting_values_between_captures": c1[:50],
        "note": "a count captured on the same day it refers to is partial; later captures supersede it. "
                "The last date of the newest capture/live page may still be partial"}
    v = merged.get(1, {})
    dv = sorted(v)
    series["IITM_AQEWS_VIIRS_fire_count"] = {
        "date": dv, "share_pct": [None] * len(dv), "fire_count": [int(v[d]["v"]) for d in dv],
        "url": [v[d]["url"] for d in dv], "retrieved": [v[d]["retrieved"] for d in dv]}
    sources.append({
        "series": "IITM_AQEWS_fire_contribution", "name": "IITM Air Quality Early Warning System - "
        "'Daily Mean Fire Contribution to Air Pollution in Delhi' (PM2.5 dataset)",
        "page": f"{EWS}/ob_fire_count.php",
        "field": "inline Chart.js 'data2', dataset 'PM2.5' (%); same model output as the DSS series but "
                 "published with null for missing days; kept separately, not merged",
        "access": "Wayback captures + live page"})
    sources.append({
        "series": "IITM_AQEWS_VIIRS_fire_count", "name": "IITM AQEWS daily VIIRS fire counts",
        "page": f"{EWS}/ob_fire_count.php",
        "field": "Chart.js datasets 'VIIRS YYYY' (used only where the chart's own date labels are published "
                 "with equal length); domain described by IITM as 'northern India', not state-specific",
        "caveat": "IITM notes SNPP-VIIRS unavailable since 2023-11-01 (NOAA-20/21 used); counts are not the "
                  "SAFAR 'effective fire count'"})
    # ---- SAFAR
    rows = []
    for ts, date, share, fire, basis, kind, quote in SAFAR_ENTRIES:
        text = safar_text(ts)
        if quote not in text:
            log(f"  SAFAR {ts}: quote not found, dropped")
            continue
        if basis == "capture":
            date = capture_issue_date(ts).isoformat()
        elif basis == "capture-1":
            date = (capture_issue_date(ts) - dt.timedelta(days=1)).isoformat()
        rows.append((date, share, fire, basis, kind, quote, ts))
    rows.sort(key=lambda r: (r[0], r[6]))
    # same date twice (e.g. two different bulletins mapped to one day): keep the later capture
    byd = {}
    for r in rows:
        byd[r[0]] = r
    rows = [byd[d] for d in sorted(byd)]
    series["SAFAR"] = {
        "date": [r[0] for r in rows], "share_pct": [r[1] for r in rows], "fire_count": [r[2] for r in rows],
        "date_basis": [r[3] for r in rows], "kind": [r[4] for r in rows], "quote": [r[5] for r in rows],
        "url": [f"{WB}/web/{r[6]}/{SAFAR_ORIG.get(r[6], SAFAR_HOME)}" for r in rows], "retrieved": [ts_iso(r[6]) for r in rows]}
    frows = []
    for ts, date, fire, quote in SAFAR_FIRE_ENTRIES:
        if quote in safar_text(ts):
            frows.append((date, fire, quote, ts))
    frows.sort()
    series["SAFAR_fire_count"] = {
        "date": [r[0] for r in frows], "share_pct": [None] * len(frows), "fire_count": [r[1] for r in frows],
        "quote": [r[2] for r in frows], "url": [f"{WB}/web/{r[3]}/{SAFAR_ORIG.get(r[3], SAFAR_HOME)}" for r in frows],
        "retrieved": [ts_iso(r[3]) for r in frows]}
    sources.append({
        "series": "SAFAR", "name": "SAFAR (IITM Pune) Delhi short-range forecast bulletin, homepage marquee",
        "page": SAFAR_HOME,
        "access": "no API and no bulletin archive; all values from Internet Archive captures (Sep-Dec "
                  "2018-2023, ~120 unique captures scanned). Sparse: only days where a capture exists and the "
                  "text states a %. 2018 and 2023 captures contain no stubble %",
        "date_basis": "explicit = date stated in text; capture = bulletin says 'today' but has no issue date: "
                      "IST date of capture, minus 1 day if captured before 12:00 IST (dates may be off by one); "
                      "capture-1 = 'yesterday' relative to that",
        "kind": "estimate = SAFAR's stated share for the day; forecast = share predicted for a future date",
        "fire_count": "SAFAR 'effective'/'synergized' multi-satellite fire count quoted in the same bulletin; it "
                      "usually refers to the previous day, not to 'date'",
        "retrieved": "Wayback capture timestamp given per value (retrieved field); fetched " + TODAY.isoformat()})
    sources.append({
        "series": "SAFAR_fire_count", "name": "SAFAR effective fire counts with an explicit date in the text",
        "page": SAFAR_HOME})
    sources.append({"not_used": [
        "data.gov.in: catalog searches ('stubble','SAFAR','crop residue','air quality Delhi','biomass burning') "
        "return only Rajya Sabha answer tables with seasonal/state totals of burning events - no daily share",
        "PIB / CAQM press releases: occasional single peak values (e.g. season maxima) - not a daily series; "
        "not included",
        "DSS 2021-2022: hourly 5-day forecast stacks only (no daily series)",
        "news reports quoting SAFAR: secondary sources, not included"]})
    out = {"sources": sources, "methodology_caveats": [
        "SAFAR (2018-2023) and IITM DSS (2023+) are different models and fire-emission inputs; SAFAR % is a "
        "bulletin estimate (often rounded, '~'), DSS is a daily mean from WRF-Chem source tagging - do not "
        "splice them into one series without noting the break",
        "DSS 'Biomass Burning' (2023) vs 'Stubble Burning' (2024+) labels refer to the same chart",
        "values are model attributions, not measurements"],
        "build_notes": notes, "retrieved": TODAY.isoformat(), "series": series}
    OUT_STUBBLE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    for k, s in series.items():
        yrs = defaultdict(int)
        for d in s["date"]:
            yrs[d[:4]] += 1
        log(f"{k}: {len(s['date'])} values {dict(yrs)}")
    log(f"wrote {OUT_STUBBLE}")

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("aq", "all"):
        build_aq()
    if what in ("stubble", "all"):
        build_stubble()
