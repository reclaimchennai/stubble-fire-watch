#!/usr/bin/env python3
"""Data for the /timeline page: the season since 1 Sep, every 3 hours, over the subcontinent and its rim.

Region 58-102 E, 2-44 N (the subcontinent plus parts of Central Asia, Tibet and western China).

  smoke + wind  NASA GMAO GEOS-FP seamless: OC + BC + brown-carbon AOD (tavg3_2d_aer_Nx, 3-hourly means centred on
                HH:30 UTC) and the 10 m wind at the same hour (tavg1_2d_slv_Nx). Cached per UTC day in
                data/raw/timeline/geos/; past days are kept, the last two are refetched every run. Packed into 8-day
                RGB atlases (R = smoke AOD, sqrt-scaled; G, B = u, v), one tile per frame.
  fires         NASA FIRMS area API for this box (MODIS Terra/Aqua, VIIRS S-NPP, NOAA-20, NOAA-21), CREAMS bulletin
                events, Meteosat SEVIRI FRP-PIXEL detections and INSAT-3DS L2P fire pixels (MOSDAC KML files collected by the
                Talaash project; gallery-map cells for days it lacks). India's static FIRMS cells are
                dropped; elsewhere, 0.02-degree cells seen on 20 or more days (gas flares, kilns) are dropped.
                8-byte records per month: u16 lon, u16 lat (over the box), u16 time (4-minute steps from the season
                start, 00:00 UTC 1 Sep), u8 sensor, u8 log-scaled FRP.
  AQI           CPCB's daily National AQI bulletin (24-hour average to 16:00 IST, city level), one PDF per day.

Outputs in data/timeline/: meta.json, aer_<k>.png, fires_<YYYY-MM>.bin, aqi.json.
"""
import csv
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_smoke as fs  # noqa: E402  (GEOS-FP helpers: run listing, time axis, OPeNDAP ascii parser)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "timeline"
OUT = ROOT / "data" / "timeline"
LON0, LON1, LAT0, LAT1 = 58.0, 102.0, 2.0, 44.0
DLON, DLAT = fs.DLON, fs.DLAT
AOD_MAX, WIND_MAX = 2.0, 20.0
CHUNK_DAYS = 8
TSTEP = 240                       # seconds per fire time unit (4 min)
SENSORS = ["MODIS Terra", "MODIS Aqua", "VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21", "CREAMS events", "Meteosat", "INSAT-3DS"]
FIRMS_SRC = {"MODIS": None, "VIIRS_SNPP": 2, "VIIRS_NOAA20": 3, "VIIRS_NOAA21": 4}
CITIES = [("Amritsar", "Amritsar"), ("Chandigarh", "Chandigarh"), ("Delhi-NCR", "Delhi"), ("Lucknow", "Lucknow"),
          ("Kolkata", "Kolkata"), ("Bhubaneswar", "Bhubaneswar"), ("Visakhapatnam", "Visakhapatnam"),
          ("Chennai", "Chennai"), ("Bengaluru", "Bengaluru")]


