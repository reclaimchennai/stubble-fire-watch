#!/usr/bin/env python3
"""Fetch smoke aerosol optical depth + 10 m winds from NASA GEOS-FP (no key needed)
and pack them into PNG frame atlases the dashboard animates.

Source: GMAO GEOS-FP "seamless" collections on the NCCS OPeNDAP server
  tavg3_2d_aer_Nx  3-hourly  OC + BC + brown-carbon extinction AOD at 550 nm
  tavg1_2d_slv_Nx  hourly    U10M / V10M
Grid is 0.3125 deg lon x 0.25 deg lat. Each seamless run = assimilation + ~1 day forecast.

Outputs (data/smoke/):
  smoke_atlas.png   grayscale, one tile per 3 h frame, value = sqrt(AOD / AOD_MAX) * 255
  wind_atlas.png    R = u, G = v, scaled from [-WIND_MAX, WIND_MAX] m/s
  smoke_meta.json   frame times, grid, tile layout, scaling, and which frames are forecast
"""
import argparse
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "smoke"
CACHE = ROOT / "data" / "raw" / "smoke"
BASE = "https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/seamless"

# Region: northern subcontinent and its upwind margin
LON0, LON1, LAT0, LAT1 = 64.0, 94.0, 18.0, 38.0
DLON, DLAT = 0.3125, 0.25
AOD_MAX = 2.0
WIND_MAX = 20.0
TILE_COLS = 16


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fire-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))


def recent_runs(coll, n=4):
    html = get(f"{BASE}/{coll}/")
    return sorted(set(re.findall(rf"{coll}\.(\d{{8}}_\d{{2}})", html)))[-n:]


def pick_run(coll):
    """Of the last few runs, the one whose forecast reaches furthest (00Z runs carry ~10 days)."""
    best = None
    for r in recent_runs(coll):
        t0, t1 = time_axis(coll, r)
        if best is None or t1 >= best[2]:
            best = (r, t0, t1)
    return best


def fill_gaps(coll, var, arr, run, ti0, yi, xi):
    """Frames a run left empty (all zero) are taken from neighbouring runs of the same seamless series."""
    bad = [i for i in range(len(arr)) if float(arr[i].max()) < 1e-3]
    for alt in reversed(recent_runs(coll)):
        if not bad:
            break
        if alt == run:
            continue
        a, b = ti0 + bad[0], ti0 + bad[-1]
        try:
            other = fetch_var(coll, alt, var, a, b, 1, yi, xi)
        except Exception as e:
            print(f"  gap fill {var} from {alt} failed: {e}", file=sys.stderr)
            continue
        for i in list(bad):
            j = ti0 + i - a
            if j < len(other) and float(other[j].max()) >= 1e-3:
                arr[i] = other[j]
                bad.remove(i)
        print(f"  {var}: filled gaps from {alt}, {len(bad)} left", file=sys.stderr)
    return bad


def time_axis(coll, run):
    """Parse 'HH:MMZDDMONYYYY to ...' from the .info page -> (start datetime, n steps)."""
    info = re.sub(r"<[^>]+>", "", get(f"{BASE}/{coll}/{coll}.{run}.info"))
    m = re.search(r"(\d\d):(\d\d)Z(\d\d)([A-Z]{3})(\d{4}) to (\d\d):(\d\d)Z(\d\d)([A-Z]{3})(\d{4})", info)
    def p(h, mi, d, mon, y):
        return dt.datetime.strptime(f"{y}{mon}{d}{h}{mi}", "%Y%b%d%H%M")
    return p(*m.groups()[:5]), p(*m.groups()[5:])


def parse_ascii(text, nt, ny, nx):
    """OPeNDAP .ascii grid -> ndarray (nt, ny, nx)."""
    vals = []
    for line in text.splitlines():
        if re.match(r"^\[\d+\]\[\d+\]", line):
            vals.extend(float(v) for v in line.split(",")[1:] if v.strip())
    a = np.array(vals, dtype=np.float32)
    # the server can end a variable a few frames before the advertised time axis: accept whole frames
    assert a.size and a.size % (ny * nx) == 0 and a.size <= nt * ny * nx, (a.size, nt, ny, nx)
    return a.reshape(-1, ny, nx)


