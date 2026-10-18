# Satellite active-fire detections (NASA FIRMS), aggregated

Counts and fire radiative power (FRP) of MODIS and VIIRS active-fire detections over north-west India and Pakistan Punjab (box 68.5-89 E, 21-35.5 N), aggregated by day, state, district, season and sensor. These are the numbers the dashboard plots. Point-level detections (all days of every year, 2003 onwards) are in the release assets `fire_detections_<year>.parquet` / `fire_detections_all.csv.gz`.

## Files

### `season_totals.csv`

3,622 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `year` | integer |  | calendar year of the season |
| `season` | string |  | rice = Sep 15-Nov 30, wheat = Apr 1-May 31 (UTC acquisition date) |
| `state` | string |  | state/province the detection falls in (district polygon lookup); 'Outside mapped districts' = inside the analysis box but outside every mapped district |
| `country` | string |  | India or Pakistan; empty for 'Outside mapped districts' |
| `sensor` | string |  | FIRMS sensor: MODIS Terra, MODIS Aqua, VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21 (Landsat is not present in the inputs) |
| `detections` | integer | count | number of active-fire detections (pixels), per sensor; sensors are not merged |
| `frp_mw_sum` | integer | MW | sum over the season of the daily (integer-rounded) FRP sums |

### `daily_by_state.parquet / daily_by_state.csv.gz`

248,093 rows. Whole calendar year, only rows with at least one detection.

| column | type | unit | meaning |
|---|---|---|---|
| `date` | timestamp | UTC date | acquisition date (UTC) of the overpass |
| `state` | string |  | state/province the detection falls in (district polygon lookup); 'Outside mapped districts' = inside the analysis box but outside every mapped district |
| `country` | string |  | India or Pakistan; empty for 'Outside mapped districts' |
| `sensor` | string |  | FIRMS sensor: MODIS Terra, MODIS Aqua, VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21 (Landsat is not present in the inputs) |
| `detections` | integer | count | number of active-fire detections (pixels), per sensor; sensors are not merged |
| `frp_mw_sum` | integer | MW | sum of fire radiative power of those detections; the dashboard rounds each day's sum to an integer |

### `district_season.csv`

52,512 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `year` | integer |  | calendar year |
| `season` | string |  | rice | wheat (see above) |
| `district_id` | integer |  | id in boundaries/districts.geojson |
| `district` | string |  | district name (geoBoundaries ADM2) |
| `state` | string |  | state/province the detection falls in (district polygon lookup); 'Outside mapped districts' = inside the analysis box but outside every mapped district |
| `country` | string |  | India or Pakistan; empty for 'Outside mapped districts' |
| `sensor` | string |  | FIRMS sensor: MODIS Terra, MODIS Aqua, VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21 (Landsat is not present in the inputs) |
| `detections` | integer | count | number of active-fire detections (pixels), per sensor; sensors are not merged |

### `hourly_ist_paddy.csv`

1,824 rows. All 24 hours per year x sensor, zeros included.

| column | type | unit | meaning |
|---|---|---|---|
| `year` | integer |  | calendar year |
| `sensor` | string |  | FIRMS sensor: MODIS Terra, MODIS Aqua, VIIRS S-NPP, VIIRS NOAA-20, VIIRS NOAA-21 (Landsat is not present in the inputs) |
| `hour_ist` | integer | hour 0-23 | hour of the overpass in Indian Standard Time (UTC+5:30), all detections in the rice season (all states in the box) |
| `detections` | integer | count | number of active-fire detections (pixels), per sensor; sensors are not merged |

### `district_daily_paddy.csv.gz`

319,753 rows. Rice season (Sep 15-Nov 30) only, nonzero district-days.

