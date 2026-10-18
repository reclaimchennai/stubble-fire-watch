#!/usr/bin/env python3
"""Sentinel-2A at night: hot spots (fires, furnaces, flares) over Punjab from ESA's December 2025 night campaign.

Source: ESA's Copernicus Sentinel-2 night-time acquisitions (CDSE STAC collection
`sentinel-2-night-time-acquisitions`, public since June 2026). Sentinel-2A imaged parts of the world on its
ascending (night) pass from 3 to 22 Dec 2025. Two scenes cover Punjab:
  R012  9 Dec 2025 17:02 UTC (22:32 IST)  74.94-78.53 E, 29.59-32.32 N  (east Punjab, Haryana, Himachal, Uttarakhand)
  R055 12 Dec 2025 17:12 UTC (22:42 IST)  72.56-76.12 E, 29.15-31.89 N  (west Punjab, Pakistani Punjab)
The orthorectified product (ESA technical note ESA-EOPSM-S2A-TN-5021, doi:10.5281/zenodo.20286476) has one
float32 GeoTIFF per band, calibrated to radiance (W m-2 sr-1 um-1), EPSG:4326, ~10 m (B02-B04, B08) or
~20 m (B8A, B11, B12), nodata 65535. Geolocation uses no ground control, so it is worse than daytime S2.

At night there is no reflected sunlight, so anything bright in the short-wave infrared is emitting heat:
a flaming or smouldering fire, a flare, a furnace or kiln. B12 (2.2 um) is the most sensitive band; B11 (1.6 um)
needs hotter sources. B11 and B12 are separate detector rows read ~0.6 s apart, so noise in one is
independent of the other.

What the data look like (both scenes): radiance is quantised (B12 0.0094, B11 0.028 per DN); most pixels are 0;
noise comes as spatially correlated spikes up to ~0.94 in B12. Negative values bottom out at -0.94 (B12) and -2.84
(B11), the DN-0 floor, so a negated image can only stand in for positive noise below those values.

Detection, as excess radiance over the median of the surrounding 1 km block:
  candidate pixels   B12 excess >= 0.15, clustered 8-connected (one-pixel gaps bridged)
  tier "both"        peak B12 >= 0.5 and B11 >= 0.5 in the 3x3 neighbourhood   (two independent bands)
  tier "b12"         peak B12 >= 1.0 without B11 confirmation
The same rules run on the negated image ("mirror test"), where every cluster is noise: 0 selected in either tier,
on either scene (3,700+ negated candidates each). The b12 tier is above the mirror's floor, so positive-only
artefacts are not measured by it; chips show some b12 clusters are dotted streaks. Only "both" goes on the map.

Each hot spot is checked against NASA FIRMS (1 km), FIRMS static sources (VIIRS type 2, 2012-2025) and
ESA WorldCover 2021 (built-up share within ~150 m). Classes: industrial (static source), built-up, open.
Open means cropland, grass, scrub or forest; it still contains lone factories and brick kilns in farmland.
VNIR night lights were tried and are not usable: 100 m means are dominated by detector striping (~0.01-0.03)
and cities do not stand out even after destriping (B04 DN step 0.22, sd 0.1).

Outputs
  data/raw/s2night/<scene>/*_B??_CAL_MERGED.tif   the bands used (the 2.7 GB zips are deleted after extraction)
  data/raw/s2night/<scene>/b12_max_100m.tif        B12 max per 100 m cell, b11_max_100m.tif, vnir_mean_100m.tif
  data/raw/s2night/<scene>/candidates.json        every candidate cluster, and those on the negated image
  data/raw/s2night/s2night_all.geojson            selected clusters, both tiers, all fields (for the release)
  data/s2night/s2night.geojson                    two-band hot spots for the map (both nights), classified
  data/s2night/s2night_validation.json            mirror test, FIRMS match rates, chance baseline, classes
Usage: s2night.py fetch R012 R055 | detect R012 R055 | validate | all
       (fetch needs CDSE S3 keys: CDSE_S3_ACCESS_KEY / CDSE_S3_SECRET_KEY in the environment or ~/.config/cdse/.env)
"""
import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import sys
import warnings
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.windows import Window
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "s2night"
OUT = ROOT / "data" / "s2night"
FIRMS = ROOT / "data" / "raw" / "firms_country"
S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"
PREFIX = "Sentinel-2/MSI/S2MSI_NIGHT/2025/12"
SCENES = {
    "R012": {"product": "S2A_OPER_PRD_R012_V20251209T170223_20251209T170259", "day": "09", "utc": "2025-12-09T17:02:23Z"},
    "R055": {"product": "S2A_OPER_PRD_R055_V20251212T171220_20251212T171256", "day": "12", "utc": "2025-12-12T17:12:20Z"},
}
BANDS = ["B04", "B08", "B11", "B12"]       # B04/B08: light and flame peaks at 10 m; B11/B12: heat
NODATA = 65535.0