def fetch_var(coll, run, var, ti0, ti1, step, yi, xi):
    chunk = 16 * step

    def one(a):
        b = min(ti1, a + chunk - step)
        url = f"{BASE}/{coll}/{coll}.{run}.ascii?{var}[{a}:{step}:{b}][{yi[0]}:{yi[1]}][{xi[0]}:{xi[1]}]"
        cache = CACHE / f"{run}_{var}_{a}_{b}_{step}_{yi[0]}_{xi[0]}.npy"
        want = (b - a) // step + 1
        last = b + step > ti1            # only the final chunk may legitimately come back short
        if cache.exists():
            arr = np.load(cache)
            if len(arr) == want or last:
                return arr
            cache.unlink()               # a short middle chunk from an older version: re-fetch it
        arr = None
        for i in range(5):  # responses are sometimes truncated mid-stream
            try:
                got = parse_ascii(get(url), want, yi[1] - yi[0] + 1, xi[1] - xi[0] + 1)
                if len(got) < want and not last:
                    raise ValueError(f"short chunk: {len(got)} of {want} frames")
                arr = got
                break
            except (AssertionError, ValueError) as e:
                print(f"  bad response {var} {a}-{b} ({e}), retrying", file=sys.stderr)
                time.sleep(5 * (i + 1))
        if arr is None:
            if last:
                raise RuntimeError(f"{var} {a}-{b} failed")
            # keep the time axis aligned: missing frames become zeros, which fill_gaps then takes from other runs
            print(f"  {var} {a}-{b}: padding {want} missing frames", file=sys.stderr)
            return np.zeros((want, yi[1] - yi[0] + 1, xi[1] - xi[0] + 1), np.float32)
        np.save(cache, arr)
        print(f"  {var} {a}-{b}", file=sys.stderr)
        return arr

    with ThreadPoolExecutor(8) as ex:
        return np.concatenate(list(ex.map(one, range(ti0, ti1 + 1, chunk))))


