#!/usr/bin/env python3
"""North to south: join air quality in Chennai (and other receptors) with back-trajectories, fires and cyclones.

Inputs (data/db/extras/): aq_south.json (build_aq_south.py), transport.json (transport.py), cyclones.json
(build_cyclones.py). Output: data/db/south.json for the /south/ page.

Day classes (from the four daily 120 h back-trajectories at 850 hPa, averaged):
  nw_fire   the air crossed the Punjab-Haryana-Delhi box while it held fires (fire_nw >= 1 detection-day)
  igp       it spent >= 6 h over the Indo-Gangetic Plain box, but did not meet live NW fires
  marine    >= 60 % of the 121 hourly points over the sea
  land      everything else (peninsular / east-coast land air)
PM2.5 is compared between classes within the same month and on dry days (ERA5 precipitation < 1 mm), because rain
and the north-east monsoon change both the wind and the PM2.5.
"""
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "data" / "db" / "extras"
OUT = ROOT / "data" / "db" / "south.json"
CHENNAI = (13.08, 80.27)
SEASON_MONTHS = (10, 11, 12)
CYCLONE_KM = 800


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def qa_ground(daily, name, log):
    """Null implausible ground city-days in place: <= 2 stations reporting and PM2.5 > 3 x CAMS + 50 ug/m3
    (e.g. Chennai 4 Dec 2025: 324 from two stations the day after 142 mm of rain, CAMS 11): flooded or failing sensors."""
    for i, (v, n, c) in enumerate(zip(daily["pm25"], daily["n_stations"], daily["cams_pm25"])):
        if v is not None and c is not None and (n or 0) <= 2 and v > 3 * c + 50:
            log.append({"city": name, "date": daily["date"][i], "pm25": v, "n_stations": n, "cams_pm25": c})
            daily["pm25"][i] = None


def classify(r):
    if r["fire_nw"] is None:
        return None
    if r["fire_nw"] >= 1:
        return "nw_fire"
    if r["igp_h"] >= 6:
        return "igp"
    if r["sea_h"] >= 0.6 * 121:
        return "marine"
    return "land"


def storm_days(storms, point):
    """{date: (km, name)} of the nearest active cyclone (track point) per day."""
    out = {}
    for s in storms:
        for p in s["track"]:
            if p.get("kind") == "forecast":
                continue
            d = p["t"][:10]
            km = hav(point, (p["lat"], p["lon"]))
            if d not in out or km < out[d][0]:
                out[d] = (round(km), s["name"] or s["id"], s["id"])
    return out


def ols(y, X, names):
    """Least squares with heteroskedasticity-robust (HC1) standard errors."""
    X = np.column_stack([np.ones(len(y))] + X)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    n, k = X.shape
    xtx = np.linalg.inv(X.T @ X)
    cov = xtx @ (X.T * e ** 2) @ X @ xtx * n / (n - k)
    se = np.sqrt(np.diag(cov))
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    return {"n": int(n), "r2": round(float(r2), 3),
            "coef": {nm: {"b": round(float(b), 4), "se": round(float(s), 4)} for nm, b, s in zip(["const"] + names, beta, se)}}


