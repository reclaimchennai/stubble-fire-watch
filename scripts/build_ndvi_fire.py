#!/usr/bin/env python3
"""Crop greenness vs stubble-fire counts for Punjab + Haryana, refit every run: data/db/ndvi_fire.json.

After Jethva et al. (2019, Sci Rep 9:16594) and Jethva (2022, Front Earth Sci 10:1047278): the Aqua/MODIS 16-day NDVI
composite that starts on day 249 (6 Sep; 5 Sep in leap years) predicts the Oct-Nov Aqua/MODIS fire count.

  NDVI   MYD13Q1 v061 day-249 composite, 250 m. Mean of every pixel inside Punjab + Haryana whose pixel_reliability is
         0 (good) or 1 (marginal). Tiles h24v05 + h24v06 from Microsoft Planetary Computer (keyless); years it lacks
         (2025) come from the ORNL DAAC MODIS web service, which serves the same product in 200 km subsets.
         Cached per year in data/raw/ndvi/ndvi.json; a year is fetched once its composite exists.
  Fires  Aqua MODIS detections 1 Oct - 30 Nov in Punjab + Haryana from data/fires/summary.json (static sources
         removed by build_fires.py). 2002, which the dashboard archive does not hold, is counted from the FIRMS
         country file with the same type/static-cell filters.
  Fit    ordinary least squares, count ~ NDVI, over FIT_YEARS (the seasons before the burning moved past the 13:30
         Aqua overpass). The current season gets a prediction with a 95 % prediction interval, and so does its count
         to date (same fit on each year's 1 Oct .. same-day count), so a partial season can be compared fairly.
"""
import csv
import datetime as dt
import json
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from rasterio.windows import Window
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "ndvi"
OUT = ROOT / "data" / "db" / "ndvi_fire.json"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/modiseuwest/modis-061-cogs"
ORNL = "https://modis.ornl.gov/rst/api/v1/MYD13Q1/subset"
SINU = "+proj=sinu +R=6371007.181 +nadgrids=@null +wktext +units=m +no_defs"
TILE = 1111950.5197665233
X0, Y0 = -20015109.355798, 10007554.677899
PS = TILE / 4800                       # 250 m pixel, metres
STATES = {"IN-PB": "Punjab", "IN-HR": "Haryana"}
FIRST_YEAR = 2002                      # Aqua's first post-monsoon season
FIT_YEARS = list(range(2002, 2022))    # 2002-2021; Jethva (2026) shows peak burning moved to late afternoon from 2022
DOY = 249
SEASON = ((10, 1), (11, 30))
NDAYS = 61


def get_json(url, body=None, tries=4, timeout=180):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json", "User-Agent": "stubble-fire-watch"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