def season():
    """(start, end) of the season shown: 1 Sep of this season's year to now, at most 31 Jan."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    y = now.year if now.month >= 9 else now.year - 1
    return dt.datetime(y, 9, 1), min(now, dt.datetime(y + 1, 2, 1))


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


# ---------------------------------------------------------------- GEOS-FP smoke + wind
def grid_idx():
    xi = (round((LON0 + 180) / DLON), round((LON1 + 180) / DLON))
    yi = (round((LAT0 + 90) / DLAT), round((LAT1 + 90) / DLAT))
    return xi, yi


def fetch_block(coll, run, var, a, b, step, yi, xi):
    url = f"{fs.BASE}/{coll}/{coll}.{run}.ascii?{var}[{a}:{step}:{b}][{yi[0]}:{yi[1]}][{xi[0]}:{xi[1]}]"
    want = (b - a) // step + 1
    for i in range(5):
        try:
            arr = fs.parse_ascii(fs.get(url), want, yi[1] - yi[0] + 1, xi[1] - xi[0] + 1)
            if len(arr) == want:
                return arr
            raise ValueError(f"short: {len(arr)} of {want}")
        except Exception as e:
            print(f"  {var} {a}-{b}: {e}; retry", file=sys.stderr)
            time.sleep(6 * (i + 1))
    return None


def geos_days(t0, t1):
    """{date: (smoke[8,h,w], u[8,h,w], v[8,h,w])} for every UTC day from t0 up to the last available frame."""
    cdir = RAW / "geos"
    cdir.mkdir(parents=True, exist_ok=True)
    xi, yi = grid_idx()
    aer_run, a_t0, a_t1 = fs.pick_run("tavg3_2d_aer_Nx")
    slv_runs = fs.recent_runs("tavg1_2d_slv_Nx", 8)
    slv_run = aer_run if aer_run in slv_runs else slv_runs[-1]
    s_t0, s_t1 = fs.time_axis("tavg1_2d_slv_Nx", slv_run)
    run_time = dt.datetime.strptime(aer_run, "%Y%m%d_%H")
    last = min(a_t1, t1, s_t1)
    days, d = [], t0.date()
    while dt.datetime.combine(d, dt.time(1, 30)) <= last:
        days.append(d)
        d += dt.timedelta(days=1)

    def one(day):
        p = cdir / f"{day:%Y%m%d}.npz"
        settled = dt.datetime.combine(day, dt.time()) + dt.timedelta(days=2) <= run_time
        if p.exists() and settled:
            z = np.load(p)
            return day, (z["smoke"], z["u"], z["v"])
        c0 = dt.datetime.combine(day, dt.time(1, 30))
        a = round((c0 - a_t0).total_seconds() / 10800)
        b = min(a + 7, int((a_t1 - a_t0).total_seconds() // 10800))
        parts = [fetch_block("tavg3_2d_aer_Nx", aer_run, v, a, b, 1, yi, xi) for v in ("ocexttau", "bcexttau", "brexttau")]
        wa = round((c0 - s_t0).total_seconds() / 3600)
        wb = min(wa + 21, int((s_t1 - s_t0).total_seconds() // 3600))
        wb -= (wb - wa) % 3
        u = fetch_block("tavg1_2d_slv_Nx", slv_run, "u10m", wa, wb, 3, yi, xi)
        v = fetch_block("tavg1_2d_slv_Nx", slv_run, "v10m", wa, wb, 3, yi, xi)
        if any(x is None for x in parts) or u is None or v is None:
            print(f"  {day}: incomplete, skipped", file=sys.stderr)
            return day, None
        n = min(len(parts[0]), len(u))
        smoke = sum(x[:n] for x in parts)
        smoke[smoke > 100] = np.nan                    # GEOS fill value 1e15
        u, v = u[:n], v[:n]
        u[np.abs(u) > 1e3] = np.nan
        v[np.abs(v) > 1e3] = np.nan
        for i in range(n):                             # a run can hand back all-zero frames: mark them missing
            if np.nanmax(smoke[i]) < 1e-3:
                smoke[i] = np.nan
        if n == 8 and settled:
            np.savez_compressed(p, smoke=smoke.astype(np.float32), u=u.astype(np.float32), v=v.astype(np.float32))
        print(f"  geos {day}: {n} frames", file=sys.stderr)
        return day, (smoke, u, v)

    with ThreadPoolExecutor(4) as ex:
        got = dict(ex.map(one, days))
    bounds = [xi[0] * DLON - 180 - DLON / 2, yi[0] * DLAT - 90 - DLAT / 2, xi[1] * DLON - 180 + DLON / 2, yi[1] * DLAT - 90 + DLAT / 2]
    return got, bounds, run_time


def pack_atlases(days, t0):
    """Frames in time order, gaps filled by linear interpolation; one 8-day RGB atlas per chunk."""
    order = sorted(d for d, v in days.items() if v is not None)
    if not order:
        return [], 0, 0, []
    first = t0.date()
    nday = (order[-1] - first).days + 1
    h, w = days[order[0]][0].shape[1:]
    S = np.full((nday * 8, h, w), np.nan, np.float32)
    U, V = S.copy(), S.copy()
    for d in order:
        s, u, v = days[d]
        k = (d - first).days * 8
        S[k:k + len(s)], U[k:k + len(u)], V[k:k + len(v)] = s, u, v
    valid = [i for i in range(len(S)) if not np.isnan(S[i]).all()]
    n = valid[-1] + 1
    S, U, V = S[:n], U[:n], V[:n]
    filled = []
    for A in (S, U, V):
        ok = [i for i in range(n) if not np.isnan(A[i]).all()]
        for i in range(n):
            if np.isnan(A[i]).all():
                lo = max((j for j in ok if j < i), default=None)
                hi = min((j for j in ok if j > i), default=None)
                if lo is None or hi is None:
                    A[i] = A[lo if lo is not None else hi]
                else:
                    wt = (i - lo) / (hi - lo)
                    A[i] = A[lo] * (1 - wt) + A[hi] * wt
                if A is S:
                    filled.append(i)
            A[i] = np.nan_to_num(A[i])
    enc_s = lambda f: (np.sqrt(np.clip(f, 0, AOD_MAX) / AOD_MAX) * 255).round().astype(np.uint8)
    enc_w = lambda f: ((np.clip(f, -WIND_MAX, WIND_MAX) + WIND_MAX) / (2 * WIND_MAX) * 255).round().astype(np.uint8)
    per = CHUNK_DAYS * 8
    cols = 8
    chunks = []
    for c in range(math.ceil(n / per)):
        a, b = c * per, min(n, (c + 1) * per)
        rows = math.ceil((b - a) / cols)
        img = np.zeros((rows * h, cols * w, 3), np.uint8)
        for i in range(a, b):
            r, k = divmod(i - a, cols)
            img[r * h:(r + 1) * h, k * w:(k + 1) * w] = np.stack([enc_s(S[i]), enc_w(U[i]), enc_w(V[i])], -1)[::-1]
        name = f"aer_{c:02d}.png"
        tmp = OUT / f".{name}.tmp.png"
        Image.fromarray(img, "RGB").save(tmp, optimize=True)
        os.replace(tmp, OUT / name)
        chunks.append({"file": name, "f0": a, "n": b - a, "rows": rows})
    return chunks, w, h, filled


# ---------------------------------------------------------------- fires
def firms_rows(t0, t1):
    load_env()
    key = os.environ.get("NASA_FIRMS_MAP_KEY")
    if not key:
        print("no NASA_FIRMS_MAP_KEY: FIRMS skipped", file=sys.stderr)
        return []
    fdir = RAW / "firms"
    fdir.mkdir(parents=True, exist_ok=True)
    url = f"https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
    avail = {r["data_id"]: (dt.date.fromisoformat(r["min_date"]), dt.date.fromisoformat(r["max_date"]))
             for r in csv.DictReader(urllib.request.urlopen(url, timeout=60).read().decode().splitlines())}
    bbox = f"{LON0:g},{LAT0:g},{LON1:g},{LAT1:g}"
    today = dt.date.today()
    rows = []
    for sensor, code in FIRMS_SRC.items():
        for kind in ("SP", "NRT"):
            src = f"{sensor}_{kind}"
            if src not in avail:
                continue
            lo, hi = max(avail[src][0], t0.date()), min(avail[src][1], t1.date())
            if kind == "NRT" and f"{sensor}_SP" in avail:
                lo = max(lo, avail[f"{sensor}_SP"][1] + dt.timedelta(days=1))
            d = lo
            while d <= hi:
                n = min(5, (hi - d).days + 1)
                p = fdir / f"{src}_{d}_{n}.csv"
                settled = (today - (d + dt.timedelta(days=n))).days > 3
                if not (p.exists() and settled and p.stat().st_size > 0):
                    u = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{n}/{d}"
                    for i in range(4):
                        try:
                            text = urllib.request.urlopen(u, timeout=180).read().decode()
                            if text.startswith("Invalid") or "exceeded" in text[:200]:
                                raise RuntimeError(text[:120])
                            p.write_text(text)
                            print(f"  firms {src} {d} +{n}: {text.count(chr(10)) - 1}", file=sys.stderr)
                            break
                        except Exception as e:
                            print(f"  firms {src} {d}: {e}; retry", file=sys.stderr)
                            time.sleep(20 * (i + 1))
                    time.sleep(0.4)
                if p.exists():
                    for r in csv.DictReader(p.read_text().splitlines()):
                        try:
                            s = code if code is not None else (0 if r.get("satellite") in ("T", "Terra") else 1)
                            hhmm = int(r["acq_time"])
                            t = dt.datetime.fromisoformat(r["acq_date"]) + dt.timedelta(hours=hhmm // 100, minutes=hhmm % 100)
                            rows.append((float(r["longitude"]), float(r["latitude"]), t, s, float(r.get("frp") or 0)))
                        except (KeyError, ValueError):
                            continue
                d += dt.timedelta(days=n)
    return rows


def static_filter(rows):
    """Drop FIRMS-flagged static cells (India/Pakistan archive) and, elsewhere, cells that burn on 20+ days."""
    sp = ROOT / "data" / "raw" / "firms_static_cells.json"
    static = {tuple(c) for c in json.loads(sp.read_text())["cells"]} if sp.exists() else set()
    days = defaultdict(set)
    for lon, lat, t, s, _ in rows:
        if s <= 4:
            days[(round(lon / 0.02), round(lat / 0.02))].add(t.date())
    hot = {k for k, v in days.items() if len(v) >= 20}
    india = india_test()
    out, drop = [], defaultdict(int)
    for r in rows:
        lon, lat, t, s, frp = r
        if s <= 4:
            if (round(lon / 0.01), round(lat / 0.01)) in static:
                drop["static"] += 1
                continue
            if (round(lon / 0.02), round(lat / 0.02)) in hot and not india(lon, lat):
                drop["persistent, Pakistani Punjab box" if 69 < lon < 75.5 and 28 < lat < 33 else "persistent, elsewhere"] += 1
                continue
        out.append(r)
    print(f"  fires dropped: {dict(drop)}; kept {len(out)} of {len(rows)}", file=sys.stderr)
    return out


def geo_filter(rows):
    """Geostationary pixels: drop 0.04-degree cells on FIRMS's static list or seen on 40+ days (industry, flares)."""
    sp = ROOT / "data" / "raw" / "firms_static_cells.json"
    static4 = {(round(c[0] * 0.01 / 0.04), round(c[1] * 0.01 / 0.04)) for c in json.loads(sp.read_text())["cells"]} if sp.exists() else set()
    days = defaultdict(set)
    for lon, lat, t, s, _ in rows:
        days[(s, round(lon / 0.04), round(lat / 0.04))].add(t.date())
    out = [r for r in rows if (round(r[0] / 0.04), round(r[1] / 0.04)) not in static4 and len(days[(r[3], round(r[0] / 0.04), round(r[1] / 0.04))]) < 40]
    print(f"  geostationary pixels: kept {len(out)} of {len(rows)}", file=sys.stderr)
    return out


