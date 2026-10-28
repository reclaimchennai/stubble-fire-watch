#!/usr/bin/env python3
"""Back-trajectories from south and east Indian cities: does air arriving there pass over north Indian farm fires?

Winds: NASA GMAO GEOS-FP assimilation, tavg1_2d_slv_Nx U850/V850 (hourly, 0.3125 x 0.25 deg, Dec 2017 onward,
keyless OPeNDAP), sampled every 3 h on a 0.625 x 0.5 deg grid over 60-100E, 0-40N, 15 Sep - 31 Dec of each year.
850 hPa (about 1.5 km) is the usual level for regional smoke transport; it sits at or just above the winter boundary
layer, so these are 2-D isobaric trajectories, not a dispersion model (no vertical motion, no mixing, no deposition).

Fires: NASA FIRMS VIIRS S-NPP detections (India + Pakistan country files, API for the current year), type 0
(vegetation) only, counted per day on a 0.5 deg grid.

For every receptor and day, four trajectories (00, 06, 12, 18 UTC arrivals) run 120 h backward with RK2 and a 15-min
step. Along each, hourly positions pick up
  fire_all   detections within the 0.5 deg cell, the same day and the day before (all of the domain)
  fire_nw    the same, only inside the Punjab-Haryana-Delhi box (73.5-78.5E, 27.5-32.5N)
  igp        hours spent over the Indo-Gangetic Plain box (74-88E, 24-31N)
  nw         hours spent over the Punjab-Haryana-Delhi box
  sea        hours over the Bay of Bengal / Arabian Sea (the GEOS-FP land fraction is not fetched: a lon/lat test)

  transport.py winds [YEAR ...]   fetch/cache winds (data/raw/winds/)
  transport.py run                trajectories + exposure -> data/db/extras/transport.json
"""
import csv
import datetime as dt
import glob
import json
import math
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "raw" / "winds"
OUT = ROOT / "data" / "db" / "extras" / "transport.json"
BASE = "https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/assim/tavg1_2d_slv_Nx"
T0 = dt.datetime(2017, 12, 1, 0, 30)          # first time step of the collection
LON0, LON1, LAT0, LAT1, STRIDE = 60.0, 100.0, 0.0, 40.0, 2
XI = (round((LON0 + 180) / 0.3125), round((LON1 + 180) / 0.3125))
YI = (round((LAT0 + 90) / 0.25), round((LAT1 + 90) / 0.25))
LONS = np.arange(XI[0], XI[1] + 1, STRIDE) * 0.3125 - 180
LATS = np.arange(YI[0], YI[1] + 1, STRIDE) * 0.25 - 90
SEASON = ((9, 15), (12, 31))
HOURS_BACK = 120
RECEPTORS = {
    "Chennai": (13.08, 80.27), "Puducherry": (11.93, 79.83), "Bengaluru": (12.97, 77.59),
    "Hyderabad": (17.39, 78.49), "Vijayawada": (16.51, 80.65), "Visakhapatnam": (17.69, 83.22), "Bhubaneswar": (20.30, 85.82),
    "Kolkata": (22.57, 88.36), "Delhi": (28.61, 77.21),
}
NW_BOX = (73.5, 27.5, 78.5, 32.5)
IGP_BOX = (74.0, 24.0, 88.0, 31.0)
FG = 0.5                                       # fire grid (deg)
FW, FS, FE, FN = 66.0, 6.0, 98.0, 37.5


