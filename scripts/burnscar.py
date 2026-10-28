#!/usr/bin/env python3
"""Burn scars: NASA MODIS burned area (MCD64A1 collection 6.1) over Punjab and Haryana, every paddy season.

Source: MCD64A1 on Microsoft Planetary Computer (keyless STAC, SAS-signed COGs). 500 m sinusoidal grid,
monthly files; the Burn_Date band gives the day of year a pixel burned (0 unburned, -1 unmapped, -2 water).
This is NASA's validated global burned-area product. It is coarse for Punjab's 1-2 ha fields and misses many
small, short agricultural burns, so read its areas as a lower bound and its pattern as the signal.

A Sentinel-2 field-scale classifier was tried first (2026-10-17) and rejected: harvested paddy that is dark
with a negative NBR is common whether or not it burned, and the rule rated 2025 (a near-burn-free year) far
above 2021 on the same tile. See ARCHITECTURE.md. Do not bring back a spectral rule without labelled validation.

Per season (Sep 15 .. Dec 5): burn date per pixel -> mosaic in UTM 43N -> district / state burned area,
burned area by date, FIRMS cross-check, XYZ tiles coloured by burn date.

Outputs
  data/raw/burnscar/<season>/mosaic.tif   EPSG:32643, 463 m; 0 none, 10+d burned on season day d (Sep 15 = 0)
  data/burnscar/<season>/{z}/{x}/{y}.png  tiles z5-11
  data/burnscar/burnscar.json             per season: states, districts, daily burned area, FIRMS cross-check
Usage: burnscar.py [--season 2021 2022 ...]   (default: 2003 .. this year; months without data are skipped)
"""
import argparse
import csv
import datetime as dt
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom, transform as warp_xy
from shapely.geometry import shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "burnscar"
OUT = ROOT / "data" / "burnscar"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/modiseuwest/modis-061-cogs"
CRS = "EPSG:32643"           # UTM 43N covers 72-78 E: all of Punjab and Haryana
RES = 463.3127               # MCD64A1 nominal pixel, metres
STATES = {"Punjab", "Haryana", "Chandigarh", "Delhi"}
SEASON_END = (12, 5)