def atlas(frames, encode):
    n, h, w = frames.shape[:3]
    rows = math.ceil(n / TILE_COLS)
    chans = 1 if frames.ndim == 3 else frames.shape[3]
    img = np.zeros((rows * h, TILE_COLS * w, chans), np.uint8)
    for i in range(n):
        r, c = divmod(i, TILE_COLS)
        # flip so north is up in the image
        img[r * h:(r + 1) * h, c * w:(c + 1) * w] = encode(frames[i])[::-1].reshape(h, w, chans)
    return img, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-15", help="first day (UTC)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    xi = (round((LON0 + 180) / DLON), round((LON1 + 180) / DLON))
    yi = (round((LAT0 + 90) / DLAT), round((LAT1 + 90) / DLAT))

    aer_run, a_t0, a_t1 = pick_run("tavg3_2d_aer_Nx")
    run_time = dt.datetime.strptime(aer_run, "%Y%m%d_%H")
    start = max(dt.datetime.fromisoformat(args.start), a_t0)
    ti0 = math.ceil((start - a_t0).total_seconds() / 10800)
    ti1 = int((a_t1 - a_t0).total_seconds() // 10800)
    times = [a_t0 + dt.timedelta(hours=3 * i) for i in range(ti0, ti1 + 1)]
    print(f"aer run {aer_run}: {times[0]} .. {times[-1]} ({len(times)} frames)", file=sys.stderr)

    names = ("ocexttau", "bcexttau", "brexttau", "totexttau")
    parts = [fetch_var("tavg3_2d_aer_Nx", aer_run, v, ti0, ti1, 1, yi, xi) for v in names]
    k = min(len(p) for p in parts)
    parts = [p[:k] for p in parts]
    for arr in parts:  # GEOS fill value is 1e15: blank such frames so they count as missing
        arr[np.max(arr, axis=(1, 2)) > 100] = 0
    missing = set()
    for name, arr in zip(names, parts):
        missing |= set(fill_gaps("tavg3_2d_aer_Nx", name, arr, aer_run, ti0, yi, xi))
    # interior frames no run could supply: linear interpolation in time, flagged in meta
    filled = []
    for i in sorted(missing):
        lo = max((j for j in range(i) if j not in missing), default=None)
        hi = min((j for j in range(i + 1, k) if j not in missing), default=None)
        if lo is None or hi is None:
            continue
        w = (i - lo) / (hi - lo)
        for arr in parts:
            arr[i] = arr[lo] * (1 - w) + arr[hi] * w
        filled.append(i)
    oc, bc, br, total = parts
    smoke = oc + bc + br

    slv_run = aer_run if aer_run in recent_runs("tavg1_2d_slv_Nx", 8) else recent_runs("tavg1_2d_slv_Nx")[-1]
    s_t0, s_t1 = time_axis("tavg1_2d_slv_Nx", slv_run)
    # aerosol frames are 3 h means centred on HH:30; take the hourly wind at the same centre
    w0 = int(round((times[0] - s_t0).total_seconds() / 3600))
    w1 = min(w0 + 3 * (len(times) - 1), int((s_t1 - s_t0).total_seconds() // 3600))
    u = fetch_var("tavg1_2d_slv_Nx", slv_run, "u10m", w0, w1, 3, yi, xi)
    v = fetch_var("tavg1_2d_slv_Nx", slv_run, "v10m", w0, w1, 3, yi, xi)
    for arr in (u, v):
        arr[np.abs(arr) > 1e3] = 0
    n = min(len(smoke), len(total))  # trim to frames every aerosol variable has
    # drop trailing frames still missing after gap filling
    while n > 1 and (n - 1) in missing and (n - 1) not in filled:
        n -= 1
    smoke, total, times = smoke[:n], total[:n], times[:n]
    u, v = u[:n], v[:n]
    if len(u) < len(times):  # pad the tail with the last available wind
        pad = len(times) - len(u)
        u = np.concatenate([u, np.repeat(u[-1:], pad, 0)])
        v = np.concatenate([v, np.repeat(v[-1:], pad, 0)])

    enc_aod = lambda f: (np.sqrt(np.clip(f, 0, AOD_MAX) / AOD_MAX) * 255).round().astype(np.uint8)
    enc_w = lambda f: ((np.clip(f, -WIND_MAX, WIND_MAX) + WIND_MAX) / (2 * WIND_MAX) * 255).round().astype(np.uint8)

    s_img, rows = atlas(smoke, enc_aod)
    Image.fromarray(s_img[:, :, 0], "L").save(OUT / "smoke_atlas.png", optimize=True)
    t_img, _ = atlas(total, enc_aod)
    Image.fromarray(t_img[:, :, 0], "L").save(OUT / "total_aod_atlas.png", optimize=True)
    uv = np.stack([u, v, np.zeros_like(u)], -1)
    w_img, _ = atlas(uv, lambda f: np.stack([enc_w(f[..., 0]), enc_w(f[..., 1]), np.zeros(f.shape[:2], np.uint8)], -1))
    Image.fromarray(w_img, "RGB").save(OUT / "wind_atlas.png", optimize=True)

    h, w = smoke.shape[1:]
    # per-frame regional stats (box over Punjab-Haryana-Delhi) for the time chart
    lat_c = np.linspace(yi[0] * DLAT - 90, yi[1] * DLAT - 90, h)
    lon_c = np.linspace(xi[0] * DLON - 180, xi[1] * DLON - 180, w)
    box = np.ix_((lat_c >= 27.5) & (lat_c <= 32.5), (lon_c >= 73.5) & (lon_c <= 78.5))
    meta = {
        "source": "NASA GMAO GEOS-FP tavg3_2d_aer_Nx + tavg1_2d_slv_Nx (seamless)",
        "aer_run": aer_run, "wind_run": slv_run,
        "analysis_until": run_time.strftime("%Y-%m-%dT%H:%MZ"),
        "bounds": [xi[0] * DLON - 180 - DLON / 2, yi[0] * DLAT - 90 - DLAT / 2,
                   xi[1] * DLON - 180 + DLON / 2, yi[1] * DLAT - 90 + DLAT / 2],
        "tile_w": w, "tile_h": h, "cols": TILE_COLS, "rows": rows,
        "aod_max": AOD_MAX, "aod_encoding": "aod = (px/255)^2 * aod_max",
        "wind_max": WIND_MAX, "wind_encoding": "m/s = px/255 * 2*wind_max - wind_max",
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in times],
        "forecast": [t > run_time for t in times],
        "interpolated_frames": [i for i in filled if i < n],
        "box_name": "Punjab-Haryana-Delhi box (73.5-78.5E, 27.5-32.5N)",
        "box_smoke_mean": [round(float(f[box].mean()), 4) for f in smoke],
        "box_total_mean": [round(float(f[box].mean()), 4) for f in total],
    }
    (OUT / "smoke_meta.json").write_text(json.dumps(meta))
    # keep the chunk cache to the runs that can still be used for gap filling
    keep = set(recent_runs("tavg3_2d_aer_Nx")) | set(recent_runs("tavg1_2d_slv_Nx"))
    for f in CACHE.glob("*.npy"):
        if f.name[:11] not in keep:
            f.unlink()
    print(f"wrote {len(times)} frames {w}x{h} to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
