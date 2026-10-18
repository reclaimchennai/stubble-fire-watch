#!/usr/bin/env python3
"""Precompute everything the analysis page (/db) plots, into data/db/db.json.

Reads only files the pipeline already produced:
  data/fires/summary.json         daily counts + FRP by state x sensor x year, district season totals, hourly
  data/fires/district_daily.json  rice-season daily district series (MODIS, VIIRS S-NPP)
  data/creams/creams_dashboard.json, data/creams/creams.json   official bulletin counts
  data/db/extras/rice_area.json   kharif rice area/production by district (optional, scripts/build_db_extras.py)
  data/db/extras/aq_met.json      receptor-city PM2.5 + meteorology (optional, same script)
Nothing here is modelled or interpolated; derived numbers are simple sums, ratios and percentiles.
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "db"
STATES = ["Punjab", "Haryana", "Uttar Pradesh", "Madhya Pradesh", "Rajasthan", "Delhi", "Punjab (PK)"]
BASIS = {"modis": ["MODIS Terra", "MODIS Aqua"], "snpp": ["VIIRS S-NPP"], "viirs": ["VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21"]}
SEASON_DAYS = 77  # Sep 15 .. Nov 30


def load(p):
    p = ROOT / p
    return json.loads(p.read_text()) if p.exists() else None


def season_slice(year):
    a = dt.date(year, 9, 15).timetuple().tm_yday - 1
    return a, a + SEASON_DAYS


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    S = load("data/fires/summary.json")
    DD = load("data/fires/district_daily.json")
    C = load("data/creams/creams_dashboard.json")
    extras_rice = load("data/db/extras/rice_area.json")
    extras_aq = load("data/db/extras/aq_met.json")
    burn = load("data/burnscar/burnscar.json")
    aq_ground = load("data/db/extras/aq_ground.json")
    stubble = load("data/db/extras/stubble_share.json")
    enforcement = load("data/db/extras/enforcement.json")
    last = dt.date.fromisoformat(S["last_detection_date"])
    years = sorted(int(y) for y in S["daily"])

    # ---- season daily series per state per basis (Sep 15 .. Nov 30), and FRP
    daily = {}   # basis -> state -> year -> [77 counts]
    frp = {}     # basis -> state -> year -> total FRP (MW)
    for b, sensors in BASIS.items():
        daily[b], frp[b] = {}, {}
        for st in STATES:
            daily[b][st], frp[b][st] = {}, {}
            for y in years:
                a, z = season_slice(y)
                n, f, have = [0] * SEASON_DAYS, 0.0, False
                for sen in sensors:
                    d = S["daily"][str(y)].get(sen, {}).get(st)
                    if not d:
                        continue
                    have = True
                    for k, v in enumerate(d["n"][a:z]):
                        n[k] += v
                    f += sum(d["frp"][a:z])
                if have or (b == "modis") or (b == "snpp" and y >= 2012):
                    if (b == "snpp" and y < 2012) or (b == "viirs" and y < 2012):
                        continue
                    daily[b][st][y] = n
                    frp[b][st][y] = round(f)

    # ---- timing: day of season when 10/50/90 % of the season's detections had happened (Punjab+Haryana)
    timing = {}
    for b in BASIS:
        timing[b] = []
        for y in years:
            if y not in daily[b]["Punjab"]:
                continue
            tot = [daily[b]["Punjab"][y][k] + daily[b]["Haryana"].get(y, [0] * SEASON_DAYS)[k] for k in range(SEASON_DAYS)]
            s = sum(tot)
            complete = y < last.year or last >= dt.date(y, 11, 30)
            if s < 50 or not complete:
                continue
            cum, out = 0, {}
            for k, v in enumerate(tot):
                cum += v
                for q in (10, 50, 90):
                    if q not in out and cum >= s * q / 100:
                        out[q] = k
            timing[b].append({"year": y, "p10": out[10], "p50": out[50], "p90": out[90], "total": s})

    # ---- overpass mix: MODIS Terra (~10:30 IST) vs Aqua (~13:30 IST), VIIRS night share (Punjab+Haryana not
    # separable in the hourly table, so this uses the whole region's paddy-season detections)
    overpass = []
    for y in years:
        h = S["hourly_ist_rice"].get(str(y), {})
        t, a = sum(h.get("MODIS Terra", [])), sum(h.get("MODIS Aqua", []))
        v = h.get("VIIRS S-NPP")
        night = sum(v[k] for k in list(range(0, 6)) + list(range(19, 24))) if v else None
        overpass.append({"year": y, "terra": t, "aqua": a, "viirs_total": sum(v) if v else None, "viirs_night": night})

    # ---- districts: rice-season totals by year (VIIRS S-NPP and MODIS), from district_season
    dmeta = S["districts"]
    focus_ids = [i for i, d in enumerate(dmeta) if d["state"] in ("Punjab", "Haryana", "Delhi", "Uttar Pradesh", "Rajasthan", "Madhya Pradesh", "Punjab (PK)")]
    dist = {"ids": focus_ids, "names": [dmeta[i]["name"] for i in focus_ids], "states": [dmeta[i]["state"] for i in focus_ids], "modis": {}, "snpp": {}}
    for y in years:
        ds = S["district_season"].get(str(y), {}).get("rice", {})
        for b, sensors in (("modis", BASIS["modis"]), ("snpp", BASIS["snpp"])):
            if b == "snpp" and y < 2012:
                continue
            arrs = [ds[s] for s in sensors if s in ds]
            if not arrs:
                continue
            dist[b][y] = [sum(a[i] for a in arrs) for i in focus_ids]

    # ---- calendar: Punjab+Haryana daily counts per season day, per year (for the heat strip)
    calendar = {}
    for b in ("modis", "snpp"):
        calendar[b] = {y: [daily[b]["Punjab"][y][k] + daily[b]["Haryana"].get(y, [0] * SEASON_DAYS)[k] for k in range(SEASON_DAYS)]
                       for y in years if y in daily[b]["Punjab"]}

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "last_detection_date": S["last_detection_date"],
        "years": years, "states": STATES, "season_days": SEASON_DAYS,
        "daily": daily, "frp": frp, "timing": timing, "overpass": overpass, "districts": dist, "calendar": calendar,
        "creams": C,
    }
    if extras_rice:
        out["rice"] = extras_rice
    if extras_aq:
        out["aq"] = extras_aq
    if burn:   # drop the per-district daily detail the page does not use
        out["burn"] = {y: {k: v for k, v in b.items() if k != "updated"} for y, b in burn.items()}
    if aq_ground:
        out["aq_ground"] = aq_ground
    if stubble:
        out["stubble"] = stubble
    if enforcement:
        out["enforcement"] = enforcement
    p = OUT / "db.json"
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {p} ({p.stat().st_size / 1e6:.1f} MB); " + ", ".join(f"{k}={'yes' if k in out else 'no'}" for k in ("rice", "aq", "burn", "aq_ground", "stubble", "enforcement")))


if __name__ == "__main__":
    main()
