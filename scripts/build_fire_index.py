#!/usr/bin/env python3
"""An all-satellite fire index for the paddy season: the best VIIRS count, plus the fires the polar passes missed.

Polar orbiters pass at about 13:30 and 01:30 IST (Aqua has drifted to about 16:00 by 2026, Terra to about 09:30); since
2022 most Punjab burning starts later in the afternoon. Meteosat (SEVIRI, every 15 min, 2017-) and INSAT-3DS (every
30 min, 2025-) see those fires in ~4 km pixels. The index counts each fire once, in VIIRS-equivalent detections:

  base        per district and UTC day, the highest count among the VIIRS satellites flying that year (S-NPP 2012-,
              NOAA-20 2018-, NOAA-21 2023-): one VIIRS census, robust to one satellite's gaps (S-NPP saw less than half
              of NOAA-20's Punjab fires in 2026).
  geo unit    a 0.04-degree cell on one UTC day in which Meteosat or INSAT-3DS saw fire (first-seen time kept). Meteosat
              first; an INSAT-3DS unit counts only if no Meteosat unit lies in the same or a neighbouring cell that day.
              Static sources (FIRMS's list) and cells seen on 40+ of the 61 days are dropped.
  credit      each geo unit is worth r VIIRS-sized fires. Per state and season, the index adds sum(r) over the geo units
              minus the VIIRS detections in or next to them within 12 h (each detection counted once; the best VIIRS
              satellite that day), when positive. So a VIIRS pass that caught a small early fire next to a cell that burned
              big later does not hide the later fire, and midday burning that VIIRS saw adds little. The credit is spread
              over the days, and each day's over the districts, in proportion to their shortfall.
  r           VIIRS-equivalent detections per unseen geo unit = matched S-NPP detections per jointly seen Meteosat unit,
              2017-2021 (each detection matched once). INSAT-3DS: r scaled by how many Aqua detections an INSAT-3DS
              cell carries against a Meteosat cell (same box, same seasons) when Aqua passed in the late afternoon.
  Aqua-only   Aqua MODIS detections with no VIIRS detection or geo unit within a cell and 12 h: small afternoon fires
              only the drifted Aqua pass saw, times k = S-NPP detections per Aqua detection in cell-days both saw,
              2018-2021.
  index       base + geostationary credit + k x Aqua-only detections, by state, district and day, 15 Sep - 30 Nov.
  cross-check for seasons Aqua passed at 15:00 IST or later: (Aqua detections + r_aqua x geo units Aqua missed) x the
              2018-2021 VIIRS : Aqua ratio. A second, independent route to the same quantity.

Coverage: Meteosat is downloaded only over 69-78 E, 27.5-34.2 N (both Punjabs, Haryana, Delhi, north Rajasthan) from
2017; INSAT-3DS covers every state from the 2025 season. The index runs from 2017 and equals the VIIRS base where there
are no geostationary data. It is an estimate: geostationary pixels see only the larger fires.

Inputs: data/fires/points_<year>_rice.bin and summary.json (scripts/build_fires.py), data/raw/geostationary/<year>/
fires.csv (scripts/fetch_geostationary.py), INSAT-3DS L2P pixel CSVs shared by the Talaash project.
Output: data/db/extras/fire_index.json
"""
import csv
import datetime as dt
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "db" / "extras" / "fire_index.json"
MSG = ROOT / "data" / "raw" / "geostationary"
INSAT = Path.home() / "svr" / "projects" / "talaash" / "mosdac_sat" / "shared" / "fire" / "3SIMG_L2P_FIR"
CELL = 0.04
WIN = dt.timedelta(hours=12)
IST = dt.timedelta(hours=5, minutes=30)
FIRST_YEAR = 2017
CALIB_MSG = range(2017, 2022)              # burning still mostly before the overpass
CALIB_K = range(2018, 2022)                # Aqua and VIIRS at the same hour
SEASON = ((10, 1), (11, 30))               # geostationary seasons (IST)
START = (9, 15)                            # index days run 15 Sep - 30 Nov (UTC), like /db
MSG_BOX = (69.0, 27.5, 78.0, 34.2)
TERRA, AQUA, SNPP, N20, N21 = 0, 1, 2, 3, 4
VIIRS = (SNPP, N20, N21)
LATE_AQUA_H = 15                           # Aqua's daytime pass at or after this IST hour: the cross-check applies
PH = ("Punjab", "Haryana")


def season_bounds(y):
    a = dt.datetime(y, *SEASON[0]) - IST
    b = dt.datetime(y, *SEASON[1]) + dt.timedelta(days=1) - IST
    return a, b