# detection parameters (radiance excess over the 1 km background, W m-2 sr-1 um-1)
BLK = 50                     # background block, 20 m pixels (1 km)
CAND12 = 0.15                # candidate pixels; ~2x the DLR night SWIR noise limit (mean + 3 sd ~ 0.08)
SEL_BOTH = 0.5               # selected: B12 and B11 (3x3) both >= 0.5 -> 0 clusters on the negated image
SEL_B12 = 1.0                # or B12 alone >= 1.0: above the negated-image noise plateau (-0.94 is the DN-0 floor)
SAT12 = 29.0                 # B12 saturates near 29.8-30.0 at night
STRIP, HALO = 1024, 64
MATCH_KM = 1.0
WEB_PROPS = ["date", "class", "district", "state", "country", "npix", "l12_peak", "l11_peak", "l12_sum", "t_k", "frp_mw", "landcover", "built_share", "firms_km", "firms_sat", "firms_dt_h"]
BUILT_SHARE = 0.25           # WorldCover built-up share within ~150 m above which a hot spot is called built-up


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------------ fetch
def s3_client():
    import boto3
    env = {k: os.environ[k] for k in ("CDSE_S3_ACCESS_KEY", "CDSE_S3_SECRET_KEY") if k in os.environ}
    p = Path.home() / ".config" / "cdse" / ".env"
    if len(env) < 2 and p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env.setdefault(k.strip(), v.strip().strip("'\""))
    return boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="default",
                        aws_access_key_id=env["CDSE_S3_ACCESS_KEY"], aws_secret_access_key=env["CDSE_S3_SECRET_KEY"])


def fetch(scene):
    d = RAW / scene
    if all(glob.glob(str(d / f"*_{b}_CAL_MERGED.tif")) for b in BANDS):
        log(scene, "bands present"); return
    s3, meta = s3_client(), SCENES[scene]
    pre = f"{PREFIX}/{meta['day']}/{meta['product']}/orthorectified/"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="eodata", Prefix=pre).get("Contents", []) if o["Key"].endswith(".zip")]
    if not keys:
        sys.exit(f"{scene}: no orthorectified zip under {pre}")
    d.mkdir(parents=True, exist_ok=True)
    z = RAW / f"{scene}.zip"
    log(scene, "downloading", keys[0])
    s3.download_file("eodata", keys[0], str(z) + ".part")
    os.replace(str(z) + ".part", z)
    with zipfile.ZipFile(z) as zf:          # keep only the bands used; the zip is ~2.7 GB
        for n in zf.namelist():
            if any(n.endswith(f"_{b}_CAL_MERGED.tif") for b in BANDS):
                (d / Path(n).name).write_bytes(zf.read(n))
    z.unlink()
    log(scene, "extracted", len(list(d.glob("*.tif"))), "bands")


# ------------------------------------------------------------------------------------------------ detect
def band(scene, b):
    return rasterio.open(glob.glob(str(RAW / scene / f"*_{b}_CAL_MERGED.tif"))[0])


def read_on(ds, ref, row0, nrows, scale=1, ncols=None):
    """Read rows of `ds` covering ref rows [row0, row0+nrows) and ref columns [0, ncols); `scale` 10 m pixels per ref pixel."""
    t, rt = ds.transform, ref.transform
    c0 = round((rt.c - t.c) / t.a)
    r0 = round((rt.f - t.f) / t.e) + row0 * scale
    w = Window(c0, r0, (ncols or ref.width) * scale, nrows * scale)
    return ds.read(1, window=w, boundless=True, fill_value=NODATA)


