#!/usr/bin/env python3
"""Pilot: CEEW "Beyond Single Sources" (Oct 2026) burnt-field method with open imagery only.

CEEW (Ignatious et al.) mark a 10 m Sentinel-2 pixel burnt where BAIS2 >= 0.78 and Tasseled Cap brightness
(TBI) <= 0.34, mask to ESRI cropland minus OSM roads, and fill the days between Sentinel-2 passes with
PlanetScope (burnt where 35.78 <= BAI <= 160.08). A field counts as burnt at >= 5 burnt pixels; "newly burnt"
means burnt on day N+1 but not on day N. PlanetScope is commercial, so the gap days here come from NASA HLS
Landsat 8/9 (hls2-l30, 30 m, radiometrically harmonised to Sentinel-2) with the same BAI rule, which needs only
red and NIR, as PlanetScope did. Google ALU field polygons need a login, so "fields" are 8-connected patches
of burnt pixels (>= 5 pixels).

The rule is checked against the failure that sank this project's earlier Sentinel-2 rule (ARCHITECTURE.md
"Gotchas"): harvested-but-unburnt paddy looks like char. So the same box is run for 2021 (peak), 2024 and 2025
(few fires) and compared with VIIRS detections and MODIS MCD64A1, and patches are spot-checked on chips.

Imagery: Microsoft Planetary Computer (keyless STAC, SAS-signed COGs): sentinel-2-l2a (10/20 m),
hls2-s30 (only where sentinel-2-l2a is missing, e.g. most of Oct 2021), hls2-l30.

Outputs (data/raw/burnfield_pilot/)
  aux/{crop,roads}.tif, aux/villages.json          10 m grid (EPSG:32643) masks
  <year>/scenes/<date>_<src>.tif                   band1 CEEW rule, band2 BAI rule: 0 unburnt crop,
                                                   1 burnt, 2 cloud/no data, 3 not crop or road
  <year>/scenes/<date>_<src>_rgb.tif               true colour, 0-0.25 reflectance stretched to 0-255
  <year>/patches.csv, results.json, figures/*.png, chips/*.png
Usage: burnfield_pilot.py aux | scenes --year 2024 | analyse | chips
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as warp_xy
from rasterio.warp import transform_geom
from rasterio.windows import from_bounds
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "burnfield_pilot"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
CRS = "EPSG:32643"
RES = 10.0
# CEEW's ~1,600 km2 ROI over Sangrur/Patiala in the 2-3 day Sentinel-2 strip (their Fig. 1), widened to hold
# the four villages OSM can place. Kakrala has two candidates inside; Mansurpur is not in OSM or Nominatim.
BBOX = [75.95, 29.93, 76.27, 30.42]
VILLAGES = {"Phaguwala": (76.0026, 30.2563), "Khetla": (76.0072, 30.0292), "Kakrala (Samana)": (76.1291, 30.0725),
            "Kakrala (Nabha)": (76.0899, 30.3699), "Burar": (76.0656, 29.9915)}
VILLAGE_R = 1500.0           # m; Punjab villages average ~5-7 km2, no village boundaries are open
CEEW = {"Phaguwala": ([18, 24, 36], [50, 34, 167]), "Khetla": ([36, 58, 31], [69, 82, 457]),
        "Kakrala": ([33, 27, 35], [78, 102, 338]), "Mansurpur": ([32, 47, 5], [113, 81, 124]),
        "Burar": ([18, 12, 8], [38, 53, 134])}
# CEEW sets (Table 1): Sentinel-2 days and their PlanetScope days. Only 24 Oct has an open (Landsat) image.
SETS = {"Set 1": ["2024-10-23", "2024-10-24", "2024-10-25"], "Set 2": ["2024-10-28", "2024-10-29", "2024-10-30"],
        "Set 3": ["2024-11-04", "2024-11-05", "2024-11-06", "2024-11-07"]}
SEASON = ("10-15", "11-15")
YEARS = [2021, 2024, 2025]
MIN_PIX = 5
BAI_LO, BAI_HI = 35.78, 160.08
FIRES = ROOT / "data" / "fires"
# quantisation box of the points files as built (summary.json), which can differ from build_fires.py's constant
FW, FS, FE, FN = json.loads((FIRES / "summary.json").read_text())["bbox"]
VIIRS = (2, 3, 4)
MODIS_DIR = ROOT / "data" / "raw" / "burnscar"

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("VSI_CACHE", "TRUE")


def get_json(url, body=None, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


_tok = {}


def sign(href, coll):
    if coll not in _tok or _tok[coll][1] < time.time():
        _tok[coll] = (get_json(TOKEN.format(coll))["token"], time.time() + 1800)
    return f"{href}?{_tok[coll][0]}"


def grid():
    xs, ys = warp_xy("EPSG:4326", CRS, [BBOX[0], BBOX[2], BBOX[0], BBOX[2]], [BBOX[1], BBOX[1], BBOX[3], BBOX[3]])
    x0, x1 = math.floor(min(xs) / 60) * 60, math.ceil(max(xs) / 60) * 60
    y0, y1 = math.floor(min(ys) / 60) * 60, math.ceil(max(ys) / 60) * 60
    w, h = int((x1 - x0) / RES), int((y1 - y0) / RES)
    return from_origin(x0, y1, RES, RES), w, h, (x0, y0, x1, y1)


TR, W, H, GB = grid()


def profile(count=1, dtype="uint8", **kw):
    p = dict(driver="GTiff", width=W, height=H, count=count, dtype=dtype, crs=CRS, transform=TR,
             compress="deflate", tiled=True, blockxsize=512, blockysize=512)
    p.update(kw)
    return p


def search(coll, start, end, bbox=BBOX):
    return get_json(f"{STAC}/search", {"collections": [coll], "bbox": bbox, "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
                                       "limit": 500})["features"]


def read_into(href, arr, resampling):
    """Read the part of a UTM 43N COG that overlaps the grid into arr (only where arr is still unset)."""
    with rasterio.open(href) as src:
        b = src.bounds
        ix0, iy0, ix1, iy1 = max(b.left, GB[0]), max(b.bottom, GB[1]), min(b.right, GB[2]), min(b.top, GB[3])
        if ix0 >= ix1 or iy0 >= iy1:
            return
        c0, r0 = int(round((ix0 - GB[0]) / RES)), int(round((GB[3] - iy1) / RES))
        c1, r1 = int(round((ix1 - GB[0]) / RES)), int(round((GB[3] - iy0) / RES))
        if str(src.crs) != CRS:
            with WarpedVRT(src, crs=CRS, transform=TR, width=W, height=H, resampling=resampling) as vrt:
                data = vrt.read(1)[r0:r1, c0:c1]
        else:
            data = src.read(1, window=from_bounds(ix0, iy0, ix1, iy1, src.transform),
                            out_shape=(r1 - r0, c1 - c0), resampling=resampling)
        nd = src.nodata
    sub = arr[r0:r1, c0:c1]
    empty = sub == FILL
    ok = empty & (data != nd) if nd is not None else empty
    sub[ok] = data[ok]


FILL = -32768


# ---------------------------------------------------------------- aux layers
def build_aux():
    adir = OUT / "aux"
    adir.mkdir(parents=True, exist_ok=True)
    # ESRI / Impact Observatory 10 m LULC, class 5 = crops. CEEW used 2024; Planetary Computer stops at 2023.
    feats = search("io-lulc-annual-v02", "2023-01-01", "2023-12-31")
    lulc = np.zeros((H, W), np.uint8)
    for f in feats:
        href = sign(f["assets"]["data"]["href"], "io-lulc-annual-v02")
        with rasterio.open(href) as src, WarpedVRT(src, crs=CRS, transform=TR, width=W, height=H,
                                                    resampling=Resampling.nearest) as vrt:
            d = vrt.read(1)
        lulc = np.where(lulc == 0, d, lulc)
    crop = (lulc == 5).astype(np.uint8)
    with rasterio.open(adir / "crop.tif", "w", **profile()) as dst:
        dst.write(crop, 1)
    # OSM roads (Overpass), burnt into the grid with all_touched so every road is at least one pixel wide
    q = (f"[out:json][timeout:180];way[\"highway\"~\"motorway|trunk|primary|secondary|tertiary|unclassified|"
         f"residential|service|track|living_street|road\"]({BBOX[1]},{BBOX[0]},{BBOX[3]},{BBOX[2]});out geom;")
    cache = adir / "osm_roads.json"
    for i in range(6):
        if cache.exists():
            break
        try:
            req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                         data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": "stubble-fire-watch-burnfield-pilot/1.0"})
            cache.write_bytes(urllib.request.urlopen(req, timeout=300).read())
        except Exception as e:
            print("overpass:", e)
            time.sleep(30 * (i + 1))
    ways = json.loads(cache.read_text())["elements"]
    major = {"motorway", "trunk", "primary", "secondary"}
    shapes = []
    for w_ in ways:
        g = {"type": "LineString", "coordinates": [(p["lon"], p["lat"]) for p in w_.get("geometry", [])]}
        if len(g["coordinates"]) < 2:
            continue
        gu = transform_geom("EPSG:4326", CRS, g)
        if w_["tags"].get("highway") in major:
            from shapely.geometry import shape
            gu = shape(gu).buffer(10).__geo_interface__
        shapes.append((gu, 1))
    roads = rasterize(shapes, out_shape=(H, W), transform=TR, all_touched=True, dtype="uint8")
    with rasterio.open(adir / "roads.tif", "w", **profile()) as dst:
        dst.write(roads, 1)
    vil = {}
    for name, (lon, lat) in VILLAGES.items():
        x, y = warp_xy("EPSG:4326", CRS, [lon], [lat])
        vil[name] = {"lon": lon, "lat": lat, "x": x[0], "y": y[0], "radius_m": VILLAGE_R}
    (adir / "villages.json").write_text(json.dumps(vil, indent=1))
    print(f"grid {W}x{H} ({W * H / 1e4:.0f} km2); crop {crop.mean():.1%}; roads {len(ways)} ways, "
          f"{roads.mean():.1%} of pixels; {lulc.size} px")


def masks():
    with rasterio.open(OUT / "aux" / "crop.tif") as a, rasterio.open(OUT / "aux" / "roads.tif") as b:
        return (a.read(1) == 1) & (b.read(1) == 0)


# ---------------------------------------------------------------- scenes
S2_BANDS = ["B02", "B03", "B04", "B06", "B07", "B08", "B8A", "B11", "B12"]
L30_BANDS = {"B02": "B02", "B03": "B03", "B04": "B04", "B08": "B05", "B11": "B06", "B12": "B07"}


def scene_list(year):
    """{(date, src): [items]} for 10 Oct .. 20 Nov; HLS S30 only for dates sentinel-2-l2a lacks."""
    s, e = f"{year}-10-10", f"{year}-11-20"
    out = {}
    for coll, src in (("sentinel-2-l2a", "s2"), ("hls2-s30", "s30"), ("hls2-l30", "l30")):
        for f in search(coll, s, e):
            out.setdefault((f["properties"]["datetime"][:10], src), []).append(f)
    s2d = {d for d, s_ in out if s_ == "s2"}
    return {k: v for k, v in sorted(out.items()) if not (k[1] == "s30" and k[0] in s2d)}


def load_bands(items, coll, names, assets):
    arrs = {n: np.full((H, W), FILL, np.int32) for n in names}
    jobs = []
    for it in items:
        for n, a in zip(names, assets):
            if a in it["assets"]:
                rs = Resampling.nearest if a in ("SCL", "Fmask") or coll == "hls2-l30" else Resampling.bilinear
                jobs.append((sign(it["assets"][a]["href"], coll), arrs[n], rs))
    # items of one date are filled in order, so run each item's bands in parallel but items one by one
    per = len(jobs) // max(1, len(items))
    with ThreadPoolExecutor(10) as ex:
        for i in range(0, len(jobs), per):
            list(ex.map(lambda j: read_into(*j), jobs[i:i + per]))
    return arrs


def near_cloud(cloud, px=100):
    """True within ~1 km of cloud: L2A/HLS shadow flags are only trusted there (char is often called shadow)."""
    if not cloud.any():
        return cloud
    small = cloud[::10, ::10]
    grown = ndimage.binary_dilation(small, iterations=px // 10)
    return np.repeat(np.repeat(grown, 10, 0), 10, 1)[:H, :W]


def classify_scene(year, date, src, items, crop):
    sdir = OUT / str(year) / "scenes"
    sdir.mkdir(parents=True, exist_ok=True)
    out = sdir / f"{date}_{src}.tif"
    if out.exists():
        return json.loads(out.with_suffix(".json").read_text())
    t0 = time.time()
    if src == "s2":
        arrs = load_bands(items, "sentinel-2-l2a", S2_BANDS + ["SCL"], S2_BANDS + ["SCL"])
        base = items[0]["properties"].get("s2:processing_baseline", "00.00")
        off = 1000 if float(base) >= 4.0 else 0
        scl = arrs.pop("SCL")
        nodata = (scl == FILL) | (scl == 0)
        cloud = np.isin(scl, (8, 9, 10))
        bad = nodata | cloud | np.isin(scl, (1, 11)) | ((scl == 3) & near_cloud(cloud))
        r = {k: (v.astype(np.float32) - off) / 10000 for k, v in arrs.items()}
        for k, v in arrs.items():
            bad |= v == FILL
    else:
        coll = "hls2-s30" if src == "s30" else "hls2-l30"
        names = S2_BANDS if src == "s30" else list(L30_BANDS)
        assets = names if src == "s30" else [L30_BANDS[n] for n in names]
        arrs = load_bands(items, coll, names + ["Fmask"], assets + ["Fmask"])
        fm = arrs.pop("Fmask")
        nodata = (fm == FILL) | (fm == 255)
        fmv = np.where(nodata, 0, fm)
        cloud = ((fmv >> 1) & 1).astype(bool) | ((fmv >> 2) & 1).astype(bool)
        bad = nodata | cloud | (((fmv >> 3) & 1).astype(bool) & near_cloud(cloud))
        r = {}
        for k, v in arrs.items():
            bad |= (v == FILL) | (v == -9999)
            r[k] = v.astype(np.float32) / 10000
        r.setdefault("B8A", r["B08"])
    del arrs
    with np.errstate(all="ignore"):
        red, nir = r["B04"], r["B08"]
        bai = 1.0 / ((0.1 - red) ** 2 + (0.06 - nir) ** 2)
        bai_burn = (bai >= BAI_LO) & (bai <= BAI_HI)
        if "B06" in r:
            # CEEW Annexure 1 (after Deshpande et al. 2022 / Filipponi 2018); TBI after Shi & Xu (2019)
            bais2 = (1 - np.sqrt(r["B06"] * r["B07"] * r["B08"] / red)) * \
                    ((r["B12"] - r["B8A"]) / np.sqrt(r["B12"] + r["B8A"]) + 1)
            tbi = (0.3510 * r["B02"] + 0.3813 * r["B03"] + 0.3437 * red + 0.7196 * nir + 0.2396 * r["B11"]
                   + 0.1949 * r["B12"])
            ceew = (bais2 >= 0.78) & (tbi <= 0.34)
        else:
            ceew = bai_burn       # Landsat stands in for PlanetScope: BAI rule only
    bad |= ~np.isfinite(red) | (red <= 0) | (nir <= 0)

    def code(burn):
        c = np.where(burn, 1, 0).astype(np.uint8)
        c[bad] = 2
        c[~crop] = 3
        return c
    c1, c2 = code(ceew), code(bai_burn)
    with rasterio.open(out, "w", **profile(count=2)) as dst:
        dst.write(c1, 1)
        dst.write(c2, 2)
    rgb = np.stack([np.clip(r[b] / 0.25 * 255, 0, 255) for b in ("B04", "B03", "B02")]).astype(np.uint8)
    rgb[:, nodata] = 0
    with rasterio.open(out.with_name(out.stem + "_rgb.tif"), "w",
                       **profile(count=3, compress="jpeg", photometric="ycbcr", jpeg_quality=88)) as dst:
        dst.write(rgb)
    valid = crop & ~bad
    st = {"date": date, "src": src, "items": [i["id"] for i in items], "valid_crop_frac": float(valid.sum() / crop.sum()),
          "ceew_burnt_frac": float((c1 == 1).sum() / max(1, valid.sum())),
          "bai_burnt_frac": float((c2 == 1).sum() / max(1, valid.sum())), "secs": round(time.time() - t0)}
    out.with_suffix(".json").write_text(json.dumps(st))
    return st


def run_scenes(year, only=None):
    crop = masks()
    for (date, src), items in scene_list(year).items():
        if only and date not in only:
            continue
        st = classify_scene(year, date, src, items, crop)
        print(year, date, src, f"valid {st['valid_crop_frac']:.0%} ceew {st['ceew_burnt_frac']:.1%} "
              f"bai {st['bai_burnt_frac']:.1%} ({st['secs']} s)", flush=True)


# ---------------------------------------------------------------- analysis
def scenes_of(year):
    out = []
    for js in sorted((OUT / str(year) / "scenes").glob("*.json")):
        st = json.loads(js.read_text())
        st["path"] = js.with_suffix(".tif")
        out.append(st)
    return out


def read_cls(st, band=1):
    with rasterio.open(st["path"]) as src:
        return src.read(band)


def new_patches(cur, prior_burnt, prior_valid):
    """Patches (8-connected, >= MIN_PIX) burnt in cur, < MIN_PIX burnt before and >= 50% observed before."""
    lab, n = ndimage.label(cur == 1, structure=np.ones((3, 3)))
    if n == 0:
        return lab, np.zeros(0, bool), np.zeros(0), np.zeros(0)
    idx = np.arange(1, n + 1)
    size = ndimage.sum_labels(np.ones_like(lab), lab, idx)
    pb = ndimage.sum_labels(prior_burnt, lab, idx)
    pv = ndimage.sum_labels(prior_valid, lab, idx) / size
    is_new = (size >= MIN_PIX) & (pb < MIN_PIX) & (pv >= 0.5)
    return lab, is_new, size, pv


def run_sequence(scn, band=1, region=None, collect=None):
    """Walk scenes in date order; count newly burnt patches against everything seen before in the sequence."""
    prior_b = prior_v = None
    rows = []
    for i, st in enumerate(scn):
        c = read_cls(st, band)
        valid = c <= 1
        if i == 0:
            prior_b, prior_v = c == 1, valid
            rows.append({"date": st["date"], "src": st["src"], "new_patches": None, "new_ha": None,
                         "burnt_ha": float((c == 1).sum() / 100)})
            continue
        lab, is_new, size, pv = new_patches(c, prior_b, prior_v)
        new_ids = np.flatnonzero(is_new) + 1
        newmask = np.isin(lab, new_ids) if len(new_ids) else np.zeros((H, W), bool)
        r = {"date": st["date"], "src": st["src"], "new_patches": int(is_new.sum()), "new_ha": float(size[is_new].sum() / 100),
             "burnt_ha": float((c == 1).sum() / 100)}
        if region is not None:
            r["villages"] = {}
            for name, m in region.items():
                ids = np.unique(lab[m & newmask])
                r["villages"][name] = int((ids > 0).sum())
        if collect is not None and len(new_ids):
            com = ndimage.center_of_mass(np.ones_like(lab), lab, new_ids)
            for pid, (cy, cx) in zip(new_ids, com):
                collect.append({"date": st["date"], "src": st["src"], "prev": scn[i - 1]["date"], "prev_src": scn[i - 1]["src"],
                                "row": int(cy), "col": int(cx), "pixels": int(size[pid - 1])})
        rows.append(r)
        prior_b |= c == 1
        prior_v |= valid      # "new" needs the patch seen unburnt on at least one earlier pass
    return rows


def village_masks():
    vil = json.loads((OUT / "aux" / "villages.json").read_text())
    yy, xx = np.mgrid[0:H, 0:W]
    out = {}
    for name, v in vil.items():
        c, r = (v["x"] - GB[0]) / RES, (GB[3] - v["y"]) / RES
        out[name] = (xx - c) ** 2 + (yy - r) ** 2 <= (v["radius_m"] / RES) ** 2
    return out


def viirs_points(year):
    raw = np.fromfile(FIRES / f"points_{year}_rice.bin", dtype=np.dtype([("lon", "<u2"), ("lat", "<u2"), ("doy", "<u2"),
                      ("hhmm", "<u2"), ("frp", "<u2"), ("did", "<u2"), ("sen", "u1"), ("conf", "u1")]))
    lon = FW + raw["lon"] / 65535 * (FE - FW)
    lat = FS + raw["lat"] / 65535 * (FN - FS)
    m = np.isin(raw["sen"], VIIRS) & (lon >= BBOX[0]) & (lon <= BBOX[2]) & (lat >= BBOX[1]) & (lat <= BBOX[3])
    x, y = warp_xy("EPSG:4326", CRS, lon[m].tolist(), lat[m].tolist())
    dates = [dt.date(year, 1, 1) + dt.timedelta(int(d) - 1) for d in raw["doy"][m]]
    return np.array(x), np.array(y), np.array(dates, dtype="datetime64[D]"), raw["sen"][m]


def modis_area(year, crop, window=SEASON):
    """MCD64A1 burned km2 inside the box for a window (whole box, and its cropland share)."""
    p = MODIS_DIR / str(year) / "mosaic.tif"
    if not p.exists():
        return None
    s0 = dt.date(year, 9, 15)
    d0 = (dt.date(year, *map(int, window[0].split("-"))) - s0).days
    d1 = (dt.date(year, *map(int, window[1].split("-"))) - s0).days
    with rasterio.open(p) as src, WarpedVRT(src, crs=CRS, transform=TR, width=W, height=H,
                                            resampling=Resampling.nearest) as vrt:
        v = vrt.read(1)
    burned = (v >= 10 + d0) & (v <= 10 + d1)
    season = v >= 10
    return {"window_km2": float(burned.sum() / 1e4), "window_crop_km2": float((burned & crop).sum() / 1e4),
            "season_km2": float(season.sum() / 1e4), "mask": burned}


def analyse():
    crop = masks()
    crop_ha = crop.sum() / 100
    res = {"grid": {"w": W, "h": H, "km2": W * H / 1e4, "crop_km2": float(crop_ha / 100), "bbox": BBOX}}
    fig_dir = OUT / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    vm = village_masks()

    # --- CEEW sets, 2024: Sentinel-2 only vs Sentinel-2 + open gap-fill
    sc24 = {s["date"] + s["src"]: s for s in scenes_of(2024)}
    sets = {}
    for name, days in SETS.items():
        s2 = [sc24[d + "s2"] for d in days if d + "s2" in sc24]
        # one image per day as in CEEW Table 1: Sentinel-2 where it flew, Landsat (for PlanetScope) otherwise
        fused = [sc24[d + "s2"] if d + "s2" in sc24 else sc24[d + "l30"] for d in days
                 if d + "s2" in sc24 or (d + "l30" in sc24 and sc24[d + "l30"]["valid_crop_frac"] >= 0.2)]
        sets[name] = {"days": days, "s2": [s["date"] for s in s2], "fused": [f"{s['date']} {s['src']}" for s in fused],
                      "s2_rows": run_sequence(s2, region=vm), "fused_rows": run_sequence(fused, region=vm)}
    res["sets_2024"] = sets

    # --- placebo: Landsat on the SAME morning as Sentinel-2 (overpasses ~20 min apart, both before the
    # afternoon burning), so every "newly burnt" patch it adds is a sensor/rule artefact, not a new fire
    res["placebo_same_day"] = []
    for year in YEARS:
        sc = {s["date"] + s["src"]: s for s in scenes_of(year)}
        for k, st in sc.items():
            if st["src"] != "l30" or st["valid_crop_frac"] < 0.2:
                continue
            for lag, kind in ((0, "same morning"), (1, "next day")):
                d0 = str(np.datetime64(st["date"]) - np.timedelta64(lag, "D"))
                base = sc.get(d0 + "s2") or sc.get(d0 + "s30")
                if not base:
                    continue
                rows = run_sequence([base, st], region=vm)
                vkm2 = float(((read_cls(base) <= 1) & (read_cls(st) <= 1)).sum() / 1e4)
                res["placebo_same_day"].append({"year": year, "kind": kind, "s2": d0, "l30": st["date"],
                                                "both_valid_km2": vkm2, "new_patches": rows[1]["new_patches"],
                                                "new_km2": rows[1]["new_ha"] / 100,
                                                "new_patches_per_100km2": 100 * rows[1]["new_patches"] / max(1, vkm2),
                                                "villages": rows[1]["villages"]})

    # --- season sequences per year, S2-only and fused, plus first-burn maps
    patches = []
    res["years"] = {}
    for year in YEARS:
        scn = [s for s in scenes_of(year) if f"{year}-{SEASON[0]}" <= s["date"] <= f"{year}-{SEASON[1]}"
               and s["valid_crop_frac"] >= 0.2]
        s2 = [s for s in scn if s["src"] in ("s2", "s30")]
        fused = sorted(scn, key=lambda s: (s["date"], s["src"] == "l30"))
        coll = []
        rows_s2 = run_sequence(s2, collect=coll)
        rows_f = run_sequence(fused)
        for p in coll:
            p["year"] = year
        patches += coll
        # union and first-burn date, plus how often a burnt pixel stays "burnt" (char fades in days; damp
        # stubble and tilled soil persist)
        ever = np.zeros((H, W), bool)
        nflag = np.zeros((H, W), np.uint8)
        nobs = np.zeros((H, W), np.uint8)
        first = np.zeros((H, W), np.int16)
        for st in s2:
            c = read_cls(st)
            d = (np.datetime64(st["date"]) - np.datetime64(f"{year}-{SEASON[0]}")).astype(int) + 1
            first[(c == 1) & ~ever] = d
            ever |= c == 1
            nflag += (c == 1)
            nobs += (c <= 1)
        ever_l = ever.copy()
        for st in fused:
            if st["src"] == "l30":
                ever_l |= read_cls(st) == 1
        with rasterio.open(OUT / str(year) / "first_burn_s2.tif", "w", **profile(dtype="int16")) as dst:
            dst.write(first, 1)
        vx, vy, vd, vs = viirs_points(year)
        w0, w1 = np.datetime64(f"{year}-{SEASON[0]}"), np.datetime64(f"{year}-{SEASON[1]}")
        inwin = (vd >= w0) & (vd <= w1)
        # VIIRS association: share of season-flagged crop within 750 m of a VIIRS detection, vs share of all crop
        near = np.zeros((H, W), bool)
        cc = ((vx[inwin] - GB[0]) / RES).astype(int)
        rr = ((GB[3] - vy[inwin]) / RES).astype(int)
        ok = (cc >= 0) & (cc < W) & (rr >= 0) & (rr < H)
        near[rr[ok], cc[ok]] = True
        near = ndimage.binary_dilation(near[::5, ::5], iterations=15)   # 50 m cells, 750 m
        near = np.repeat(np.repeat(near, 5, 0), 5, 1)[:H, :W]
        persist = nflag[ever & crop].astype(float) / np.maximum(1, nobs[ever & crop])
        # like-for-like windows: passes up to 9 Nov only (2024 is cloudy after), and 1-15 Nov (MODIS 2025 has
        # only November on Planetary Computer)
        n9 = (np.datetime64(f"{year}-11-09") - np.datetime64(f"{year}-{SEASON[0]}")).astype(int) + 1
        nov1 = (np.datetime64(f"{year}-11-01") - np.datetime64(f"{year}-{SEASON[0]}")).astype(int) + 1
        by_nov9 = float(((first > 0) & (first <= n9) & crop).sum() / crop.sum())
        new_nov = float(((first >= nov1) & crop).sum() / 1e4)
        mod_nov = modis_area(year, crop, ("11-01", SEASON[1]))
        mod = modis_area(year, crop)
        # spatial agreement with MODIS: is the rule's flagged cropland more often inside MODIS burned cells than
        # cropland in general? (1.0 = no association)
        mod_enrich = None
        if mod and mod["mask"].any():
            mod_enrich = float(((ever & crop & mod["mask"]).sum() / max(1, (ever & crop).sum()))
                               / max(1e-9, (crop & mod["mask"]).sum() / crop.sum()))
        for m_ in (mod_nov, mod):
            if m_:
                m_.pop("mask")
        res["years"][year] = {
            "scenes_s2": [f"{s['date']} {s['src']} ({s['valid_crop_frac']:.0%} clear)" for s in s2],
            "scenes_l30": [f"{s['date']} ({s['valid_crop_frac']:.0%} clear)" for s in fused if s["src"] == "l30"],
            "flagged_crop_frac_s2": float((ever & crop).sum() / crop.sum()),
            "flagged_crop_frac_fused": float((ever_l & crop).sum() / crop.sum()),
            "flagged_km2_s2": float((ever & crop).sum() / 1e4),
            "new_patches_s2": int(sum(r["new_patches"] or 0 for r in rows_s2)),
            "new_patches_fused": int(sum(r["new_patches"] or 0 for r in rows_f)),
            "new_km2_s2": float(sum(r["new_ha"] or 0 for r in rows_s2) / 100),
            "new_km2_fused": float(sum(r["new_ha"] or 0 for r in rows_f) / 100),
            "rows_s2": rows_s2, "rows_fused": rows_f,
            "viirs_window": int(inwin.sum()), "viirs_season": int(len(vd)),
            "viirs_snpp_window": int((inwin & (vs == 2)).sum()),   # S-NPP alone is comparable across years
            "viirs_daily": {str(d): int(n) for d, n in zip(*np.unique(vd[inwin], return_counts=True))},
            "modis": mod, "modis_enrichment": mod_enrich,
            "near_viirs_share_of_flagged": float((near & ever & crop).sum() / max(1, (ever & crop).sum())),
            "near_viirs_share_of_crop": float((near & crop).sum() / crop.sum()),
            "median_flag_persistence": float(np.median(persist)) if persist.size else None,
            "flagged_crop_frac_by_nov9": by_nov9,
            "first_flagged_nov1_15_km2": new_nov, "modis_nov1_15_km2": mod_nov and mod_nov["window_km2"],
            "viirs_snpp_nov1_15": int(((vd >= np.datetime64(f"{year}-11-01")) & (vd <= w1) & (vs == 2)).sum()),
            "pass_flagged_frac": {s_["date"]: s_["ceew_burnt_frac"] for s_ in s2},
        }
        y_ = res["years"][year]
        y_["viirs_enrichment"] = y_["near_viirs_share_of_flagged"] / max(1e-9, y_["near_viirs_share_of_crop"])
        print(year, {k: v for k, v in y_.items() if not isinstance(v, (list, dict))}, flush=True)

    with open(OUT / "patches.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(patches[0]))
        wr.writeheader()
        wr.writerows(patches)

    # --- same-day cross-sensor check of the gap-fill rule (Landsat BAI vs Sentinel-2 CEEW rule)
    res["cross_sensor"] = []
    for year in YEARS:
        sc = scenes_of(year)
        by = {(s["date"], s["src"]): s for s in sc}
        for s in sc:
            if s["src"] != "l30":
                continue
            for lag in (0, 1, -1):
                d2 = str(np.datetime64(s["date"]) + np.timedelta64(lag, "D"))
                o = by.get((d2, "s2")) or by.get((d2, "s30"))
                if o:
                    a, b = read_cls(s), read_cls(o)
                    bb = read_cls(o, 2)
                    v = (a <= 1) & (b <= 1)
                    A, B, BB = (a == 1) & v, (b == 1) & v, (bb == 1) & v
                    res["cross_sensor"].append({
                        "l30": s["date"], "s2": d2, "both_valid_km2": float(v.sum() / 1e4),
                        "l30_bai_frac": float(A.sum() / max(1, v.sum())), "s2_ceew_frac": float(B.sum() / max(1, v.sum())),
                        "s2_bai_frac": float(BB.sum() / max(1, v.sum())),
                        "iou_l30bai_s2ceew": float((A & B).sum() / max(1, (A | B).sum())),
                        "iou_l30bai_s2bai": float((A & BB).sum() / max(1, (A | BB).sum()))})
                    break
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    figures(res)


def figures(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fd = OUT / "figures"
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    ys = [str(y) for y in YEARS]
    yr = {str(k): v for k, v in res["years"].items()}

    # 1. the year test
    fig, ax = plt.subplots(1, 3, figsize=(10, 3.2))
    ax[0].bar(ys, [yr[y]["viirs_snpp_window"] for y in ys], color="#c0392b")
    ax[0].set_title("VIIRS S-NPP detections in box\n15 Oct-15 Nov")
    ax[1].bar(ys, [(yr[y]["modis"] or {}).get("window_km2", np.nan) for y in ys], color="#7f8c8d")
    ax[1].set_title("MODIS MCD64A1 burned km²\n15 Oct-15 Nov")
    ax[2].bar(ys, [100 * yr[y]["flagged_crop_frac_s2"] for y in ys], color="#2c3e50")
    ax[2].set_title("CEEW Sentinel-2 rule: % of cropland\never flagged burnt, 15 Oct-15 Nov")
    for a in ax:
        for p in a.patches:
            a.annotate(f"{p.get_height():,.0f}", (p.get_x() + p.get_width() / 2, p.get_height()), ha="center", va="bottom")
    fig.tight_layout()
    fig.savefig(fd / "year_test.png", dpi=130)
    plt.close(fig)

    # 2. newly flagged area per pass vs VIIRS per day
    fig, axs = plt.subplots(len(ys), 1, figsize=(9, 2.2 * len(ys)), sharex=False)
    for a, y in zip(axs, ys):
        rows = [r for r in yr[y]["rows_s2"] if r["new_ha"] is not None]
        d = [np.datetime64(r["date"]) for r in rows]
        a.bar(d, [r["new_ha"] / 100 for r in rows], width=1.6, color="#2c3e50", label="newly flagged km² (S2 rule)")
        a2 = a.twinx()
        vd = sorted(yr[y]["viirs_daily"].items())
        a2.plot([np.datetime64(k) for k, _ in vd], [v for _, v in vd], color="#c0392b", lw=1.2, label="VIIRS / day")
        a.set_ylabel(f"{y}\nkm² new")
        a2.set_ylabel("VIIRS")
        a.set_xlim(np.datetime64(f"{y}-{SEASON[0]}"), np.datetime64(f"{y}-{SEASON[1]}"))
    axs[0].legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(fd / "daily_new_vs_viirs.png", dpi=130)
    plt.close(fig)

    # 2b. share of cropland the rule calls burnt on each clear pass
    fig, a = plt.subplots(figsize=(7, 3.2))
    for y, col in zip(ys, ("#c0392b", "#2980b9", "#27ae60")):
        pf = sorted(yr[y]["pass_flagged_frac"].items())
        doy = [(np.datetime64(d) - np.datetime64(f"{y}-{SEASON[0]}")).astype(int) for d, _ in pf]
        a.plot(doy, [100 * v for _, v in pf], "o-", color=col, label=f"{y} (VIIRS S-NPP {yr[y]['viirs_snpp_window']:,})")
    a.set_xlabel("days after 15 Oct")
    a.set_ylabel("% of clear cropland flagged")
    a.set_title("CEEW Sentinel-2 rule, each clear pass", fontsize=9)
    a.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(fd / "per_pass_flagged.png", dpi=130)
    plt.close(fig)

    # 3. villages vs CEEW
    sets = res["sets_2024"]
    names = ["Phaguwala", "Khetla", "Kakrala (Samana)", "Kakrala (Nabha)", "Burar"]
    fig, axs = plt.subplots(1, 3, figsize=(11, 3.4), sharey=True)
    for a, sname in zip(axs, sets):
        k = list(sets).index(sname)
        ours_s2 = [sum((r.get("villages") or {}).get(n, 0) for r in sets[sname]["s2_rows"]) for n in names]
        ours_f = [sum((r.get("villages") or {}).get(n, 0) for r in sets[sname]["fused_rows"]) for n in names]
        ce_s2 = [CEEW[n.split(" (")[0]][0][k] for n in names]
        ce_f = [CEEW[n.split(" (")[0]][1][k] for n in names]
        x = np.arange(len(names))
        a.bar(x - 0.3, ce_s2, 0.2, label="CEEW S2", color="#95a5a6")
        a.bar(x - 0.1, ce_f, 0.2, label="CEEW S2+PlanetScope", color="#7f8c8d")
        a.bar(x + 0.1, ours_s2, 0.2, label="pilot S2 (patches)", color="#2980b9")
        a.bar(x + 0.3, ours_f, 0.2, label="pilot S2+Landsat", color="#1abc9c")
        a.set_xticks(x, [n.replace(" (", "\n(") for n in names], fontsize=7)
        a.set_title(f"{sname}: {', '.join(sets[sname]['fused'])}", fontsize=7)
        a.set_yscale("symlog", linthresh=10)
    axs[0].legend(fontsize=7)
    axs[0].set_ylabel("newly burnt fields / patches")
    fig.tight_layout()
    fig.savefig(fd / "villages_vs_ceew.png", dpi=130)
    plt.close(fig)

    # 4. first-burn maps
    fig, axs = plt.subplots(1, len(ys), figsize=(3.3 * len(ys), 5))
    for a, y in zip(axs, ys):
        with rasterio.open(OUT / y / "first_burn_s2.tif") as src:
            f = src.read(1, out_shape=(H // 5, W // 5), resampling=Resampling.mode).astype(float)
        f[f == 0] = np.nan
        im = a.imshow(f, cmap="inferno_r", vmin=1, vmax=32)
        a.set_title(f"{y}: {100 * yr[y]['flagged_crop_frac_s2']:.0f}% of crop flagged\n"
                    f"VIIRS S-NPP {yr[y]['viirs_snpp_window']:,}", fontsize=9)
        a.axis("off")
    fig.colorbar(im, ax=axs, shrink=0.6, label="first flagged, days after 15 Oct")
    fig.savefig(fd / "first_burn_maps.png", dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- spot-check chips
def chips(n_per=None, seed=7):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    rows = list(csv.DictReader(open(OUT / "patches.csv")))
    rng = np.random.default_rng(seed)
    n_per = n_per or {"2021": 12, "2024": 14, "2025": 12}
    pick = []
    for y, n in n_per.items():
        c = [r for r in rows if r["year"] == y and int(r["pixels"]) >= 20]
        pick += [c[i] for i in rng.choice(len(c), min(n, len(c)), replace=False)]
    # 2024 Landsat gap-fill detections (Set 1, 24 Oct) too
    sc24 = {s["date"] + s["src"]: s for s in scenes_of(2024)}
    if "2024-10-24l30" in sc24 and "2024-10-23s2" in sc24:
        prev, cur = read_cls(sc24["2024-10-23s2"]), read_cls(sc24["2024-10-24l30"])
        lab, is_new, size, _ = new_patches(cur, prev == 1, prev <= 1)
        ids = np.flatnonzero(is_new & (size >= 20)) + 1
        for pid in rng.choice(ids, min(6, len(ids)), replace=False):
            cy, cx = ndimage.center_of_mass(lab == pid)
            pick.append({"year": "2024", "date": "2024-10-24", "src": "l30", "prev": "2024-10-23", "prev_src": "s2",
                         "row": int(cy), "col": int(cx), "pixels": int(size[pid - 1])})
    cdir = OUT / "chips"
    cdir.mkdir(parents=True, exist_ok=True)
    half = 25
    out = []
    for i, p in enumerate(pick):
        y = p["year"]
        r0, c0 = int(p["row"]) - half, int(p["col"]) - half
        if r0 < 0 or c0 < 0 or r0 + 2 * half > H or c0 + 2 * half > W:
            continue
        win = rasterio.windows.Window(c0, r0, 2 * half, 2 * half)
        sc = {s["date"] + s["src"]: s for s in scenes_of(int(y))}
        # after image: the detection scene and the next clear Sentinel-2 pass after it (char stays black for days)
        later = [s for s in scenes_of(int(y)) if s["date"] > p["date"] and s["src"] in ("s2", "s30")
                 and s["valid_crop_frac"] >= 0.8]
        panels = [(p["prev"], p["prev_src"]), (p["date"], p["src"])] + ([(later[0]["date"], later[0]["src"])] if later else [])
        imgs = []
        for d, s in panels:
            with rasterio.open(sc[d + s]["path"].with_name(f"{d}_{s}_rgb.tif")) as src:
                imgs.append(np.moveaxis(src.read(window=win), 0, -1))
        with rasterio.open(sc[p["date"] + p["src"]]["path"]) as src:
            cl = src.read(1, window=win)
        lab, _ = ndimage.label(cl == 1, structure=np.ones((3, 3)))
        m = lab == lab[half, half] if lab[half, half] else (cl == 1)
        edge = m & ~ndimage.binary_erosion(m)
        # fixed stretch (0-0.25 reflectance, gamma 0.7) on every chip, so "black" means dark in absolute terms;
        # the title gives the patch's median red reflectance before -> at detection (char ~0.03-0.05)
        red = [float(np.median(im[..., 0][m])) / 255 * 0.25 for im in imgs[:2]]
        fig, axs = plt.subplots(1, len(imgs) + 1, figsize=(2.6 * (len(imgs) + 1), 2.8))
        for a, im, (d, s) in zip(list(axs[:len(imgs)]) + [axs[-1]], imgs + [imgs[1]], panels + [panels[1]]):
            v = (255 * (im.astype(float) / 255) ** 0.7).astype(np.uint8)
            if a is axs[-1]:
                v[edge] = (0, 255, 255)
                a.set_title("flagged patch (cyan)", fontsize=8)
            else:
                a.contour(m, levels=[0.5], colors="cyan", linewidths=0.5, alpha=0.6)
                a.set_title(f"{d} {s}", fontsize=8)
            a.imshow(v, interpolation="nearest")
            a.axis("off")
        xs, ys_ = rasterio.transform.xy(TR, int(p["row"]), int(p["col"]))
        lon, lat = warp_xy(CRS, "EPSG:4326", [xs], [ys_])
        name = f"chip_{i:02d}_{y}_{p['date']}_{p['src']}.png"
        fig.suptitle(f"#{i:02d}  {lat[0]:.4f}N {lon[0]:.4f}E  {p['pixels']} px  red {red[0]:.3f}->{red[1]:.3f}  ({2 * half * 10} m window)", fontsize=8)
        fig.tight_layout()
        fig.savefig(cdir / name, dpi=110)
        plt.close(fig)
        out.append({**p, "chip": name, "lat": round(lat[0], 5), "lon": round(lon[0], 5),
                    "red_before": round(red[0], 4), "red_at": round(red[1], 4)})
    with open(cdir / "chips.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0]) + ["label", "note"])
        wr.writeheader()
        wr.writerows(out)
    print(len(out), "chips")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["aux", "scenes", "analyse", "chips"])
    ap.add_argument("--year", type=int, nargs="*", default=YEARS)
    ap.add_argument("--dates", nargs="*")
    a = ap.parse_args()
    if a.cmd == "aux":
        build_aux()
    elif a.cmd == "scenes":
        for y in a.year:
            run_scenes(y, a.dates)
    elif a.cmd == "analyse":
        analyse()
    else:
        chips()


if __name__ == "__main__":
    main()