def get_json(url, body=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def season_mosaic(year, bbox, sdir):
    """Warp each month's Burn_Date tiles to UTM 43N; keep the first burn inside the season window."""
    feats = get_json(f"{STAC}/search", {"collections": ["modis-64A1-061"], "bbox": bbox,
                                        "datetime": f"{year}-09-01T00:00:00Z/{year}-12-31T23:59:59Z", "limit": 100})["features"]
    if not feats:
        return None, []
    tok = get_json(TOKEN_URL)["token"]
    xs, ys = warp_xy("EPSG:4326", CRS, [bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    te = [str(v) for v in (min(xs), min(ys), max(xs), max(ys))]
    sdir.mkdir(parents=True, exist_ok=True)
    months = sorted({f["properties"]["start_datetime"][:7] for f in feats})
    s0 = np.datetime64(f"{year}-09-15")
    last_day = int((np.datetime64(dt.date(year, *SEASON_END).isoformat()) - s0).astype(int))
    code, tr = None, None
    for m in months:
        srcs = [f"/vsicurl/{f['assets']['Burn_Date']['href']}?{tok}" for f in feats if f["properties"]["start_datetime"][:7] == m]
        tif = sdir / f"bd_{m}.tif"
        subprocess.run(["gdalwarp", "-q", "-overwrite", "-t_srs", CRS, "-tr", str(RES), str(RES), "-te", *te, "-r", "near",
                        "-dstnodata", "-32768", *srcs, str(tif)], check=True)
        with rasterio.open(tif) as src:
            doy = src.read(1).astype(np.int64)
            tr = src.transform
        tif.unlink()
        if code is None:
            code = np.zeros(doy.shape, np.uint8)
        burned = doy > 0
        day = ((np.datetime64(f"{year}-01-01") + (doy - 1)) - s0).astype(int)
        ok = burned & (day >= 0) & (day <= last_day) & (code == 0)
        code[ok] = (10 + day[ok]).astype(np.uint8)
    mosaic = sdir / "mosaic.tif"
    with rasterio.open(mosaic, "w", driver="GTiff", width=code.shape[1], height=code.shape[0], count=1, dtype="uint8",
                       crs=CRS, transform=tr, nodata=0, compress="deflate") as dst:
        dst.write(code, 1)
    return mosaic, months


def colour_table():
    """Burn date -> colour: mid-Sep pale yellow, early Nov orange-red, Dec deep purple (same ramp as the map legend)."""
    stops = [(0, (255, 236, 150)), (25, (255, 183, 77)), (45, (244, 104, 45)), (60, (214, 45, 72)), (81, (122, 22, 110))]
    ct = {v: (0, 0, 0, 0) for v in range(10)}
    for v in range(10, 256):
        d = v - 10
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if d <= b or b == stops[-1][0]:
                t = max(0.0, min(1.0, (d - a) / (b - a)))
                ct[v] = tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3)) + (230,)
                break
    return ct


def make_tiles(year, mosaic, sdir):
    col = sdir / "colour.tif"
    with rasterio.open(mosaic) as src:
        prof, data = src.profile, src.read(1)
    with rasterio.open(col, "w", **prof) as dst:
        dst.write(data, 1)
        dst.write_colormap(1, colour_table())
    merc = sdir / "colour_3857.tif"
    subprocess.run(["gdalwarp", "-q", "-overwrite", "-t_srs", "EPSG:3857", "-r", "near", "-srcnodata", "0", "-dstnodata", "0",
                    str(col), str(merc)], check=True)
    rgba = sdir / "rgba_3857.vrt"
    subprocess.run(["gdal_translate", "-q", "-of", "VRT", "-expand", "rgba", str(merc), str(rgba)], check=True)
    tmp, tiles = OUT / f".{year}.tmp", OUT / str(year)
    subprocess.run(["rm", "-rf", str(tmp)], check=True)
    g2t = "/usr/bin/gdal2tiles.py" if Path("/usr/bin/gdal2tiles.py").exists() else "gdal2tiles.py"
    subprocess.run([g2t, "-q", "--xyz", "-z", "5-11", "-r", "near", "-w", "none", "--processes", "4", str(rgba), str(tmp)], check=True)
    subprocess.run(["rm", "-rf", str(tiles)], check=True)
    tmp.rename(tiles)


def firms_check(year, code, mtr, region):
    """Share of season detections (Punjab + Haryana + Delhi) that fall on an MCD64A1 burned pixel."""
    out = {}
    for name, fn in (("viirs_snpp", f"viirs-snpp_{year}_India.csv"), ("modis", f"modis_{year}_India.csv")):
        p = ROOT / "data" / "raw" / "firms_country" / fn
        if not p.exists():
            continue
        pts = [(float(r["longitude"]), float(r["latitude"])) for r in csv.DictReader(open(p))
               if f"{year}-09-15" <= r["acq_date"] <= f"{year}-12-05" and r.get("type", "0") == "0"]
        if not pts:
            continue
        xs, ys = warp_xy("EPSG:4326", CRS, [q[0] for q in pts], [q[1] for q in pts])
        inv = ~mtr
        H, W = code.shape
        n = hit = 0
        for x, y in zip(xs, ys):
            c, r = inv * (x, y)
            r, c = int(r), int(c)
            if 0 <= r < H and 0 <= c < W and region[r, c]:
                n += 1
                hit += int(code[r, c] >= 10)
        out[name] = {"detections": n, "on_burned_pixel": round(hit / max(1, n), 3)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, nargs="*")
    ap.add_argument("--no-tiles", action="store_true")
    a = ap.parse_args()
    geo = json.loads((ROOT / "data" / "geo" / "districts.geojson").read_text())
    dists = [f for f in geo["features"] if f["properties"]["country"] == "India" and f["properties"]["state"] in STATES]
    region_geom = unary_union([shape(f["geometry"]) for f in dists])
    bbox = [round(v, 2) for v in region_geom.bounds]
    seasons = a.season or list(range(2003, dt.date.today().year + 1))
    stats_path = OUT / "burnscar.json"
    OUT.mkdir(parents=True, exist_ok=True)
    allstats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    for year in seasons:
        sdir = RAW / str(year)
        mosaic, months = season_mosaic(year, bbox, sdir)
        if mosaic is None:
            print(f"{year}: no MCD64A1 months yet", flush=True)
            continue
        with rasterio.open(mosaic) as src:
            code, mtr, shp = src.read(1), src.transform, src.shape
        shapes = [(transform_geom("EPSG:4326", CRS, f["geometry"]), f["properties"]["id"] + 1) for f in dists]
        did = rasterize(shapes, out_shape=shp, transform=mtr, fill=0, dtype="uint16")
        region = did > 0
        px_ha = RES * RES / 1e4
        burned = region & (code >= 10)
        burned_d = np.bincount(did[burned], minlength=len(geo["features"]) + 1) * px_ha
        st_of = {f["properties"]["id"] + 1: f["properties"]["state"] for f in dists}
        days = (dt.date(year, *SEASON_END) - dt.date(year, 9, 15)).days + 1
        daily, states = {}, {}
        for st in ("Punjab", "Haryana", "Delhi", "Chandigarh"):
            ids = np.array([k for k, v in st_of.items() if v == st])
            m = burned & np.isin(did, ids)
            daily[st] = (np.bincount(code[m].astype(int) - 10, minlength=days)[:days] * px_ha).round(0).tolist()
            states[st] = {"burned_ha": round(float(m.sum() * px_ha))}
        need = [f"{year}-{m:02d}" for m in (9, 10, 11)]
        missing = [m for m in need if m not in months]
        complete = not missing and months[-1] >= f"{year}-11"
        through = dt.date(year, *SEASON_END) if complete else \
            (dt.date.fromisoformat(months[-1] + "-01") + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        allstats[str(year)] = {
            "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "product": "MODIS MCD64A1 C6.1 burned area, 500 m", "months": months, "missing_months": missing, "complete": complete,
            "through": min(through, dt.date(year, *SEASON_END)).isoformat(),
            "states": states, "daily_burned_ha": daily, "pixel_ha": round(px_ha, 2),
            "districts": {str(f["properties"]["id"]): round(float(burned_d[f["properties"]["id"] + 1])) for f in dists},
            "firms_check": firms_check(year, code, mtr, region),
        }
        stats_path.write_text(json.dumps(allstats, separators=(",", ":")))
        print(f"{year} {months[0]}..{months[-1]}: Punjab {states['Punjab']['burned_ha']:,} ha, Haryana {states['Haryana']['burned_ha']:,} ha,"
              f" FIRMS on burned pixels {allstats[str(year)]['firms_check']}", flush=True)
        if not a.no_tiles:
            make_tiles(year, mosaic, sdir)


if __name__ == "__main__":
    main()