def block_median(a, valid):
    """Median per BLK x BLK block (1 km), expanded back to pixels: the local background."""
    h, w = a.shape
    H, W = -(-h // BLK) * BLK, -(-w // BLK) * BLK
    x = np.full((H, W), np.nan, "f4")
    x[:h, :w] = np.where(valid, a, np.nan)
    b = x.reshape(H // BLK, BLK, W // BLK, BLK).transpose(0, 2, 1, 3).reshape(H // BLK, W // BLK, -1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        med = np.nan_to_num(np.nanmedian(b, axis=2))
    return np.repeat(np.repeat(med, BLK, 0), BLK, 1)[:h, :w]


def planck(lam_um, T):
    c1, c2 = 1.191042e8, 1.4387752e4      # W m-2 sr-1 um4, um K
    return c1 / (lam_um ** 5 * (np.exp(c2 / (lam_um * T)) - 1.0))


_TGRID = np.arange(500.0, 2500.0, 5.0)
_RATIO = planck(1.610, _TGRID) / planck(2.190, _TGRID)


def fire_temperature(l11, l12):
    """Single-temperature fit from the B11/B12 radiance ratio (Dozier-style); returns T (K), area fraction."""
    if l11 <= 0 or l12 <= 0 or l12 >= SAT12:
        return None, None
    T = float(np.interp(l11 / l12, _RATIO, _TGRID, left=np.nan, right=np.nan))
    if not np.isfinite(T):
        return None, None
    return T, float(l12 / planck(2.190, T))


def detect_scene(scene, mirror=False):
    """Candidate clusters for one scene; mirror=True negates B11/B12 so every cluster is noise (false-alarm test)."""
    s = -1.0 if mirror else 1.0
    r12, r11 = band(scene, "B12"), band(scene, "B11")
    vnir = {b: band(scene, b) for b in ("B04", "B08")}
    H, W = r12.height, r12.width
    T = r12.transform
    px_m = (abs(T.a) * 111320 * math.cos(math.radians(31)), abs(T.e) * 110900)
    dets, nvalid = [], 0
    for row0 in range(0, H, STRIP):
        a0 = max(0, row0 - HALO)
        n = min(H, row0 + STRIP + HALO) - a0
        a12 = r12.read(1, window=Window(0, a0, W, n), boundless=True, fill_value=NODATA)
        a11 = read_on(r11, r12, a0, n)
        valid = (a12 != NODATA) & (a11 != NODATA) & np.isfinite(a12) & np.isfinite(a11)
        a12 = np.where(valid, a12 * s, 0).astype("f4"); a11 = np.where(valid, a11 * s, 0).astype("f4")
        core = slice(row0 - a0, row0 - a0 + min(STRIP, H - row0))
        nvalid += int(valid[core].sum())
        e12 = a12 - block_median(a12, valid)          # excess over the 1 km background
        e11 = a11 - block_median(a11, valid)
        cand = valid & (e12 >= CAND12)
        if not cand.any():
            continue
        lab, _ = ndimage.label(ndimage.binary_dilation(cand, np.ones((3, 3), bool)), np.ones((3, 3)))
        lab = np.where(cand, lab, 0)
        e11max = ndimage.maximum_filter(np.where(valid, e11, -99), size=3)
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            if sl is None:
                continue
            m = lab[sl] == i
            v12 = e12[sl][m]
            k = int(np.argmax(v12))
            rr, cc = np.nonzero(m)
            pr, pc = rr[k] + sl[0].start, cc[k] + sl[1].start
            if not (core.start <= pr < core.stop):
                continue
            w = np.clip(v12, 1e-6, None)
            lon, lat = T * ((cc + sl[1].start + 0.5) @ w / w.sum(), (rr + sl[0].start + 0.5) @ w / w.sum() + a0)
            l12pk, l11pk = float(v12[k]), float(e11max[sl][m].max())
            Tk, frac = fire_temperature(float(e11max[pr, pc]), float(a12[pr, pc]))
            d = {"lon": round(float(lon), 5), "lat": round(float(lat), 5), "npix": int(m.sum()),
                 "l12_peak": round(l12pk, 3), "l12_sum": round(float(v12.sum()), 3), "l11_peak": round(l11pk, 3),
                 "saturated": bool(a12[pr, pc] >= SAT12), "t_k": round(Tk) if Tk else None,
                 "area_m2": round(frac * px_m[0] * px_m[1], 2) if frac else None,
                 "frp_mw": round(frac * px_m[0] * px_m[1] * 5.670374e-8 * Tk ** 4 / 1e6, 4) if frac else None}
            dets.append(d)
        log(f"  {scene}{' mirror' if mirror else ''} rows {row0}-{row0 + STRIP}: {len(dets)} candidates")
    for d in dets:
        if d["l12_peak"] < 0.5 and d["l11_peak"] < 0.5:
            continue                                   # VNIR only for clusters that could pass selection
        for b, ds in vnir.items():                     # VNIR peak in a 110 m box at 10 m (lights, bright flames)
            r, c = ds.index(d["lon"], d["lat"])
            v = ds.read(1, window=Window(c - 5, r - 5, 11, 11), boundless=True, fill_value=NODATA)
            v = v[(v != NODATA) & np.isfinite(v)]
            d[f"{b.lower()}_peak"] = round(float(v.max()), 3) if v.size else None
    meta = {"valid_px": nvalid, "valid_km2": round(nvalid * px_m[0] * px_m[1] / 1e6), "pixel_m": [round(px_m[0], 1), round(px_m[1], 1)]}
    return dets, meta


def write_rasters(scene, f=5):
    """B12 / B11 max and VNIR (B04+B08)/2 mean per 100 m cell, for the release and for inspection."""
    r12, r11 = band(scene, "B12"), band(scene, "B11")
    b04, b08 = band(scene, "B04"), band(scene, "B08")
    H, W = r12.height // f, r12.width // f
    T = r12.transform * Affine.scale(f, f)
    prof = dict(driver="GTiff", width=W, height=H, count=1, dtype="float32", crs="EPSG:4326", transform=T,
                nodata=np.nan, compress="deflate", predictor=3, tiled=True)
    outs = {k: np.full((H, W), np.nan, "f4") for k in ("b12_max", "b11_max", "vnir_mean")}
    for R in range(0, H, 200):
        n = min(200, H - R)
        a12 = r12.read(1, window=Window(0, R * f, W * f, n * f), boundless=True, fill_value=NODATA)
        a11 = read_on(r11, r12, R * f, n * f, ncols=W * f)
        for k, a in (("b12_max", a12), ("b11_max", a11)):
            a = np.where((a == NODATA) | ~np.isfinite(a), np.nan, a).reshape(n, f, W, f)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                outs[k][R:R + n] = np.nanmax(a, axis=(1, 3))
        acc = None
        for ds in (b04, b08):
            a = read_on(ds, r12, R * f, n * f, scale=2, ncols=W * f)
            a = np.where((a == NODATA) | ~np.isfinite(a), np.nan, a).reshape(n, 2 * f, W, 2 * f)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m = np.nanmean(a, axis=(1, 3))
            acc = m if acc is None else (acc + m)
        outs["vnir_mean"][R:R + n] = acc / 2
    for k, a in outs.items():
        with rasterio.open(RAW / scene / f"{k}_100m.tif", "w", **prof) as o:
            o.write(a, 1)
    log(scene, "rasters", {k: f"{W}x{H}" for k in outs})


def detect(scenes, rasters_only=False):
    for sc in scenes:
        if rasters_only:
            write_rasters(sc); continue
        log("detect", sc)
        dets, meta = detect_scene(sc)
        mir, _ = detect_scene(sc, mirror=True)
        (RAW / sc / "candidates.json").write_text(json.dumps({"scene": sc, **SCENES[sc], **meta, "candidates": dets, "mirror": mir}))
        log(sc, len(dets), "candidates;", len(mir), "on the negated image")
        write_rasters(sc)


# ------------------------------------------------------------------------------------------------ validate
def km(lon1, lat1, lon2, lat2):
    x = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(x, (lat2 - lat1) * 110.9)


class Grid:
    """0.05 degree bucket index for nearest-neighbour lookups."""
    def __init__(self, pts):
        self.b = {}
        for i, (lon, lat) in enumerate(pts):
            self.b.setdefault((int(lon // 0.05), int(lat // 0.05)), []).append(i)
        self.p = pts

    def nearest(self, lon, lat):
        best = (1e9, None)
        gx, gy = int(lon // 0.05), int(lat // 0.05)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.b.get((gx + dx, gy + dy), []):
                    d = km(lon, lat, *self.p[i])
                    if d < best[0]:
                        best = (d, i)
        return best


def firms_rows(years=(2025,), files=("modis", "viirs-snpp", "viirs-jpss1"), bbox=(72.4, 29.0, 78.7, 32.5), date_range=None, types=None):
    out = []
    for y in years:
        for f in files:
            for c in ("India", "Pakistan"):
                p = FIRMS / f"{f}_{y}_{c}.csv"
                if not p.exists():
                    continue
                with open(p) as fh:
                    for r in csv.DictReader(fh):
                        if date_range and not (date_range[0] <= r["acq_date"] <= date_range[1]):
                            continue
                        if types and r.get("type") not in types:
                            continue
                        lon, lat = float(r["longitude"]), float(r["latitude"])
                        if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
                            t = dt.datetime.strptime(r["acq_date"] + r["acq_time"].zfill(4), "%Y-%m-%d%H%M")
                            out.append({"lon": lon, "lat": lat, "t": t, "sat": f"{r['instrument']}:{r['satellite']}",
                                        "dn": r["daynight"], "type": r.get("type", ""), "frp": float(r["frp"] or 0),
                                        "conf": r["confidence"]})
    return out


def covered(ds, lon, lat):
    r, c = ds.index(lon, lat)
    if not (0 <= r < ds.height and 0 <= c < ds.width):
        return False
    v = ds.read(1, window=Window(c, r, 1, 1))[0, 0]
    return v != NODATA and np.isfinite(v)


def tier(d):
    """Selection from the mirror test (see the module docstring): two-band, B12-only above the noise plateau, or none."""
    if d["l12_peak"] >= SEL_BOTH and d["l11_peak"] >= SEL_BOTH:
        return "both"
    if d["l12_peak"] >= SEL_B12:
        return "b12"
    return None


def firms_groups(F, t0):
    groups = {}
    for r in F:
        h = (r["t"] - t0).total_seconds() / 3600
        if r["sat"].startswith("VIIRS") and r["dn"] == "N" and 0 < h < 6:
            g = "viirs_night_after"            # ~01:30 IST, 3 h after Sentinel-2
        elif r["sat"].startswith("VIIRS") and r["dn"] == "N" and -24 < h < 0:
            g = "viirs_night_before"           # previous night, ~21 h before
        elif r["dn"] == "D" and -12 < h < 0:
            g = "same_day_daytime"             # 10:30-14:30 IST, 8-12 h before
        elif r["sat"].startswith("MODIS") and r["dn"] == "N" and -3 < h < 3:
            g = "modis_night_same_pass"        # Terra ~21:00-22:30 IST
        else:
            continue
        groups.setdefault(g, []).append(r)
    return groups


def validate():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    static = firms_rows(years=range(2012, 2026), files=("viirs-snpp", "viirs-jpss1"), types={"2"})
    static_g = Grid([(r["lon"], r["lat"]) for r in static])
    log("FIRMS static (type 2) VIIRS detections 2012-2025 in the box:", len(static))
    res, sel, cov = {}, {}, {}
    reg = Regions()
    for sc, meta in SCENES.items():
        D = json.loads((RAW / sc / "candidates.json").read_text())
        t0 = dt.datetime.strptime(meta["utc"], "%Y-%m-%dT%H:%M:%SZ")
        r12 = band(sc, "B12")
        cov[sc] = r12
        L, B, R, Tp = r12.bounds
        F = [r for r in firms_rows(date_range=((t0 - dt.timedelta(days=4)).date().isoformat(), (t0 + dt.timedelta(days=4)).date().isoformat()), bbox=(L, B, R, Tp))
             if covered(r12, r["lon"], r["lat"])]
        fg = Grid([(r["lon"], r["lat"]) for r in F])
        groups = firms_groups(F, t0)
        dets = [dict(d, tier=tier(d)) for d in D["candidates"] if tier(d)]
        for d in dets:
            d["district"], d["state"], d["country"] = reg(d["lon"], d["lat"])
        mirror = {t: sum(tier(d) == t for d in D["mirror"]) for t in ("both", "b12")}
        for d in dets:
            dist, i = fg.nearest(d["lon"], d["lat"])
            sd, _ = static_g.nearest(d["lon"], d["lat"])
            d["firms_km"] = round(dist, 2) if i is not None else None
            d["firms_dt_h"] = round((F[i]["t"] - t0).total_seconds() / 3600, 1) if i is not None and dist <= MATCH_KM else None
            d["firms_sat"] = F[i]["sat"] if i is not None and dist <= MATCH_KM else None
            d["static_km"] = round(sd, 2) if sd < 1e8 else None
        out = {"date_utc": meta["utc"], "valid_km2": D["valid_km2"], "candidates": len(D["candidates"]),
               "mirror_candidates": len(D["mirror"]), "mirror_selected": mirror, "tiers": {}}
        for tname, members in (("both", [d for d in dets if d["tier"] == "both"]), ("b12", [d for d in dets if d["tier"] == "b12"]), ("all", dets)):
            g = Grid([(d["lon"], d["lat"]) for d in members])
            rec = {}
            for gname, rows in groups.items():
                hit = [g.nearest(r["lon"], r["lat"])[0] <= MATCH_KM for r in rows]
                ns = [h for h, r in zip(hit, rows) if r["type"] != "2"]
                rec[gname] = {"firms": len(rows), "matched": int(sum(hit)), "share": round(sum(hit) / max(len(rows), 1), 3),
                              "firms_excl_static": len(ns), "matched_excl_static": int(sum(ns))}
            # chance: the same detections displaced 5-30 km in a random direction, kept on valid pixels
            base = []
            for _ in range(20):
                k = 0
                for d in members:
                    for _try in range(20):
                        ang, rr = rng.uniform(0, 2 * math.pi), rng.uniform(5, 30)
                        lon = d["lon"] + rr * math.cos(ang) / (111.32 * math.cos(math.radians(d["lat"])))
                        lat = d["lat"] + rr * math.sin(ang) / 110.9
                        if L < lon < R and B < lat < Tp and covered(r12, lon, lat):
                            break
                    k += fg.nearest(lon, lat)[0] <= MATCH_KM
                base.append(k / max(len(members), 1))
            split = {}
            for rname, inreg in (("hills", lambda st: st in HILLS), ("plains", lambda st: st not in HILLS)):
                rows = [r for r in groups.get("viirs_night_after", []) if inreg(reg(r["lon"], r["lat"])[1])]
                hit = sum(g.nearest(r["lon"], r["lat"])[0] <= MATCH_KM for r in rows)
                split[rname] = {"firms": len(rows), "matched": int(hit), "static": sum(r["type"] == "2" for r in rows)}
            rec["viirs_night_after_by_region"] = split
            corr = sum((d["firms_km"] or 99) <= MATCH_KM for d in members)
            out["tiers"][tname] = {"detections": len(members), "recall_1km": rec,
                                   "with_firms_1km_pm4d": corr, "with_firms_share": round(corr / max(len(members), 1), 3),
                                   "chance_share": round(float(np.mean(base)), 3),
                                   "near_static_1km": sum((d["static_km"] or 99) <= MATCH_KM for d in members)}
        res[sc] = out
        sel[sc] = dets
        log(sc, json.dumps(out, indent=1))
    # persistence: the same spot hot on both nights (overlap strip 74.94-76.12 E) points to a kiln or industry
    g55 = Grid([(d["lon"], d["lat"]) for d in sel["R055"]])
    both = 0
    for d in sel["R012"]:
        if covered(cov["R055"], d["lon"], d["lat"]):
            dist, i = g55.nearest(d["lon"], d["lat"])
            if dist <= 0.3:
                d["both_nights"] = sel["R055"][i]["both_nights"] = True; both += 1
    res["overlap"] = {"R012_in_overlap": sum(covered(cov["R055"], d["lon"], d["lat"]) for d in sel["R012"]),
                      "R055_in_overlap": sum(covered(cov["R012"], d["lon"], d["lat"]) for d in sel["R055"]),
                      "same_place_both_nights_300m": both}
    worldcover(sel["R012"] + sel["R055"])
    feats = []
    for sc, dets in sel.items():
        for d in dets:
            d["class"] = classify(d)
            p = {k: v for k, v in d.items() if k not in ("lon", "lat")}
            p["scene"], p["date"] = sc, SCENES[sc]["utc"][:10]
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]}, "properties": p})
        res[sc]["classes"] = {t: {c: sum(d["class"] == c and d["tier"] == t for d in dets) for c in ("industrial", "built-up", "open")} for t in ("both", "b12")}
        res[sc]["both_by_state"] = {}
        for d in dets:
            if d["tier"] == "both":
                k = f"{d['state'] or 'outside'} ({(d['country'] or '')[:2]})" if d["country"] == "Pakistan" else (d["state"] or "outside")
                res[sc]["both_by_state"].setdefault(k, {"industrial": 0, "built-up": 0, "open": 0, "with_firms": 0})
                res[sc]["both_by_state"][k][d["class"]] += 1
                res[sc]["both_by_state"][k]["with_firms"] += (d["firms_km"] or 99) <= MATCH_KM
    # the web map gets the two-band hot spots only; both tiers go to data/raw for the release
    (RAW / "s2night_all.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    web = [dict(f, properties={k: f["properties"].get(k) for k in WEB_PROPS}) for f in feats if f["properties"]["tier"] == "both"]
    (OUT / "s2night.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": web}, separators=(",", ":")))
    (OUT / "s2night_validation.json").write_text(json.dumps(res, indent=1, default=str))
    log("overlap", res["overlap"])


WC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
WC_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/esa-worldcover"
WC_NAMES = {10: "tree cover", 20: "shrubland", 30: "grassland", 40: "cropland", 50: "built-up", 60: "bare", 70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}


def worldcover(dets):
    """ESA WorldCover 2021 (10 m) at each detection: class at the point and built-up share within ~150 m."""
    import urllib.request
    q = json.dumps({"collections": ["esa-worldcover"], "bbox": [72.4, 29.0, 78.7, 32.5], "limit": 100,
                    "query": {"esa_worldcover:product_version": {"eq": "2.0.0"}}}).encode()
    items = json.load(urllib.request.urlopen(urllib.request.Request(WC_URL, q, {"Content-Type": "application/json"}), timeout=120))["features"]
    tok = json.load(urllib.request.urlopen(WC_TOKEN, timeout=60))["token"]
    for it in items:
        L, B, R, T = it["bbox"]
        sub = [d for d in dets if L <= d["lon"] < R and B <= d["lat"] < T]
        if not sub:
            continue
        with rasterio.open(it["assets"]["map"]["href"] + "?" + tok) as ds:
            for d in sub:
                r, c = ds.index(d["lon"], d["lat"])
                a = ds.read(1, window=Window(c - 15, r - 15, 31, 31), boundless=True, fill_value=0)
                d["landcover"] = WC_NAMES.get(int(a[15, 15]), "unknown")
                d["built_share"] = round(float((a == 50).mean()), 2)


HILLS = {"Himachal Pradesh", "Uttarakhand", "Jammu and Kashmir", "Jammu & Kashmir"}


class Regions:
    """District / state / country lookup from the dashboard's own boundaries."""
    def __init__(self):
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        G = json.loads((ROOT / "data" / "geo" / "districts.geojson").read_text())["features"]
        self.props = [f["properties"] for f in G]
        self.geoms = [shape(f["geometry"]) for f in G]
        self.tree = STRtree(self.geoms)

    def __call__(self, lon, lat):
        from shapely.geometry import Point
        pt = Point(lon, lat)
        for i in self.tree.query(pt):
            if self.geoms[i].contains(pt):
                p = self.props[i]
                return p["name"], p["state"], p["country"]
        return None, None, None


def classify(d):
    """industrial: a FIRMS static source within 1 km (refinery, flare, kiln cluster) or seen on both nights;
    built-up: mostly built land around it (factories, furnaces, waste fires; not crop fires);
    open: cropland, grass, scrub or forest, where an open fire is the likely source."""
    if (d.get("static_km") or 99) <= MATCH_KM or d.get("both_nights"):
        return "industrial"
    if (d.get("built_share") or 0) >= BUILT_SHARE:
        return "built-up"
    return "open"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["fetch", "detect", "rasters", "validate", "all"])
    ap.add_argument("scenes", nargs="*", default=list(SCENES))
    a = ap.parse_args()
    if a.step in ("fetch", "all"):
        for s in a.scenes:
            fetch(s)
    if a.step in ("detect", "all"):
        detect(a.scenes)
    if a.step == "rasters":
        detect(a.scenes, rasters_only=True)
    if a.step in ("validate", "all"):
        validate()


if __name__ == "__main__":
    main()