| column | type | unit | meaning |
|---|---|---|---|
| `year` | integer |  | season year |
| `sensor_group` | string |  | modis = Terra+Aqua; snpp = VIIRS S-NPP only; viirs = S-NPP+NOAA-20+NOAA-21 |
| `date` | string | UTC date | acquisition date |
| `district_id` | integer |  | id in boundaries/districts.geojson |
| `district` | string |  | district name |
| `state` | string |  | state/province the detection falls in (district polygon lookup); 'Outside mapped districts' = inside the analysis box but outside every mapped district |
| `country` | string |  | India or Pakistan; empty for 'Outside mapped districts' |
| `detections` | integer | count | number of active-fire detections (pixels), per sensor; sensors are not merged |

## Coverage

MODIS Terra/Aqua 2003 onwards, VIIRS S-NPP 2012 onwards, NOAA-20 2018 onwards, NOAA-21 2023 onwards; last detection date 2026-10-18 (summary generated 2026-10-18T12:04Z).

## Source and licence

NASA FIRMS (https://firms.modaps.eosdis.nasa.gov/): MODIS Collection 6.1 (MCD14) and VIIRS 375 m (VNP14IMG / VJ114IMG / VJ214IMG) active fire products. Yearly country archive files (standard processing) for completed years; the FIRMS area API (standard 'SP' then near-real-time 'NRT') for the current year and for NOAA-21. NASA data are freely usable without restriction; cite FIRMS (see ../../LICENSES.md). District/state attribution uses geoBoundaries polygons (ODbL / CC BY 2.5 IN / public domain).

## Known caveats

- Counts are detections (pixels), not fires or burned fields. MODIS pixels are ~1 km, VIIRS ~375 m, so VIIRS counts are several times MODIS counts for the same burning; never add sensors together and do not compare across sensors.
- Detections are attributed to districts by a 0.01-degree raster of the district polygons (centre of pixel); detections on a border can fall in the neighbouring district.
- Static land sources (FIRMS type 2: industry, gas flares, etc.) and offshore detections (type 3) were removed; near-real-time rows have no type, so 0.01-degree cells flagged static in any archive year are removed from them.
- Dates are UTC acquisition dates; seasons are defined on UTC dates (a 01:30 IST overpass on Oct 2 is dated Oct 1 UTC).
- Year-to-year comparisons are affected by overpass-time drift (Terra/Aqua orbits drifted after ~2020; S-NPP data gaps, e.g. no S-NPP after 2023-11-01 in some feeds) and by later sowing/harvest.
- CREAMS (ICAR-IARI) bulletin events, which the dashboard also shows as a 'sensor', are excluded here; see ../creams/.
- The FIRMS API returned empty files (no detections at all, i.e. a feed gap rather than no fires) for: VIIRS_SNPP_NRT 2026-07-11 .. 2026-07-15; VIIRS_SNPP_SP 2026-05-01 .. 2026-05-05; VIIRS_SNPP_SP 2026-05-06 .. 2026-05-10; VIIRS_SNPP_SP 2026-05-11 .. 2026-05-15; VIIRS_SNPP_SP 2026-05-16 .. 2026-05-20; VIIRS_SNPP_SP 2026-05-21 .. 2026-05-25; VIIRS_SNPP_SP 2026-05-26 .. 2026-05-30. Counts for those sensors and dates are missing (they are not zero-fire days).
- Current-year values come from near-real-time data and will change when standard-processing data replace them.

## How it was produced

`scripts/export_release.py` reads `data/fires/summary.json` and `data/fires/district_daily.json`, written by `scripts/build_fires.py` from the FIRMS CSVs.

## Loading

Python (pandas):

```python
import pandas as pd
st = pd.read_csv("season_totals.csv")
punjab = st.query("season == 'rice' and state == 'Punjab' and country == 'India' and sensor == 'MODIS Aqua'")
daily = pd.read_parquet("daily_by_state.parquet")
```

R:

```r
library(readr); library(arrow)
st <- read_csv("season_totals.csv")
daily <- read_parquet("daily_by_state.parquet")
```