def get(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fire-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read().decode()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(10 * (i + 1))


def parse(text, ny, nx):
    vals = []
    for line in text.splitlines():
        if re.match(r"^\[\d+\]\[\d+\]", line):
            vals.extend(float(v) for v in line.split(",")[1:] if v.strip())
    a = np.array(vals, np.float32)
    assert a.size and a.size % (ny * nx) == 0, (a.size, ny, nx)
    return a.reshape(-1, ny, nx)


def available_until():
    """Index of the newest time step the collection holds (its .info page: '... to HH:MMZDDMONYYYY')."""
    info = re.sub(r"<[^>]+>", " ", get(f"{BASE}.info"))
    m = re.search(r"to (\d\d):(\d\d)Z(\d\d)([A-Z]{3})(\d{4})", info)
    end = dt.datetime.strptime(f"{m.group(5)}{m.group(4)}{m.group(3)}{m.group(1)}{m.group(2)}", "%Y%b%d%H%M")
    return int((end - T0).total_seconds() // 3600)


def season_steps(year):
    a = dt.datetime(year, *SEASON[0]) - dt.timedelta(hours=HOURS_BACK + 24)
    b = dt.datetime(year, *SEASON[1], 23, 30)
    i0 = max(0, math.ceil((a - T0).total_seconds() / 3600 / 3) * 3)
    i1 = int((b - T0).total_seconds() // 3600)
    return i0, i1


def fetch_year(year):
    """u850, v850 every 3 h for the season (+5 days of spin-up before it), cached per 6-day chunk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    i0, i1 = season_steps(year)
    i1 = min(i1, available_until())
    i1 -= (i1 - i0) % 3                        # stay on the 3-hourly sampling
    ny, nx = len(LATS), len(LONS)
    chunk = 48 * 3

    def one(args):
        var, a = args
        b = min(i1, a + chunk - 3)
        f = CACHE / f"{var}_{a}_{b}_{XI[0]}_{YI[0]}_{STRIDE}.npy"
        want = (b - a) // 3 + 1
        if f.exists():
            return
        for k in range(5):
            try:
                arr = parse(get(f"{BASE}.ascii?{var}[{a}:3:{b}][{YI[0]}:{STRIDE}:{YI[1]}][{XI[0]}:{STRIDE}:{XI[1]}]"), ny, nx)
                if len(arr) != want:
                    raise ValueError(f"short {len(arr)}/{want}")
                bad = np.abs(arr) > 500           # 850 hPa is below ground over Tibet: GEOS fill value 1e15
                if bad.all(axis=(1, 2)).any():
                    raise ValueError("whole frame missing")
                arr[bad] = np.nan
                np.save(f, arr)
                print(f"  {year} {var} {a}-{b}", file=sys.stderr)
                return
            except (AssertionError, ValueError) as e:
                print(f"  bad {var} {a}-{b}: {e}", file=sys.stderr)
                time.sleep(10 * (k + 1))
        print(f"  FAILED {var} {a}-{b}", file=sys.stderr)

    jobs = [(v, a) for v in ("u850", "v850") for a in range(i0, i1 + 1, chunk)]
    with ThreadPoolExecutor(8) as ex:              # the server opens one file per hour of data: latency-bound
        list(ex.map(one, jobs))


def load_year(year):
    i0, i1 = season_steps(year)
    out = {}
    for var in ("u850", "v850"):
        parts = []
        for f in sorted(CACHE.glob(f"{var}_*_{XI[0]}_{YI[0]}_{STRIDE}.npy"), key=lambda p: int(p.name.split("_")[1])):
            a, b = int(f.name.split("_")[1]), int(f.name.split("_")[2])
            if a >= i0 and a <= i1:
                parts.append((a, np.load(f)))
        if not parts:
            return None
        start = parts[0][0]
        n = (parts[-1][0] - start) // 3 + len(parts[-1][1])
        arr = np.full((n, len(LATS), len(LONS)), np.nan, np.float32)
        for a, p in parts:
            k = (a - start) // 3
            arr[k:k + len(p)] = p
        out[var] = arr
    t_first = T0 + dt.timedelta(hours=start)
    return t_first, out["u850"], out["v850"]


def fire_grid(year):
    """Daily VIIRS S-NPP vegetation-fire counts on a 0.5 deg grid, {date: array}, Sep 1 - Dec 31."""
    nx, ny = round((FE - FW) / FG), round((FN - FS) / FG)
    days = {}
    files = glob.glob(str(ROOT / "data" / "raw" / "firms_country" / f"viirs-snpp_{year}_*.csv"))
    if not files:
        files = glob.glob(str(ROOT / "data" / "raw" / "firms_api" / f"VIIRS_SNPP_*_{year}-*.csv"))
    seen = set()
    for fpath in files:
        with open(fpath, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("type", "0") not in ("0", ""):
                    continue
                d = r["acq_date"]
                if d[5:7] < "09":
                    continue
                k = (r["latitude"], r["longitude"], d, r["acq_time"])
                if k in seen:
                    continue
                seen.add(k)
                lat, lon = float(r["latitude"]), float(r["longitude"])
                if not (FW <= lon < FE and FS <= lat < FN):
                    continue
                g = days.setdefault(d, np.zeros((ny, nx), np.float32))
                g[int((lat - FS) / FG), int((lon - FW) / FG)] += 1
    return days


def interp(field, t, lat, lon):
    """Vectorised bilinear-in-space, linear-in-time lookup; field[t, y, x] on LATS/LONS, t = fractional frame index.
    NaN where a parcel is outside the grid or the time axis (or the level is below ground)."""
    x = np.nan_to_num((lon - LONS[0]) / (LONS[1] - LONS[0]), nan=-1.0)   # NaN parcels (stopped) -> outside
    y = np.nan_to_num((lat - LATS[0]) / (LATS[1] - LATS[0]), nan=-1.0)
    t = np.nan_to_num(t, nan=-1.0)
    ok = (x >= 0) & (x < len(LONS) - 1) & (y >= 0) & (y < len(LATS) - 1) & (t >= 0) & (t < field.shape[0] - 1)
    xi = np.clip(x, 0, len(LONS) - 2).astype(int)
    yi = np.clip(y, 0, len(LATS) - 2).astype(int)
    ti = np.clip(t, 0, field.shape[0] - 2).astype(int)
    fx, fy, ft = x - xi, y - yi, t - ti
    v = 0.0
    for dt_, wt in ((0, 1 - ft), (1, ft)):
        k = ti + dt_
        v = v + wt * ((field[k, yi, xi] * (1 - fx) + field[k, yi, xi + 1] * fx) * (1 - fy)
                      + (field[k, yi + 1, xi] * (1 - fx) + field[k, yi + 1, xi + 1] * fx) * fy)
    return np.where(ok, v, np.nan)


def trajectories(u, v, t_first, arrivals, lat0, lon0):
    """Hourly positions (HOURS_BACK + 1, n) going back from each arrival; NaN once a parcel leaves the domain."""
    step = -900.0  # s, RK2 (Heun)
    lat = np.array(lat0, float)
    lon = np.array(lon0, float)
    t = np.array([(a - t_first).total_seconds() / 10800.0 for a in arrivals])
    out = np.full((HOURS_BACK + 1, len(lat), 2), np.nan)
    out[0, :, 0], out[0, :, 1] = lat, lon
    alive = np.ones(len(lat), bool)
    for h in range(HOURS_BACK):
        for _ in range(4):
            u1, v1 = interp(u, t, lat, lon), interp(v, t, lat, lon)
            k = 111320.0 * np.cos(np.radians(lat))
            la2, lo2, t2 = lat + step * v1 / 111320.0, lon + step * u1 / k, t + step / 10800.0
            u2, v2 = interp(u, t2, la2, lo2), interp(v, t2, la2, lo2)
            alive &= ~(np.isnan(u1) | np.isnan(u2))
            lat = np.where(alive, lat + step * 0.5 * (v1 + v2) / 111320.0, lat)
            lon = np.where(alive, lon + step * 0.5 * (u1 + u2) / k, lon)
            t = t2
        out[h + 1, :, 0] = np.where(alive, lat, np.nan)
        out[h + 1, :, 1] = np.where(alive, lon, np.nan)
    return out


def inbox(lat, lon, b):
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def at_sea(lat, lon):
    """Crude: Bay of Bengal east of the east coast, Arabian Sea west of the west coast, south of 22N."""
    if lat > 22.5:
        return False
    east = 80.3 + max(0.0, lat - 13) * 0.62 if lat >= 13 else 80.0
    west = 72.8 + max(0.0, 20 - lat) * 0.3 if lat < 20 else 72.6
    return lon > east + 0.3 or lon < west - 0.3 or lat < 8.0


def run():
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "method": __doc__.split("\n\n")[1].replace("\n", " ") + " " + __doc__.split("\n\n")[2].replace("\n", " "),
           "receptors": {k: list(v) for k, v in RECEPTORS.items()}, "boxes": {"nw": NW_BOX, "igp": IGP_BOX},
           "hours_back": HOURS_BACK, "years": {}}
    for year in range(2018, dt.date.today().year + 1):
        cache = CACHE / f"traj_{year}.json"
        # past seasons do not change: reuse them unless their winds or the receptors changed
        sig = [sorted(f.name for f in CACHE.glob("*.npy") if season_steps(year)[0] <= int(f.name.split("_")[1]) <= season_steps(year)[1]), RECEPTORS]
        if year < dt.date.today().year and cache.exists() and "--all" not in sys.argv:
            c = json.loads(cache.read_text())
            if c.get("sig") == json.loads(json.dumps(sig)):
                out["years"][str(year)] = c["yr"]
                continue
        loaded = load_year(year)
        if loaded is None:
            continue
        t_first, u, v = loaded
        fires = fire_grid(year)
        print(year, "winds", u.shape, "fire days", len(fires), file=sys.stderr)
        nyf, nxf = round((FN - FS) / FG), round((FE - FW) / FG)
        dates = []
        d = dt.date(year, *SEASON[0])
        end = min(dt.date(year, *SEASON[1]), dt.date.today())
        while d <= end:
            dates.append(d)
            d += dt.timedelta(days=1)
        names = list(RECEPTORS)
        parcels = [(d, hh, nm) for d in dates for nm in names for hh in (0, 6, 12, 18)]
        arrivals = [dt.datetime(d.year, d.month, d.day, hh) for d, hh, nm in parcels]
        P = trajectories(u, v, t_first, arrivals, [RECEPTORS[nm][0] for _, _, nm in parcels], [RECEPTORS[nm][1] for _, _, nm in parcels])
        la, lo = P[..., 0], P[..., 1]                                  # (121, n)
        valid = ~np.isnan(la)
        npts = valid.sum(0)
        # fires met: same day and day before, in the 0.5 deg cell under each hourly point
        day0 = np.array([(a - dt.datetime(year, 1, 1)).days for a in arrivals])
        hours = np.arange(HOURS_BACK + 1)[:, None]
        arr_h = np.array([a.hour for a in arrivals])[None, :]
        pday = day0[None, :] + np.floor((arr_h - hours) / 24).astype(int)  # day-of-year index of each point
        cube = np.zeros((367, nyf, nxf), np.float32)
        for k, g in fires.items():
            dd = dt.date.fromisoformat(k)
            if dd.year == year:
                cube[(dd - dt.date(year, 1, 1)).days] = g
        inside = valid & (lo >= FW) & (lo < FE) & (la >= FS) & (la < FN)
        gy = np.clip(((np.nan_to_num(la) - FS) / FG).astype(int), 0, nyf - 1)
        gx = np.clip(((np.nan_to_num(lo) - FW) / FG).astype(int), 0, nxf - 1)
        pd0 = np.clip(pday, 0, 366)
        pd1 = np.clip(pday - 1, 0, 366)
        fa = np.where(inside, cube[pd0, gy, gx] + cube[pd1, gy, gx], 0.0)
        in_nw = valid & (lo >= NW_BOX[0]) & (lo <= NW_BOX[2]) & (la >= NW_BOX[1]) & (la <= NW_BOX[3])
        in_igp = valid & (lo >= IGP_BOX[0]) & (lo <= IGP_BOX[2]) & (la >= IGP_BOX[1]) & (la <= IGP_BOX[3])
        sea = np.zeros_like(valid)
        for j in range(la.shape[1]):
            sea[:, j] = [bool(valid[i, j]) and at_sea(la[i, j], lo[i, j]) for i in range(la.shape[0])]
        per = {"fire_all": fa.sum(0), "fire_nw": np.where(in_nw, fa, 0).sum(0), "igp_h": in_igp.sum(0), "nw_h": in_nw.sum(0), "sea_h": sea.sum(0)}
        yr = {nm: {"date": [], "fire_all": [], "fire_nw": [], "igp_h": [], "nw_h": [], "sea_h": [], "n": [], "path": []} for nm in names}
        for i in range(0, len(parcels), 4):
            d, _, nm = parcels[i]
            js = [j for j in range(i, i + 4) if npts[j] >= 25]
            rec = yr[nm]
            rec["date"].append(d.isoformat())
            rec["n"].append(len(js))
            for k, arr in per.items():
                rec[k].append(round(float(np.mean(arr[js])), 1) if js else None)
            j6 = i + 1                                                   # the 06 UTC arrival
            rec["path"].append([[round(float(lo[h, j6]), 2), round(float(la[h, j6]), 2)] for h in range(0, HOURS_BACK + 1, 6)
                                if valid[h, j6]] if npts[j6] >= 25 else None)
        out["years"][str(year)] = yr
        cache.write_text(json.dumps({"sig": sig, "yr": yr}, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size, file=sys.stderr)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "winds":
        years = [int(y) for y in sys.argv[2:]] or list(range(2018, dt.date.today().year + 1))
        for y in years:
            fetch_year(y)
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        run()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