def india_test():
    from shapely.geometry import Point, shape
    from shapely.prepared import prep
    try:
        g = json.loads((ROOT / "data" / "geo" / "countries.geojson").read_text())
        polys = [shape(f["geometry"]) for f in g["features"] if f["properties"].get("name") == "India"]
    except Exception:
        polys = []
    if not polys:
        return lambda lon, lat: 68 <= lon <= 97.5 and 6 <= lat <= 37
    from shapely.ops import unary_union
    pp = prep(unary_union(polys))
    return lambda lon, lat: pp.contains(Point(lon, lat))


def creams_rows(t0, t1):
    p = ROOT / "data" / "creams" / "creams_events.csv"
    rows = []
    if not p.exists():
        return rows
    for r in csv.DictReader(open(p, newline="")):
        try:
            hh, mm = (r.get("acq_time") or "00:00").split(":")[:2]
            t = dt.datetime.fromisoformat(r["date"]) + dt.timedelta(hours=int(hh), minutes=int(mm)) - dt.timedelta(hours=5, minutes=30)
            if t0 <= t <= t1:
                rows.append((float(r["longitude"]), float(r["latitude"]), t, 5, 0.0))
        except (KeyError, ValueError):
            continue
    return rows


def meteosat_rows(t0, t1):
    p = ROOT / "data" / "raw" / "geostationary" / str(t0.year) / "fires.csv"
    rows = []
    if p.exists():
        for r in csv.DictReader(open(p, newline="")):
            try:
                t = dt.datetime.fromisoformat(r["slot_utc"])
                if t0 <= t <= t1:
                    rows.append((float(r["lon"]), float(r["lat"]), t, 6, float(r.get("frp") or 0)))
            except (KeyError, ValueError):
                continue
    return rows