def cell(lon, lat):
    return (round(lon / CELL), round(lat / CELL))


def neighbours(c):
    return [(c[0] + dx, c[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def polar_points(y, bbox):
    """[(lon, lat, utc datetime, district index, sensor)] for Terra, Aqua and the three VIIRS."""
    p = ROOT / "data" / "fires" / f"points_{y}_rice.bin"
    if not p.exists():
        return []
    a = np.fromfile(p, dtype=[("x", "<u2"), ("y", "<u2"), ("doy", "<u2"), ("hhmm", "<u2"), ("frp", "<u2"), ("d", "<u2"), ("s", "u1"), ("c", "u1")])
    a = a[a["s"] <= N21]
    W, S, E, N = bbox
    lon = W + a["x"] / 65535 * (E - W)
    lat = S + a["y"] / 65535 * (N - S)
    y0 = dt.datetime(y, 1, 1)
    return [(float(lon[i]), float(lat[i]), y0 + dt.timedelta(days=int(a["doy"][i]) - 1, hours=int(a["hhmm"][i]) // 100, minutes=int(a["hhmm"][i]) % 100),
             int(a["d"][i]) - 1, int(a["s"][i])) for i in range(len(a))]


def msg_units(y):
    p = MSG / str(y) / "fires.csv"
    units = {}
    if not p.exists():
        return units, False
    a, b = season_bounds(y)
    for r in csv.DictReader(open(p, newline="")):
        try:
            t = dt.datetime.fromisoformat(r["slot_utc"])
            lon, lat = float(r["lon"]), float(r["lat"])
        except (KeyError, ValueError):
            continue
        if a <= t < b:
            k = (cell(lon, lat), t.date())
            if k not in units or t < units[k]:
                units[k] = t
    return units, True


def insat_units(y, last):
    units = {}
    if not INSAT.is_dir():
        return units, False
    a, b = season_bounds(y)
    files = [f for f in INSAT.glob("????-??-??.csv") if a.date() <= dt.date.fromisoformat(f.stem) <= b.date()]
    days = (min(b.date(), last) - (a + IST).date()).days
    if days <= 0 or len(files) < days:      # only seasons the collector holds completely (to date)
        return units, False
    for f in files:
        for r in csv.DictReader(open(f, newline="")):
            try:
                t = dt.datetime.fromisoformat((r.get("obs_time_utc") or r["slot_utc"]).replace("Z", "")[:19])
                lon, lat = float(r["lon"]), float(r["lat"])
            except (KeyError, ValueError):
                continue
            if a <= t < b and 60 <= lon <= 100 and 5 <= lat <= 40:
                k = (cell(lon, lat), t.date())
                if k not in units or t < units[k]:
                    units[k] = t
    return units, True


def in_box(c):
    return MSG_BOX[0] <= c[0] * CELL <= MSG_BOX[2] and MSG_BOX[1] <= c[1] * CELL <= MSG_BOX[3]


def drop_static(units, static4):
    ndays = Counter(c for c, _ in units)
    return {k: t for k, t in units.items() if k[0] not in static4 and ndays[k[0]] < 40}


class Grid:
    """Points indexed by 0.04-degree cell, for 3x3 / time-window lookups."""

    def __init__(self, pts):
        self.pts, self.by = pts, defaultdict(list)
        for i, p in enumerate(pts):
            self.by[cell(p[0], p[1])].append(i)

    def near(self, c, t0, win=WIN):
        return [i for n in neighbours(c) for i in self.by.get(n, ()) if abs(self.pts[i][2] - t0) <= win]


def main():
    S = json.loads((ROOT / "data" / "fires" / "summary.json").read_text())
    bbox, dmeta = S["bbox"], S["districts"]
    g = json.loads((ROOT / "data" / "geo" / "districts.geojson").read_text())
    polys = [shape(f["geometry"]) for f in g["features"]]
    ids = [f["properties"]["id"] for f in g["features"]]
    tree = STRtree(polys)
    dcache = {}

    def district_of(c):
        if c not in dcache:
            pt = Point(c[0] * CELL, c[1] * CELL)
            dcache[c] = next((ids[j] for j in tree.query(pt) if polys[j].contains(pt)), None)
        return dcache[c]

    last = dt.date.fromisoformat(S["last_detection_date"])
    sp = ROOT / "data" / "raw" / "firms_static_cells.json"
    static4 = {(round(c[0] * 0.01 / CELL), round(c[1] * 0.01 / CELL)) for c in json.loads(sp.read_text())["cells"]} if sp.exists() else set()

    Y = {}
    for y in range(FIRST_YEAR, last.year + 1):
        pts = polar_points(y, bbox)
        mu, have_m = msg_units(y)
        iu, have_i = insat_units(y, last)
        mu, iu = drop_static(mu, static4), drop_static(iu, static4)
        iu_box = {k: t for k, t in iu.items() if in_box(k[0])}     # before de-duplication: for the like-for-like calibration
        mcells = defaultdict(set)
        for c, d in mu:
            mcells[d].add(c)
        iu = {k: t for k, t in iu.items() if not any(n in mcells[k[1]] for n in neighbours(k[0]))}
        viirs = Grid([p for p in pts if p[4] in VIIRS])
        snpp = Grid([p for p in pts if p[4] == SNPP])
        aqua = Grid([p for p in pts if p[4] == AQUA])
        hrs = [((p[2] + IST).hour) for p in pts if p[4] == AQUA and 6 <= (p[2] + IST).hour <= 19]
        Y[y] = {"pts": pts, "mu": mu, "iu": iu, "iu_box": iu_box, "viirs": viirs, "snpp": snpp, "aqua": aqua, "have": {"msg": have_m, "insat": have_i},
                "aqua_hour": float(np.median(hrs)) if hrs else None, "sats": sorted({p[4] for p in pts if p[4] in VIIRS})}

    # ---- calibration
    def joint_rate(grid, units):
        used, j, m = set(), 0, 0
        for (c, d), t0 in units.items():
            hit = grid.near(c, t0)
            if hit:
                j += 1
                new = [i for i in hit if i not in used]
                used.update(new)
                m += len(new)
        return m, j

    m = j = 0
    for y in CALIB_MSG:
        if y in Y:
            a, b = joint_rate(Y[y]["snpp"], Y[y]["mu"])
            m, j = m + a, j + b
    r_msg = m / j if j else None
    calib = {"msg": {"seasons": [CALIB_MSG[0], CALIB_MSG[-1]], "matched_snpp": m, "units": j}}
    # Aqua per geo cell: Meteosat (midday, 2017-2021) and both geos in late-Aqua seasons
    ma = ja = 0
    for y in CALIB_MSG:
        if y in Y:
            a, b = joint_rate(Y[y]["aqua"], Y[y]["mu"])
            ma, ja = ma + a, ja + b
    r_aqua_msg = ma / ja if ja else None
    late = [y for y in Y if (Y[y]["aqua_hour"] or 0) >= LATE_AQUA_H]
    lm = lj = li = lji = 0
    for y in late:
        a, b = joint_rate(Y[y]["aqua"], Y[y]["mu"])
        lm, lj = lm + a, lj + b
        a, b = joint_rate(Y[y]["aqua"], Y[y]["iu_box"])          # same region and season as Meteosat
        li, lji = li + a, lji + b
    late_msg = lm / lj if lj else None
    late_insat = li / lji if lji else None
    r_insat = r_msg * (late_insat / late_msg) if (r_msg and late_msg and late_insat) else r_msg
    r_aqua_insat = r_aqua_msg * (late_insat / late_msg) if (r_aqua_msg and late_msg and late_insat) else r_aqua_msg
    calib["aqua"] = {"midday_msg_per_cell": round(r_aqua_msg, 3) if r_aqua_msg else None, "late_seasons": late,
                     "late_msg_per_cell": round(late_msg, 3) if late_msg else None, "late_insat_per_cell": round(late_insat, 3) if late_insat else None}
    # k: S-NPP detections per Aqua detection in cell-days both saw; and the whole-region VIIRS:Aqua ratio
    ks = ka = 0
    vt = at = 0
    for y in CALIB_K:
        if y not in Y:
            continue
        cs, ca = Counter(), Counter()
        for p in Y[y]["pts"]:
            if p[4] == SNPP:
                cs[(cell(p[0], p[1]), p[2].date())] += 1
            elif p[4] == AQUA:
                ca[(cell(p[0], p[1]), p[2].date())] += 1
            if 0 <= p[3] < len(dmeta) and dmeta[p[3]]["state"] in PH:
                vt += p[4] == SNPP
                at += p[4] == AQUA
        for k_, n in ca.items():
            if k_ in cs:
                ks, ka = ks + cs[k_], ka + n
    k_aqua = ks / ka if ka else None
    ratio_va = vt / at if at else None
    calib["k_aqua_only"] = {"seasons": [CALIB_K[0], CALIB_K[-1]], "snpp": ks, "aqua": ka}
    calib["viirs_per_aqua_ph"] = round(ratio_va, 3) if ratio_va else None

    # ---- per season
    out_years = {}
    for y, P in Y.items():
        d0 = dt.date(y, *START)
        days = (dt.date(y, *SEASON[1]) - d0).days + 1
        dix = lambda d: (d - d0).days if 0 <= (d - d0).days < days else None   # noqa: E731
        zero = lambda: [0] * days            # noqa: E731
        st = defaultdict(lambda: {"base": zero(), "added": [0.0] * days, "geo_only": zero(), "geo_seen": zero(), "aqua_only": zero(),
                                  "snpp": zero(), "aqua": zero(), "added_aqua": [0.0] * days})
        dd = defaultdict(lambda: defaultdict(Counter))     # district -> day -> sensor -> n
        for lon, lat, t, d, s in P["pts"]:
            k = dix(t.date())
            if k is None or not 0 <= d < len(dmeta):
                continue
            dd[d][k][s] += 1
            if s == SNPP:
                st[dmeta[d]["state"]]["snpp"][k] += 1
            elif s == AQUA:
                st[dmeta[d]["state"]]["aqua"][k] += 1
        dist_base, dist_add = defaultdict(int), defaultdict(float)
        for d, byday in dd.items():
            for k, c in byday.items():
                b = max(c.get(s, 0) for s in VIIRS)
                st[dmeta[d]["state"]]["base"][k] += b
                dist_base[d] += b
        # geostationary cells: each is worth r VIIRS-sized fires; per district-day, credit what VIIRS did not count.
        # A VIIRS pass that caught a small early fire next to a cell that burned big later no longer hides the later fire.
        geo_cells = defaultdict(set)
        need = defaultdict(float)                 # (district, day) -> sum of r over geo units
        need_a = defaultdict(float)               # the same in Aqua units (NDVI variant)
        got = defaultdict(lambda: defaultdict(int))   # (district, day) -> sensor -> matched detections
        used = defaultdict(set)
        grids = {s: Grid([p for p in P["pts"] if p[4] == s]) for s in VIIRS + (AQUA,)}
        for src, units, r, ra in (("msg", P["mu"], r_msg, r_aqua_msg), ("insat", P["iu"], r_insat, r_aqua_insat)):
            for (c, day), t0 in units.items():
                geo_cells[day].add(c)
                k, d = dix(day), district_of(c)
                if k is None or d is None:
                    continue
                key = (d, k)
                need[key] += r
                need_a[key] += ra
                any_v = False
                for s in VIIRS + (AQUA,):
                    hit = [i for i in grids[s].near(c, t0) if i not in used[s]]
                    used[s].update(hit)
                    got[key][s] += len(hit)
                    any_v = any_v or (s != AQUA and bool(grids[s].near(c, t0)))
                s_ = st[dmeta[d]["state"]]
                s_["geo_seen" if any_v else "geo_only"][k] += 1
        # sum per state-day (small district-days cut at zero would bias the index up), then share each state's credit
        # out to its districts in proportion to their own shortfall
        S_need, S_got, S_need_a, S_got_a, short = defaultdict(float), defaultdict(lambda: defaultdict(int)), defaultdict(float), defaultdict(int), defaultdict(dict)
        for (d, k), n in need.items():
            sk = (dmeta[d]["state"], k)
            S_need[sk] += n
            for s in VIIRS:
                S_got[sk][s] += got[(d, k)].get(s, 0)
            S_need_a[sk] += need_a[(d, k)]
            S_got_a[sk] += got[(d, k)].get(AQUA, 0)
            short[sk][d] = max(0.0, n - max(got[(d, k)].get(s, 0) for s in VIIRS))
        # net over the season per state (cutting each day at zero would keep only the noise that adds), then spread the
        # state's credit over its days, and each day's over its districts, in proportion to their shortfall
        for state in {sk[0] for sk in S_need}:
            dk = [sk for sk in S_need if sk[0] == state]
            x = {sk: S_need[sk] - max(S_got[sk].get(s, 0) for s in VIIRS) for sk in dk}
            # Aqua units (NDVI variant): what either Aqua or VIIRS (converted, r_msg / r_aqua_msg per cell) already counted
            va = (r_msg / r_aqua_msg) if (r_msg and r_aqua_msg) else 1.0
            xa = {sk: S_need_a[sk] - max(S_got_a[sk], max(S_got[sk].get(s, 0) for s in VIIRS) / va) for sk in dk}
            for xs, key in ((x, "added"), (xa, "added_aqua")):
                total = max(0.0, sum(xs.values()))
                pos = {sk: v for sk, v in xs.items() if v > 0}
                ps = sum(pos.values())
                for sk, v in pos.items():
                    add = total * v / ps
                    st[state][key][sk[1]] += add
                    if key == "added":
                        tot = sum(short[sk].values())
                        for d, w in short[sk].items():
                            if tot > 0:
                                dist_add[d] += add * w / tot
        # Aqua-only small fires: no VIIRS detection and no geo unit nearby
        for lon, lat, t, d, s in P["pts"]:
            if s != AQUA or not 0 <= d < len(dmeta):
                continue
            k = dix(t.date())
            c = cell(lon, lat)
            if k is None or P["viirs"].near(c, t) or any(n in geo_cells[t.date()] for n in neighbours(c)):
                continue
            s_ = st[dmeta[d]["state"]]
            s_["aqua_only"][k] += 1
            s_["added"][k] += k_aqua
            dist_add[d] += k_aqua
        # cross-check (Punjab + Haryana), seasons Aqua passed late
        xc = None
        if (P["aqua_hour"] or 0) >= LATE_AQUA_H and ratio_va:
            aq = sum(sum(st[s]["aqua"]) for s in PH)
            add = sum(sum(st[s]["added_aqua"]) for s in PH)
            xc = {"aqua": aq, "geo_added_aqua_eq": round(add), "viirs_per_aqua": round(ratio_va, 3), "estimate": round((aq + add) * ratio_va)}
        rnd = lambda v: [round(x, 1) for x in v]   # noqa: E731
        out_years[str(y)] = {
            "first_utc_date": d0.isoformat(), "days": days, "have": P["have"], "viirs_sats": P["sats"],
            "aqua_median_hour_ist": P["aqua_hour"],
            "states": {s: {**v, "added": rnd(v["added"]), "added_aqua": rnd(v["added_aqua"])} for s, v in st.items() if sum(v["base"]) or sum(v["added"]) or sum(v["aqua"])},
            "district_base": {str(d): v for d, v in dist_base.items()},
            "district_added": {str(d): round(v, 1) for d, v in dist_add.items()},
            "crosscheck_ph": xc,
        }
        tot = lambda key: round(sum(sum(st[s][key]) for s in PH))   # noqa: E731
        print(f"{y}: P+H base {tot('base')} + added {tot('added')} = {tot('base') + tot('added')}"
              f" (geo-only {tot('geo_only')}, aqua-only {tot('aqua_only')}; Aqua at {P['aqua_hour']} h)"
              + (f"; cross-check {xc['estimate']}" if xc else ""))
    res = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "version": 2,
        "name": "All-satellite index",
        "r": {"msg": round(r_msg, 3) if r_msg else None, "insat": round(r_insat, 3) if r_insat else None},
        "r_aqua": {"msg": round(r_aqua_msg, 3) if r_aqua_msg else None, "insat": round(r_aqua_insat, 3) if r_aqua_insat else None},
        "k_aqua_only": round(k_aqua, 3) if k_aqua else None,
        "calibration": calib,
        "cell_deg": CELL, "window_h": 12, "season_ist": ["10-01", "11-30"], "start_utc": "09-15",
        "coverage": {"msg": {"box": MSG_BOX, "from": FIRST_YEAR}, "insat": {"from": min((y for y, P in Y.items() if P["have"]["insat"]), default=None)}},
        "method": ("Best VIIRS count (each district-day, the highest among S-NPP, NOAA-20 and NOAA-21) plus the fires the polar "
                   "passes missed: Meteosat and INSAT-3DS fire cells (0.04 degree, per day) with no VIIRS detection in or next to "
                   "them within 12 hours, each worth r VIIRS detections (the S-NPP detections per jointly seen Meteosat cell, "
                   "2017-2021; INSAT-3DS scaled through Aqua, which sees both in the late afternoon), plus Aqua detections no VIIRS "
                   "pass or geostationary cell saw, each worth k (S-NPP per Aqua detection where both saw the same cell, 2018-2021)."),
        "years": out_years,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(res, separators=(",", ":")))
    os.replace(tmp, OUT)
    print(f"r = {res['r']}, r_aqua = {res['r_aqua']}, k = {res['k_aqua_only']}, VIIRS:Aqua = {calib['viirs_per_aqua_ph']}; wrote {OUT}")


if __name__ == "__main__":
    main()