# ---------------------------------------------------------------- region on the MODIS sinusoidal 250 m grid
def region():
    """(mask, row0, col0) for Punjab + Haryana on the global 250 m sinusoidal grid, plus the lon/lat polygon."""
    g = json.loads((ROOT / "data" / "raw" / "boundaries" / "IND-ADM1.geojson").read_text())
    poly = unary_union([shape(f["geometry"]) for f in g["features"] if f["properties"].get("shapeISO") in STATES])
    sp = shape(transform_geom("EPSG:4326", SINU, mapping(poly)))
    x0, y0, x1, y1 = sp.bounds
    c0, c1 = int((x0 - X0) // PS), int((x1 - X0) // PS) + 1
    r0, r1 = int((Y0 - y1) // PS), int((Y0 - y0) // PS) + 1
    tr = Affine(PS, 0, X0 + c0 * PS, 0, -PS, Y0 - r0 * PS)
    mask = rasterize([(mapping(sp), 1)], out_shape=(r1 - r0, c1 - c0), transform=tr, dtype="uint8").astype(bool)
    return mask, r0, c0, poly


def from_pc(year, mask, r0, c0):
    """(ndvi, reliability) arrays on the region grid from Planetary Computer, or None if the composite is missing."""
    d = dt.date(year, 1, 1) + dt.timedelta(DOY - 1)
    feats = get_json(f"{STAC}/search", {"collections": ["modis-13Q1-061"], "bbox": [73.8, 27.6, 77.6, 32.6],
                                        "datetime": f"{d}T00:00:00Z/{d}T23:59:59Z", "limit": 20})["features"]
    feats = [f for f in feats if f["id"].startswith(f"MYD13Q1.A{year}{DOY:03d}.")]
    tiles = {f["id"].split(".")[2] for f in feats}
    if not {"h24v05", "h24v06"} <= tiles:
        return None
    tok = get_json(TOKEN_URL)["token"]
    H, W = mask.shape
    nd, rel = np.full((H, W), -3000, np.int16), np.full((H, W), -1, np.int16)
    for f in feats:
        h, v = int(f["id"].split(".")[2][1:3]), int(f["id"].split(".")[2][4:6])
        tr0, tc0 = v * 4800, h * 4800
        a0, a1 = max(r0, tr0), min(r0 + H, tr0 + 4800)
        b0, b1 = max(c0, tc0), min(c0 + W, tc0 + 4800)
        if a0 >= a1 or b0 >= b1:
            continue
        win = Window(b0 - tc0, a0 - tr0, b1 - b0, a1 - a0)
        for key, arr in (("250m_16_days_NDVI", nd), ("250m_16_days_pixel_reliability", rel)):
            with rasterio.open(f"/vsicurl/{f['assets'][key]['href']}?{tok}") as src:
                arr[a0 - r0:a1 - r0, b0 - c0:b1 - c0] = src.read(1, window=win)
    return nd, rel


def from_ornl(year, mask, r0, c0, poly):
    """Same arrays from ORNL DAAC 200 km subsets laid on a grid of centres across the region."""
    H, W = mask.shape
    nd, rel = np.full((H, W), -3000, np.int16), np.full((H, W), -1, np.int16)
    date = f"A{year}{DOY:03d}"
    lon0, lat0, lon1, lat1 = poly.bounds
    for lat in np.arange(lat0 + 0.85, lat1 + 0.9, 1.7):
        for lon in np.arange(lon0 + 0.95, lon1 + 1.0, 1.9):
            if not poly.buffer(1.0).contains(shape({"type": "Point", "coordinates": [lon, lat]})):
                continue
            for band, arr in (("250m_16_days_NDVI", nd), ("250m_16_days_pixel_reliability", rel)):
                j = get_json(f"{ORNL}?latitude={lat:.3f}&longitude={lon:.3f}&band={band}&startDate={date}&endDate={date}"
                             f"&kmAboveBelow=100&kmLeftRight=100", timeout=600)
                if not j.get("subset"):
                    return None
                nr, nc = int(j["nrows"]), int(j["ncols"])
                data = np.asarray(j["subset"][0]["data"], np.int32).reshape(nr, nc)   # row-major from the top-left
                gc = int(round((float(j["xllcorner"]) - X0) / PS))
                gr = int(round((Y0 - (float(j["yllcorner"]) + nr * PS)) / PS))
                a0, a1 = max(gr, r0), min(gr + nr, r0 + H)
                b0, b1 = max(gc, c0), min(gc + nc, c0 + W)
                if a0 < a1 and b0 < b1:
                    arr[a0 - r0:a1 - r0, b0 - c0:b1 - c0] = data[a0 - gr:a1 - gr, b0 - gc:b1 - gc]
    return nd, rel


def ndvi_years():
    RAW.mkdir(parents=True, exist_ok=True)
    cache_p = RAW / "ndvi.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    today = dt.date.today()
    need = [y for y in range(FIRST_YEAR, today.year + 1) if str(y) not in cache
            and today >= dt.date(y, 1, 1) + dt.timedelta(DOY - 1 + 16)]
    if need:
        mask, r0, c0, poly = region()
        for y in need:
            src, arrs = "Planetary Computer", from_pc(y, mask, r0, c0)
            if arrs is None:
                src, arrs = "ORNL DAAC", from_ornl(y, mask, r0, c0, poly)
            if arrs is None:
                print(f"ndvi {y}: composite not available yet")
                continue
            nd, rel = arrs
            ok = mask & (rel >= 0) & (rel <= 1) & (nd >= -2000)
            cover = mask & (rel >= 0)
            cache[str(y)] = {"ndvi": round(float(nd[ok].mean()) * 1e-4, 4), "pixels": int(ok.sum()),
                             "valid_frac": round(float(ok.sum() / mask.sum()), 3),
                             "covered_frac": round(float(cover.sum() / mask.sum()), 3), "source": src}
            print(f"ndvi {y}: {cache[str(y)]}")
            cache_p.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return cache


# ---------------------------------------------------------------- fires
def season_idx(y):
    return dt.date(y, *SEASON[0]).timetuple().tm_yday - 1


def daily_counts():
    """{year: [61 daily Aqua counts, Punjab + Haryana]} and the last detection date."""
    S = json.loads((ROOT / "data" / "fires" / "summary.json").read_text())
    out = {}
    for ys, sens in S["daily"].items():
        y, a = int(ys), season_idx(int(ys))
        aq = sens.get("MODIS Aqua", {})
        v = np.zeros(NDAYS, int)
        for st in STATES.values():
            if st in aq:
                v += np.asarray(aq[st]["n"][a:a + NDAYS], int)
        out[y] = v.tolist()
    if FIRST_YEAR not in out:
        v = counts_2002()
        if v is not None:
            out[FIRST_YEAR] = v
    return out, dt.date.fromisoformat(S["last_detection_date"])


def counts_2002():
    p = RAW / "modis_2002_India.csv"   # not in firms_country/: build_fires.py would take it in
    if not p.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve("https://firms.modaps.eosdis.nasa.gov/data/country/modis/2002/modis_2002_India.csv", p)
        except Exception as e:
            print("2002 FIRMS file:", e)
            return None
    _, _, _, poly = region()
    from shapely.prepared import prep
    pp = prep(poly)
    sp = ROOT / "data" / "raw" / "firms_static_cells.json"
    static = {tuple(c) for c in json.loads(sp.read_text()).get("cells", [])} if sp.exists() else set()
    a, v = season_idx(FIRST_YEAR), np.zeros(NDAYS, int)
    with open(p) as fh:
        for r in csv.DictReader(fh):
            if r["satellite"] != "Aqua" or r.get("type", "0") not in ("0", ""):
                continue
            k = dt.date.fromisoformat(r["acq_date"]).timetuple().tm_yday - 1 - a
            if not 0 <= k < NDAYS:
                continue
            lat, lon = float(r["latitude"]), float(r["longitude"])
            if (round(lon / 0.01), round(lat / 0.01)) in static or not pp.contains(shape({"type": "Point", "coordinates": [lon, lat]})):
                continue
            v[k] += 1
    return v.tolist()


# ---------------------------------------------------------------- regression
def ols(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm) * (y - ym)).sum() / sxx
    a = ym - b * xm
    res = y - (a + b * x)
    se = math.sqrt((res ** 2).sum() / (n - 2))
    r2 = 1 - (res ** 2).sum() / ((y - ym) ** 2).sum()
    try:
        from scipy.stats import t
        tq = float(t.ppf(0.975, n - 2))
    except Exception:
        tq = 2.1
    return {"slope": b, "intercept": a, "r2": r2, "se": se, "n": n, "x_mean": xm, "sxx": sxx, "t975": tq}


def predict(f, x):
    p = f["intercept"] + f["slope"] * x
    h = f["t975"] * f["se"] * math.sqrt(1 + 1 / f["n"] + (x - f["x_mean"]) ** 2 / f["sxx"])
    return p, p - h, p + h


def rnd(f):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}