TALAASH = Path.home() / "svr" / "projects" / "talaash" / "mosdac_sat" / "shared" / "fire" / "3SIMG_L2P_FIR"


def insat_rows(t0, t1):
    """INSAT-3DS L2P fire pixels from MOSDAC's KML files (collected by the Talaash project, one CSV per UTC day);
    days it lacks fall back to the cells read off the open gallery maps."""
    rows, have = [], set()
    if TALAASH.is_dir():
        for f in sorted(TALAASH.glob("????-??-??.csv")):
            d = dt.date.fromisoformat(f.stem)
            if not (t0.date() <= d <= t1.date()):
                continue
            have.add(d)
            for r in csv.DictReader(open(f, newline="")):
                try:
                    t = dt.datetime.fromisoformat((r.get("obs_time_utc") or r["slot_utc"]).replace("Z", "")[:19])
                    rows.append((float(r["lon"]), float(r["lat"]), t, 7, 0.0))
                except (KeyError, ValueError):
                    continue
    p = ROOT / "data" / "geostationary" / f"insat3ds_cells_{t0.year}.json"
    if p.exists():
        j = json.loads(p.read_text())
        base = dt.datetime.fromisoformat(j["season_start_ist"]) - dt.timedelta(hours=5, minutes=30)
        for c in j["cells"]:
            t = base + dt.timedelta(minutes=c[2])
            if t0 <= t <= t1 and t.date() not in have:
                rows.append((c[0], c[1], t, 7, 0.0))
    return rows