def main():
    aq = json.loads((EX / "aq_south.json").read_text())
    tr = json.loads((EX / "transport.json").read_text())
    cy = json.loads((EX / "cyclones.json").read_text())
    storms = [s for s in cy["storms"] if s["season"] >= 2016]
    qa_log = []
    for name, city in aq["cities"].items():
        qa_ground(city["daily"], name, qa_log)
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "method": __doc__.split("\n\n")[1],
           "receptors": {}, "chennai": {}, "cyclones": [], "qa_dropped_ground_days": qa_log, "sources": {
               "aq": aq["sources"], "transport": tr["method"], "cyclones": cy["sources"]}}

    # ------------------------------------------------ per receptor: day table, classes, stats
    for name in tr["receptors"]:
        city = aq["cities"].get(name)
        if not city:
            continue
        D = city["daily"]
        idx = {d: i for i, d in enumerate(D["date"])}
        sdays = storm_days(storms, tuple(tr["receptors"][name]))
        rows = []
        for y, yr in tr["years"].items():
            t = yr.get(name)
            if not t:
                continue
            for i, d in enumerate(t["date"]):
                if int(d[5:7]) not in SEASON_MONTHS or d not in idx:
                    continue
                j = idx[d]
                r = {k: t[k][i] for k in ("fire_all", "fire_nw", "igp_h", "nw_h", "sea_h")}
                r.update(date=d, cls=classify(r), pm25=D["pm25"][j], cams_pm25=D["cams_pm25"][j], cams_co=D["cams_co"][j],
                         precip=D["precip"][j], wind=D["wind"][j], wdir=D["wdir"][j], blh=D["blh"][j],
                         storm_km=sdays.get(d, (None,))[0], storm=sdays.get(d, (None, None))[1])
                rows.append(r)
        # class stats per month: dry days, no cyclone within CYCLONE_KM
        stats = {}
        for m in SEASON_MONTHS:
            for var in ("pm25", "cams_pm25", "cams_co"):
                for c in ("nw_fire", "igp", "land", "marine"):
                    v = [r[var] for r in rows if int(r["date"][5:7]) == m and r["cls"] == c and r[var] is not None
                         and (r["precip"] or 0) < 1 and not (r["storm_km"] is not None and r["storm_km"] < CYCLONE_KM)]
                    if v:
                        stats.setdefault(str(m), {}).setdefault(var, {})[c] = {
                            "n": len(v), "median": round(float(np.median(v)), 1),
                            "q25": round(float(np.percentile(v, 25)), 1), "q75": round(float(np.percentile(v, 75)), 1)}
        # contact frequency per year (Oct-Nov)
        freq = {}
        for r in rows:
            if int(r["date"][5:7]) in (10, 11) and r["cls"]:
                f = freq.setdefault(r["date"][:4], {"days": 0, "nw_fire": 0, "igp": 0, "marine": 0, "land": 0})
                f["days"] += 1
                f[r["cls"]] += 1
        # regression on dry, cyclone-free Oct-Dec days: log PM2.5 ~ month + log1p(NW fire exposure) + met
        reg = {}
        for var in ("pm25", "cams_pm25", "cams_co"):
            sel = [r for r in rows if r[var] and r["fire_nw"] is not None and r["wind"] is not None and r["blh"] and r["precip"] is not None
                   and not (r["storm_km"] is not None and r["storm_km"] < CYCLONE_KM)]
            if len(sel) < 60:
                continue
            y = np.log([r[var] for r in sel])
            X = [np.log1p([r["fire_nw"] for r in sel]), np.log1p([r["fire_all"] - r["fire_nw"] for r in sel]),
                 np.array([r["sea_h"] / 121 for r in sel]), np.log1p([r["precip"] for r in sel]),
                 np.array([r["wind"] for r in sel]), np.log([r["blh"] for r in sel]),
                 np.array([int(r["date"][5:7]) == 11 for r in sel], float), np.array([int(r["date"][5:7]) == 12 for r in sel], float)]
            reg[var] = ols(y, X, ["log1p_fire_nw", "log1p_fire_other", "sea_frac", "log1p_precip", "wind", "log_blh", "nov", "dec"])
        keep = ("date", "cls", "fire_all", "fire_nw", "igp_h", "sea_h", "pm25", "cams_pm25", "cams_co", "precip", "wind", "wdir", "storm_km", "storm")
        out["receptors"][name] = {"lat": tr["receptors"][name][0], "lon": tr["receptors"][name][1],
                                  "days": {k: [r[k] for r in rows] for k in keep}, "class_stats": stats,
                                  "contact_by_year": freq, "regression": reg}

    # ------------------------------------------------ Chennai: trajectory paths (06 UTC) for the map
    paths = {}
    for y, yr in tr["years"].items():
        t = yr.get("Chennai")
        if t:
            for d, p, fn, fa in zip(t["date"], t["path"], t["fire_nw"], t["fire_all"]):
                if p:
                    paths[d] = {"p": p, "fn": fn, "fa": fa}
    out["chennai"]["paths"] = paths
    out["chennai"]["hourly"] = aq["chennai_hourly"]
    out["chennai"]["stations"] = aq["chennai_stations"]

    # ------------------------------------------------ cyclone composites around closest approach to Chennai
    C = aq["cities"]["Chennai"]["daily"]
    idx = {d: i for i, d in enumerate(C["date"])}
    for s in storms:
        if s["closest_to_chennai_km"] is None or s["closest_to_chennai_km"] > CYCLONE_KM:
            continue
        t0 = dt.datetime.fromisoformat(s["closest_time"].replace("Z", "+00:00")).date()
        series = {"day": [], "pm25": [], "cams_pm25": [], "cams_co": [], "precip": [], "wind": [], "wdir": []}
        for k in range(-7, 8):
            d = (t0 + dt.timedelta(days=k)).isoformat()
            j = idx.get(d)
            series["day"].append(k)
            for v in ("pm25", "cams_pm25", "cams_co", "precip", "wind", "wdir"):
                series[v].append(C[v][j] if j is not None else None)
        out["cyclones"].append({"id": s["id"], "name": s["name"], "season": s["season"], "grade_max": s["grade_max"],
                                "closest_km": s["closest_to_chennai_km"], "closest_time": s["closest_time"],
                                "bearing": (s.get("closest") or {}).get("bearing_from_chennai"),
                                "landfall": s.get("landfall"), "series": series,
                                "track": [[p["lon"], p["lat"], p["t"], p.get("grade"), p.get("kind")] for p in s["track"]
                                          if p.get("kind") != "forecast"][::2 if s["season"] < 2026 else 1]})
    OUT.write_text(json.dumps(out, separators=(",", ":"), allow_nan=False, default=lambda o: None))
    print("wrote", OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
