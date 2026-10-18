# Boundaries

District (ADM2) and state (ADM1) polygons for the analysis region (68.5-89 E, 21-35.5 N), and country outlines, as used by the dashboard. `boundaries.gpkg` holds the same three layers.

## Files

### `districts.geojson (layer `districts` in boundaries.gpkg)`

529 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `id` | integer |  | district id used by every district-level table in this release |
| `name` | string |  | name (diacritics removed) |
| `state` | string |  | state/province |
| `country` | string |  | India or Pakistan |
| `area_km2` | float | km2 | polygon area |
| `geometry` | string | WGS84 | polygon geometry |

### `states.geojson (layer `states` in boundaries.gpkg)`

26 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `name` | string |  | name (diacritics removed) |
| `country` | string |  | India or Pakistan |
| `geometry` | string | WGS84 | polygon geometry |

### `subcontinent.geojson (layer `subcontinent` in boundaries.gpkg)`

64 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `name` | string |  | name (diacritics removed) |
| `subcontinent` | boolean |  | true for India, Pakistan, Nepal, Bangladesh, Bhutan, Sri Lanka |
| `geometry` | string | WGS84 | polygon geometry |

## Coverage

India and Pakistan districts/states intersecting the region; countries clipped to 40-125 E, 12 S-58 N, simplified (0.01 deg).

## Source and licence

- India districts: geoBoundaries gbOpen IND ADM2 (source: Pathways Data / lgdirectory.gov.in, 2021) - **Open Database License (ODbL) 1.0**. `districts.geojson` and the districts layer are a derivative database and are released under ODbL 1.0 (share-alike, attribution).
- India states: geoBoundaries gbOpen IND ADM1 (DataMeet India / Election Commission of India) - CC BY 2.5 IN.
- Pakistan ADM1/ADM2: geoBoundaries gbOpen PAK - public domain.
- Countries: Natural Earth 1:10m admin-0 countries, India point of view - public domain.
Cite: Runfola et al. (2020) geoBoundaries, PLoS ONE 15(4): e0231866. Licences checked against the geoBoundaries API on 2026-10-18.

## Known caveats

- Boundaries are for analysis, not authoritative; Natural Earth's India point-of-view outlines are used for country borders and disputed areas.
- Districts follow the 2021 LGD set: districts created later (e.g. Malerkotla, Punjab) are inside their parent polygon.
- Coordinates rounded to 4 decimals (districts, states) / 3 decimals (countries).

## How it was produced

`scripts/build_geo.py` builds `data/geo/*.geojson`; `scripts/export_release.py` copies them and writes the GeoPackage.

## Loading

Python (pandas):

```python
import geopandas as gpd
d = gpd.read_file("boundaries.gpkg", layer="districts")
```

R:

```r
library(sf)
d <- st_read("boundaries.gpkg", layer = "districts")
```