def main():
    nd = ndvi_years()
    daily, last = daily_counts()
    cur = last.year
    a = season_idx(cur)
    k = min(NDAYS, max(0, last.timetuple().tm_yday - a))          # days of the current season observed so far
    years = sorted(y for y in daily if str(y) in nd)
    rows = []
    for y in years:
        v = daily[y]
        complete = y < cur or k >= NDAYS
        rows.append({"year": y, "ndvi": nd[str(y)]["ndvi"], "valid_frac": nd[str(y)]["valid_frac"], "source": nd[str(y)]["source"],
                     "season": sum(v) if complete else None, "to_date": sum(v[:k]) if k else 0, "complete": complete,
                     "cum": np.cumsum(v).tolist()})
    fy = [r for r in rows if r["year"] in FIT_YEARS and r["complete"]]
    fit = ols([r["ndvi"] for r in fy], [r["season"] for r in fy])
    fit_td = ols([r["ndvi"] for r in fy], [r["to_date"] for r in fy]) if k else None
    for r in rows:
        p, lo, hi = predict(fit, r["ndvi"])
        r["pred"], r["pred_lo"], r["pred_hi"] = round(p), round(lo), round(hi)
        if r["complete"]:
            r["ratio"] = round(r["season"] / p, 3)
        if fit_td:
            p2, lo2, hi2 = predict(fit_td, r["ndvi"])
            r["pred_to_date"], r["pred_to_date_lo"], r["pred_to_date_hi"] = round(p2), round(max(lo2, 0)), round(hi2)
            r["ratio_to_date"] = round(r["to_date"] / p2, 3) if p2 > 0 else None
    # median share of the season total reached by each day, fit years: turns a season prediction into a day-by-day path
    share = np.median(np.array([np.asarray(r["cum"]) / r["season"] for r in fy if r["season"]]), axis=0)
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "region": "Punjab + Haryana",
        "season": "1 Oct - 30 Nov",
        "current_year": cur, "last_detection": last.isoformat(), "days_observed": k,
        "fit_years": [FIT_YEARS[0], FIT_YEARS[-1]],
        "fit": rnd(fit), "fit_to_date": rnd(fit_td) if fit_td else None,
        "typical_share": [round(float(s), 4) for s in share],
        "years": rows,
        "method": ("NDVI: Aqua MODIS MYD13Q1 v061 16-day composite starting day 249 (6 Sep), mean of good/marginal 250 m pixels "
                   "inside Punjab + Haryana. Fires: Aqua MODIS detections 1 Oct - 30 Nov in the same states, static sources removed. "
                   f"Ordinary least squares on {FIT_YEARS[0]}-{FIT_YEARS[-1]}; 95% prediction intervals. Refit on every refresh; "
                   "the count-to-date fit uses each year's count up to the same day as the current season."),
        "sources": [
            {"name": "MYD13Q1 v061 (NASA LP DAAC) via Microsoft Planetary Computer", "url": "https://planetarycomputer.microsoft.com/dataset/modis-13Q1-061"},
            {"name": "ORNL DAAC MODIS web service (years Planetary Computer lacks)", "url": "https://modis.ornl.gov/data/modis_webservice.html"},
            {"name": "NASA FIRMS MODIS Collection 6.1", "url": "https://firms.modaps.eosdis.nasa.gov/"},
            {"name": "Jethva et al. 2019, Sci Rep 9:16594", "url": "https://doi.org/10.1038/s41598-019-52799-x"},
            {"name": "Jethva 2022, Front Earth Sci 10:1047278", "url": "https://doi.org/10.3389/feart.2022.1047278"},
            {"name": "Jethva 2026, Remote Sensing 18(10):1506", "url": "https://doi.org/10.3390/rs18101506"},
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    c = next(r for r in rows if r["year"] == cur) if any(r["year"] == cur for r in rows) else None
    print(f"fit {FIT_YEARS[0]}-{FIT_YEARS[-1]}: y = {fit['slope']:.0f}x {fit['intercept']:+.0f}, R2 {fit['r2']:.3f}, SE {fit['se']:.0f}")
    if c:
        print(f"{cur}: NDVI {c['ndvi']}, predicted {c['pred']} ({c['pred_lo']}-{c['pred_hi']}); to day {k}: {c['to_date']} vs {c.get('pred_to_date')}")


if __name__ == "__main__":
    main()