def write_fires(rows, t0):
    by_month = defaultdict(list)
    for lon, lat, t, s, frp in rows:
        if not (LON0 <= lon < LON1 and LAT0 <= lat < LAT1) or t < t0:
            continue
        by_month[t.strftime("%Y-%m")].append((t, lon, lat, s, frp))
    files = []
    for old in OUT.glob("fires_*.bin"):
        if old.stem[6:] not in by_month:
            old.unlink()
    for m in sorted(by_month):
        recs = sorted(by_month[m])
        a = np.zeros(len(recs), dtype=[("x", "<u2"), ("y", "<u2"), ("t", "<u2"), ("s", "u1"), ("f", "u1")])
        a["x"] = [round((r[1] - LON0) / (LON1 - LON0) * 65535) for r in recs]
        a["y"] = [round((r[2] - LAT0) / (LAT1 - LAT0) * 65535) for r in recs]
        a["t"] = [min(65535, int((r[0] - t0).total_seconds() // TSTEP)) for r in recs]
        a["s"] = [r[3] for r in recs]
        a["f"] = [min(255, round(math.log2(1 + max(r[4], 0)) * 25)) for r in recs]
        name = f"fires_{m}.bin"
        tmp = OUT / f".{name}.tmp"
        a.tofile(tmp)
        os.replace(tmp, OUT / name)
        counts = [0] * len(SENSORS)
        for r in recs:
            counts[r[3]] += 1
        files.append({"file": name, "month": m, "n": len(recs), "by_sensor": counts})
    return files


# ---------------------------------------------------------------- fires by state, per IST day
STATES = [("Punjab", "India", "Punjab"), ("Haryana", "India", "Haryana"), ("Delhi", "India", "Delhi"),
          ("Uttar Pradesh", "India", "Uttar Pradesh"), ("Rajasthan", "India", "Rajasthan"),
          ("Madhya Pradesh", "India", "Madhya Pradesh"), ("Punjab (PK)", "Pakistan", "Punjab")]


def state_counts(rows, t0, t1):
    """Per state and IST day: VIIRS and MODIS detections, Meteosat and INSAT-3DS fire cells (0.04 degree, unique per
    day), and the all-satellite index (scripts/build_fire_index.py) where it exists."""
    from shapely.geometry import Point, shape
    from shapely.prepared import prep
    g = json.loads((ROOT / "data" / "geo" / "states.geojson").read_text())
    polys = []
    for key, country, name in STATES:
        f = next((f for f in g["features"] if f["properties"].get("country") == country and f["properties"]["name"] == name), None)
        if f:
            sh = shape(f["geometry"])
            polys.append((key, sh.bounds, prep(sh)))
    ist = dt.timedelta(hours=5, minutes=30)
    d0 = (t0 + ist).date()
    nd = ((t1 + ist).date() - d0).days + 1
    keys = ["viirs", "modis", "msg", "insat"]
    out = {k: {c: [0] * nd for c in keys} for k, _, _ in polys}
    cells = defaultdict(set)
    for lon, lat, t, s, _ in rows:
        if s == 5:
            continue
        hit = next((k for k, (x0, y0, x1, y1), pp in polys if x0 <= lon <= x1 and y0 <= lat <= y1 and pp.contains(Point(lon, lat))), None)
        if hit is None:
            continue
        i = ((t + ist).date() - d0).days
        if not 0 <= i < nd:
            continue
        if s <= 1:
            out[hit]["modis"][i] += 1
        elif s <= 4:
            out[hit]["viirs"][i] += 1
        else:
            c = "msg" if s == 6 else "insat"
            cl = (c, hit, i, round(lon / 0.04), round(lat / 0.04))
            if cl not in cells[c]:
                cells[c].add(cl)
                out[hit][c][i] += 1
    fi = ROOT / "data" / "db" / "extras" / "fire_index.json"
    idx = {}
    if fi.exists():
        y = json.loads(fi.read_text())["years"].get(str(t0.year))
        if y:
            f0 = dt.date.fromisoformat(y["first_utc_date"])
            for k, _, _ in polys:
                v = y["states"].get(k)
                if v:
                    arr = [None] * nd
                    for j, (a, b) in enumerate(zip(v.get("base", v.get("snpp")), v["added"])):
                        i = (f0 + dt.timedelta(days=j) - d0).days
                        if 0 <= i < nd:
                            arr[i] = round(a + b)
                    idx[k] = arr
    return {"first_ist_date": d0.isoformat(), "days": nd,
            "states": [{"name": k, **out[k], "index": idx.get(k)} for k, _, _ in polys]}


# ---------------------------------------------------------------- CPCB AQI bulletin
ROW = re.compile(r"^\s*\d+\s+(.+?)\s+(Good|Satisfactory|Moderate|Very Poor|Poor|Severe)\s+(\d+)\s+(.+?)\s+(\d+)\s*/\s*(\d+)\s*$")


def aqi_days(t0, t1):
    adir = RAW / "aqi"
    adir.mkdir(parents=True, exist_ok=True)
    out, d = {}, t0.date()
    ist_now = t1 + dt.timedelta(hours=5, minutes=30)
    while d <= ist_now.date():
        p = adir / f"{d:%Y%m%d}.json"
        if p.exists():
            out[d.isoformat()] = json.loads(p.read_text())
        elif d < ist_now.date() or ist_now.hour >= 17:
            pdf = adir / f"{d:%Y%m%d}.pdf"
            try:
                req = urllib.request.Request(f"https://cpcb.gov.in/upload/Downloads/AQI_Bulletin_{d:%Y%m%d}.pdf", headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=90).read()
                if data[:4] != b"%PDF":
                    raise ValueError("not a PDF")
                pdf.write_bytes(data)
                text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
                rows = {}
                for line in text.splitlines():
                    m = ROW.match(line)
                    if m:
                        rows[m.group(1).strip().lower()] = {"cat": m.group(2), "aqi": int(m.group(3)), "prom": m.group(4).strip(),
                                                            "st": [int(m.group(5)), int(m.group(6))]}
                pdf.unlink()
                if len(rows) > 50:
                    p.write_text(json.dumps(rows, separators=(",", ":")))
                    out[d.isoformat()] = rows
                    print(f"  aqi {d}: {len(rows)} cities", file=sys.stderr)
            except Exception as e:
                print(f"  aqi {d}: {e}", file=sys.stderr)
                pdf.unlink(missing_ok=True)
        d += dt.timedelta(days=1)
    dates = sorted(out)
    cities = []
    for label, key in CITIES:
        k = key.lower()
        cities.append({"name": label, "bulletin_name": key,
                       "aqi": [out[d].get(k, {}).get("aqi") for d in dates],
                       "cat": [out[d].get(k, {}).get("cat") for d in dates],
                       "prom": [out[d].get(k, {}).get("prom") for d in dates]})
    return {"source": "CPCB National Air Quality Index daily bulletin (24-h average to 16:00 IST)",
            "url": "https://cpcb.nic.in/AQI_Bulletin.php", "dates": dates, "cities": cities}


# ---------------------------------------------------------------- main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    t0, t1 = season()
    print(f"timeline {t0:%Y-%m-%d} .. {t1:%Y-%m-%d %H:%M}", file=sys.stderr)
    days, bounds, run_time = geos_days(t0, t1)
    chunks, w, h, filled = pack_atlases(days, t0)
    rows = firms_rows(t0, t1)
    n_firms = len(rows)
    rows = static_filter(rows) + creams_rows(t0, t1) + geo_filter(meteosat_rows(t0, t1) + insat_rows(t0, t1))
    fires = write_fires(rows, t0)
    sc = state_counts(rows, t0, t1)
    tmp = OUT / ".states.json.tmp"
    tmp.write_text(json.dumps(sc, separators=(",", ":")))
    os.replace(tmp, OUT / "states.json")
    aqi = aqi_days(t0, t1)
    tmp = OUT / ".aqi.json.tmp"
    tmp.write_text(json.dumps(aqi, separators=(",", ":")))
    os.replace(tmp, OUT / "aqi.json")
    nframes = sum(c["n"] for c in chunks)
    meta = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "start": t0.strftime("%Y-%m-%dT%H:%MZ"),
        "end": t1.strftime("%Y-%m-%dT%H:%MZ"),
        "region": [LON0, LAT0, LON1, LAT1],
        "aer": {"source": "NASA GMAO GEOS-FP tavg3_2d_aer_Nx (OC + BC + brown carbon AOD) + tavg1_2d_slv_Nx (10 m wind), seamless",
                "run": run_time.strftime("%Y-%m-%dT%H:%MZ"), "bounds": bounds, "tile_w": w, "tile_h": h, "cols": 8,
                "first_frame": (t0 + dt.timedelta(hours=1, minutes=30)).strftime("%Y-%m-%dT%H:%MZ"), "step_h": 3,
                "frames": nframes, "chunks": chunks, "interpolated_frames": filled,
                "aod_max": AOD_MAX, "aod_encoding": "R: aod = (px/255)^2 * aod_max",
                "wind_max": WIND_MAX, "wind_encoding": "G, B: m/s = px/255 * 2*wind_max - wind_max"},
        "fires": {"sensors": SENSORS, "tstep_s": TSTEP, "files": fires, "firms_rows": n_firms,
                  "record": "u16 lon, u16 lat (scaled over region), u16 time (tstep_s from start), u8 sensor, u8 frp = log2(1+MW)*25"},
        "states": {"file": "states.json"},
        "aqi": {"file": "aqi.json", "days": len(aqi["dates"]), "last": aqi["dates"][-1] if aqi["dates"] else None},
    }
    tmp = OUT / ".meta.json.tmp"
    tmp.write_text(json.dumps(meta, separators=(",", ":")))
    os.replace(tmp, OUT / "meta.json")
    print(f"frames {nframes} in {len(chunks)} chunks; fires {sum(f['n'] for f in fires)}; aqi days {len(aqi['dates'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
