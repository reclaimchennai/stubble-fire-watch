#!/usr/bin/env python3
"""Build the dashboard's boundary layers from open data.

Inputs (data/raw/boundaries/):
  ne_ind/ne_10m_admin_0_countries_ind.shp  Natural Earth 10m admin-0, India point of view
  IND-ADM1/ADM2.geojson, PAK-ADM1/ADM2.geojson  geoBoundaries gbOpen (ODbL / CC-BY)
Outputs (data/geo/):
  countries.geojson   subcontinent countries, outline for the spotlight mask
  states.geojson      ADM1 within the region
  districts.geojson   ADM2 within the region, each with id, name, state, country
"""
import json
import unicodedata
import struct
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "boundaries"
OUT = ROOT / "data" / "geo"
REGION = box(68.5, 21, 89, 35.5)          # analysis area (same as FIRMS bbox)
VIEW = box(40, -12, 125, 58)              # outline/mask area (large enough for tilted views)
SUBCONTINENT = {"India", "Pakistan", "Nepal", "Bangladesh", "Bhutan", "Sri Lanka"}
STATE_FIX = {"Dadra and Nagar Haveli and Daman and Diu": "Dadra & Nagar Haveli and Daman & Diu",
             "National Capital Territory of Delhi": "Delhi", "NCT of Delhi": "Delhi",
             "Punjab": "Punjab", "Islamabad Capital Territory": "Islamabad"}


def read_shp_ne(path):
    """Minimal Natural Earth reader via pyshp-free route: use ogr2ogr output instead."""
    import subprocess
    gj = OUT / "_ne.geojson"
    subprocess.run(["ogr2ogr", "-f", "GeoJSON", str(gj), str(path), "-select", "NAME,ADM0_A3"], check=True)
    d = json.loads(gj.read_text())
    gj.unlink()
    return d["features"]


def clean(name):
    """geoBoundaries names carry diacritics (Haryāna); strip them for display and matching."""
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).strip()


def rnd(geom, nd=4):
    def r(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [r(x) for x in c]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def fc(feats):
    return {"type": "FeatureCollection", "features": feats}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # countries
    feats = []
    for f in read_shp_ne(RAW / "ne_ind" / "ne_10m_admin_0_countries_ind.shp"):
        g = shape(f["geometry"])
        if not g.intersects(VIEW):
            continue
        name = f["properties"]["NAME"]
        g = g.intersection(VIEW).simplify(0.01, preserve_topology=True)
        feats.append({"type": "Feature", "properties": {"name": name, "sub": name in SUBCONTINENT}, "geometry": rnd(g, 3)})
    (OUT / "countries.geojson").write_text(json.dumps(fc(feats), separators=(",", ":")))

    # states
    states = []
    for c, cname in (("IND", "India"), ("PAK", "Pakistan")):
        for f in json.loads((RAW / f"{c}-ADM1.geojson").read_text())["features"]:
            g = shape(f["geometry"]).buffer(0)
            if not g.intersects(REGION):
                continue
            n = clean(f["properties"]["shapeName"])
            n = STATE_FIX.get(n, n)
            states.append((n, cname, g))
    (OUT / "states.geojson").write_text(json.dumps(fc([
        {"type": "Feature", "properties": {"name": n, "country": c},
         "geometry": rnd(g.simplify(0.005, preserve_topology=True))} for n, c, g in states]), separators=(",", ":")))

    # districts, state assigned by largest overlap
    dists = []
    for c, cname in (("IND", "India"), ("PAK", "Pakistan")):
        for f in json.loads((RAW / f"{c}-ADM2.geojson").read_text())["features"]:
            g = shape(f["geometry"]).buffer(0)
            if not g.intersects(REGION):
                continue
            cands = [(g.intersection(sg).area, sn) for sn, sc, sg in states if sc == cname and sg.intersects(g)]
            if not cands:
                continue
            st = max(cands)[1]
            dists.append({"name": clean(f["properties"]["shapeName"]), "state": st, "country": cname, "g": g})
    dists.sort(key=lambda d: (d["country"], d["state"], d["name"]))
    out = []
    for i, d in enumerate(dists):
        out.append({"type": "Feature", "id": i,
                    "properties": {"id": i, "name": d["name"], "state": d["state"], "country": d["country"],
                                   "area_km2": round(d["g"].area * 111.32 ** 2 * 0.87, 1)},
                    "geometry": rnd(d["g"].simplify(0.003, preserve_topology=True))})
    (OUT / "districts.geojson").write_text(json.dumps(fc(out), separators=(",", ":")))
    print(f"countries {len(feats)}, states {len(states)}, districts {len(out)}")


if __name__ == "__main__":
    main()
