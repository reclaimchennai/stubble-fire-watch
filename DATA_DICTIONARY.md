# Data dictionary

Open datasets behind the paddy-stubble-burning dashboard (north-west India and Pakistan Punjab). Every folder under `datasets/` has a README with each column's type, unit and meaning, coverage, source, licence, caveats and loading snippets. Large files are attached to the GitHub Release (see *Release assets* below). Licences: [LICENSES.md](LICENSES.md).

Exported by `scripts/export_release.py` on 2026-10-29.

## Repository datasets

| folder | contents | files |
|---|---|---|
| [fires/](datasets/fires/README.md) | NASA FIRMS active-fire detections aggregated by day / state / district / season / sensor, IST hour of burning | `daily_by_state.csv.gz` (2.4 MB), `daily_by_state.parquet` (1.3 MB), `district_daily_paddy.csv.gz` (1.9 MB), `district_season.csv` (4.8 MB), `hourly_ist_paddy.csv` (41 kB), `season_totals.csv` (0.3 MB) |
| [creams/](datasets/creams/README.md) | ICAR-IARI CREAMS official daily residue-burning counts (state, district), year comparisons, bulletin list | `bulletin_state_tables_long.csv.gz` (2.3 MB), `bulletins.csv` (0.2 MB), `district_daily.csv` (35.3 MB), `monthly_reports.csv` (0.2 MB), `state_daily.csv` (1.0 MB), `validation.json` (74 kB), `year_compare.csv` (0.7 MB) |
| [burned_area/](datasets/burned_area/README.md) | NASA MODIS MCD64A1 burned area per season, state, district and burn date (Punjab, Haryana) | `daily_by_state.csv` (0.3 MB), `district_season.csv` (44 kB), `firms_check.csv` (1 kB), `state_season.csv` (10 kB) |
| [s2_night/](datasets/s2_night/README.md) | Sentinel-2A night hot spots (SWIR, 20 m), 9 and 12 Dec 2025, with FIRMS match rates and land-cover setting | `hot_spots.csv` (29 kB), `hot_spots.geojson` (98 kB), `validation.csv` (3 kB) |
| [air/](datasets/air/README.md) | CPCB ground PM2.5/PM10 (via OpenAQ), CAMS PM/AOD + ERA5 meteorology at receptor cities, modelled stubble share of Delhi PM2.5 | `cams_era5_cities.csv` (0 kB), `cams_era5_daily.csv` (1.3 MB), `cpcb_pm25_daily.csv` (1.3 MB), `cpcb_pm25_monthly.csv` (93 kB), `cpcb_stations.csv` (17 kB), `stubble_share_delhi.csv` (0.2 MB) |
| [paddy/](datasets/paddy/README.md) | Kharif rice area and production by state (1966+) and district (1997+), UPAg/DES | `rice_area_district.csv` (0.5 MB), `rice_area_state.csv` (38 kB) |
| [enforcement/](datasets/enforcement/README.md) | Enforcement and crop-residue-management indicators with source documents and policy timeline | `ENFORCEMENT_SOURCES.md` (54 kB), `events.csv` (14 kB), `metrics.csv` (4 kB), `sources.csv` (40 kB), `values.csv` (0.4 MB) |
| [boundaries/](datasets/boundaries/README.md) | District / state / country polygons used for all attributions (GeoJSON + GeoPackage) | `boundaries.gpkg` (4.8 MB), `districts.geojson` (3.1 MB), `states.geojson` (0.6 MB), `subcontinent.geojson` (0.5 MB) |
| [south/](datasets/south/README.md) | North to south: Chennai and east-coast air quality, 5-day back-trajectory fire exposure, Bay of Bengal / Arabian Sea cyclone tracks | `chennai_hourly.csv` (1.0 MB), `chennai_reference_stations.csv` (1 kB), `cities_daily.csv` (2.3 MB), `cyclone_track_points.csv` (1.1 MB), `cyclone_tracks.geojson` (0.3 MB), `cyclones.csv` (37 kB), `trajectory_exposure_daily.csv` (0.4 MB), `trajectory_paths_chennai.csv` (0.4 MB) |

## Release assets

Download from the GitHub Release; checksums in `SHA256SUMS`, list in `MANIFEST.csv`.

| file | size | rows / size | description |
|---|---|---|---|
| `burned_area_2003.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2004.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2005.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2006.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2007.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2008.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2009.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2010.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2011.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2012.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2013.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2014.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2015.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2016.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2017.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2018.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2019.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2020.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2021.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2022.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2023.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2024.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `burned_area_2025.tif` | 0.1 MB | 790 x 1170 px | MODIS MCD64A1 burn-date codes (COG, EPSG:32643): 0 none, 10+d burned on season day d (Sep 15 = 0) |
| `creams_events.csv.gz` | 8.9 MB | 700112 rows | ICAR-IARI CREAMS bulletin annexure events (CSV) |
| `creams_events.parquet` | 18.2 MB | 700112 rows | ICAR-IARI CREAMS bulletin annexure events (GeoParquet, EPSG:4326) |
| `dashboard-data.zip` | 121.8 MB | 26891 files | everything the web dashboard loads (unzip at the repository root) |
| `fire_detections_2003.parquet` | 2.2 MB | 62308 rows | FIRMS detections 2003, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2004.parquet` | 2.6 MB | 71465 rows | FIRMS detections 2004, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2005.parquet` | 2.5 MB | 69910 rows | FIRMS detections 2005, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2006.parquet` | 2.5 MB | 70058 rows | FIRMS detections 2006, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2007.parquet` | 2.8 MB | 80434 rows | FIRMS detections 2007, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2008.parquet` | 2.8 MB | 79894 rows | FIRMS detections 2008, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2009.parquet` | 3.4 MB | 97564 rows | FIRMS detections 2009, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2010.parquet` | 2.9 MB | 82102 rows | FIRMS detections 2010, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2011.parquet` | 2.8 MB | 78697 rows | FIRMS detections 2011, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2012.parquet` | 26.1 MB | 694940 rows | FIRMS detections 2012, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2013.parquet` | 21.5 MB | 565312 rows | FIRMS detections 2013, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2014.parquet` | 23.7 MB | 625396 rows | FIRMS detections 2014, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2015.parquet` | 20.8 MB | 547130 rows | FIRMS detections 2015, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2016.parquet` | 29.2 MB | 779973 rows | FIRMS detections 2016, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2017.parquet` | 26.2 MB | 691742 rows | FIRMS detections 2017, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2018.parquet` | 40.0 MB | 1062295 rows | FIRMS detections 2018, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2019.parquet` | 42.1 MB | 1113950 rows | FIRMS detections 2019, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2020.parquet` | 39.0 MB | 1041160 rows | FIRMS detections 2020, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2021.parquet` | 62.1 MB | 1658280 rows | FIRMS detections 2021, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2022.parquet` | 47.4 MB | 1266621 rows | FIRMS detections 2022, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2023.parquet` | 61.5 MB | 1622364 rows | FIRMS detections 2023, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2024.parquet` | 65.1 MB | 1724371 rows | FIRMS detections 2024, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2025.parquet` | 74.5 MB | 1971027 rows | FIRMS detections 2025, all days (GeoParquet, EPSG:4326) |
| `fire_detections_2026.parquet` | 61.2 MB | 1610095 rows | FIRMS detections 2026, all days (GeoParquet, EPSG:4326) |
| `fire_detections_all.csv.gz` | 401.2 MB | 17667088 rows | All FIRMS detections 2003-present in the box 66-98 E, 6-37.5 N, all days, one CSV (same columns as the per-year parquet minus geometry) |
| `s2night_R012_b11_max_100m.tif` | 13.7 MB | 3726 x 2848 px | Sentinel-2A night (Dec 2025): maximum SWIR radiance per 100 m cell, W m-2 sr-1 um-1 (COG, EPSG:4326) |
| `s2night_R012_b12_max_100m.tif` | 12.2 MB | 3726 x 2848 px | Sentinel-2A night (Dec 2025): maximum SWIR radiance per 100 m cell, W m-2 sr-1 um-1 (COG, EPSG:4326) |
| `s2night_R055_b11_max_100m.tif` | 13.6 MB | 3715 x 2853 px | Sentinel-2A night (Dec 2025): maximum SWIR radiance per 100 m cell, W m-2 sr-1 um-1 (COG, EPSG:4326) |
| `s2night_R055_b12_max_100m.tif` | 12.1 MB | 3715 x 2853 px | Sentinel-2A night (Dec 2025): maximum SWIR radiance per 100 m cell, W m-2 sr-1 um-1 (COG, EPSG:4326) |
| `smoke_geosfp_2026.nc` | 104.5 MB | time=424 x lat=137 x lon=123 | GEOS-FP smoke AOD, total AOD, OC/BC/BrC AOD, 10 m wind; 3-hourly, 0.3125 x 0.25 deg (NetCDF4/CF) |

### `fire_detections_<year>.parquet` and `fire_detections_all.csv.gz`

Every FIRMS detection in the box 66-98 E, 6-37.5 N (India and Pakistan country files) on every day of every year, after the dashboard's filters: static land sources (type 2) and offshore (type 3) removed; near-real-time rows in 0.01-degree cells flagged static in any archive year removed; for years covered by the FIRMS yearly country archive only NOAA-21 is taken from the API; duplicate API rows (same position, time, sensor) dropped. GeoParquet 1.x (WKB points, EPSG:4326, zstd). CREAMS events are a separate asset. Row counts reconcile exactly with the dashboard's daily counts (sensor x state x day).

| column | unit | meaning |
|---|---|---|
| `latitude` | deg | pixel-centre latitude (WGS84) |
| `longitude` | deg | pixel-centre longitude (WGS84) |
| `acq_datetime_utc` | UTC | overpass date and time (FIRMS acq_date + acq_time) |
| `acq_date_utc` | date | FIRMS acq_date (UTC) |
| `acq_date_ist` | date | acquisition date in Indian Standard Time (UTC+5:30) |
| `sensor` |  | MODIS Terra | MODIS Aqua | VIIRS S-NPP | VIIRS NOAA-20 | VIIRS NOAA-21 |
| `satellite` |  | FIRMS satellite code as published (Terra, Aqua, T, A, N, N20, 1, N21) |
| `instrument` |  | MODIS or VIIRS |
| `frp_mw` | MW | fire radiative power of the pixel (null if missing) |
| `confidence` |  | low | nominal | high. VIIRS: as published (l/n/h). MODIS: numeric confidence binned < 30 low, 30-79 nominal, >= 80 high (the dashboard's rule) |
| `confidence_raw` |  | confidence as published (MODIS 0-100, VIIRS l/n/h) |
| `daynight` |  | D = day, N = night overpass |
| `bright_t21_k` | K | MODIS channel 21/22 brightness temperature (FIRMS 'brightness'); null for VIIRS |
| `bright_t31_k` | K | MODIS channel 31 brightness temperature; null for VIIRS |
| `bright_ti4_k` | K | VIIRS I-4 brightness temperature; null for MODIS |
| `bright_ti5_k` | K | VIIRS I-5 brightness temperature; null for MODIS |
| `scan_km` | km | along-scan pixel size |
| `track_km` | km | along-track pixel size |
| `version` |  | FIRMS collection/processing version (NRT versions end in 'NRT') |
| `fire_type` |  | FIRMS type: 0 presumed vegetation fire, 1 active volcano (2 static and 3 offshore were removed); null for NRT rows, which carry no type |
| `district_id` |  | district id in datasets/boundaries/districts.geojson (null = outside mapped districts) |
| `district` |  | district name |
| `state` |  | state / province |
| `country` |  | India or Pakistan |
| `season` |  | rice (Sep 15-Nov 30) | wheat (Apr 1-May 31) by UTC date; null otherwise |
| `source_file_family` |  | firms_country_archive (FIRMS yearly country files, standard processing) | firms_api_sp (FIRMS area API, standard processing) | firms_api_nrt (FIRMS area API, near-real-time) |
| `geometry` | EPSG:4326 | point geometry (GeoParquet only) |

Source: NASA FIRMS (MODIS C6.1, VIIRS 375 m). Caveats as in [datasets/fires](datasets/fires/README.md).

```python
import geopandas as gpd, pandas as pd
g = gpd.read_parquet('fire_detections_2024.parquet')       # GeoDataFrame
df = pd.read_parquet('fire_detections_2024.parquet', columns=['latitude','longitude','acq_datetime_utc','sensor','frp_mw','district','state'])
```

```r
library(arrow); df <- read_parquet('fire_detections_2024.parquet', col_select = c(latitude, longitude, sensor, frp_mw))
library(sfarrow) # or sf::st_read() with GDAL >= 3.5 for GeoParquet
```

### `creams_events.parquet` / `creams_events.csv.gz`

Geolocated residue-burning events listed in the annexures of CREAMS daily bulletins (2018 onwards), as printed.

| column | unit | meaning |
|---|---|---|
| `date_ist` | date | date of the bulletin event list (IST) |
| `acq_time_ist` | HH:MM:SS IST | overpass time as printed (IST) |
| `acq_datetime_utc` | UTC | date_ist + acq_time_ist converted to UTC (null if no time printed) |
| `season` |  | rice | wheat |
| `state` |  | state as printed |
| `district` |  | district as printed |
| `district_matched` |  | true if the district name matched the boundary file |
| `tehsil` |  | tehsil / block as printed (null if not given) |
| `latitude` | deg | event latitude as printed (3 decimals) |
| `longitude` | deg | event longitude as printed |
| `satellite` |  | satellite as printed (S-NPP, NOAA-20, NOAA-21, MODIS ...) |
| `day_night` |  | D / N as printed |
| `frp_as_printed` | unclear (printed as W/m2 in some bulletins) | FRP column as printed in the bulletin; units are inconsistent across years - use with caution |
| `bulletin_file` |  | source bulletin PDF (URL in datasets/creams/bulletins.csv) |
| `page` |  | page of the PDF |
| `geometry` | EPSG:4326 | point geometry (GeoParquet only) |

Source and licence: see [datasets/creams](datasets/creams/README.md).

### `smoke_geosfp_<season>.nc`

NASA GMAO GEOS-FP smoke (OC+BC+BrC) and total aerosol optical depth at 550 nm, the three carbon components, and 10 m wind (u, v) on the 0.3125 x 0.25 deg grid (64-94 E, 18-38 N), 3-hourly from Sep 15 of the season to the end of the latest cached forecast. Variables `forecast` (1 = forecast frame), `smoke_frame_source` and `total_aod_frame_source` (0 primary run, 1 other cached run, 2 linear time interpolation) flag each time step; global attributes give the run ids. Only the current season is available (the source keeps a rolling window).

```python
import xarray as xr
ds = xr.open_dataset('smoke_geosfp_2026.nc'); ds.smoke_aod.sel(lat=30.9, lon=75.85, method='nearest').plot()
```

### `burned_area_<season>.tif`

Cloud-Optimized GeoTIFF per paddy season (EPSG:32643, 463 m): 0 = not burned, 10+d = burned on season day d (Sep 15 = day 0), with a colour table and the coding in the GDAL metadata. Produced only when the season mosaics are available locally.

## Conventions

- Dates are ISO 8601. FIRMS dates are UTC unless the column says `_ist`; CREAMS, CPCB and Open-Meteo dates are IST calendar days.
- `season` = the paddy season year (Sep-Nov); the wheat season is Apr-May of the same year.
- `district_id` always refers to `datasets/boundaries/districts.geojson`.
- Missing values are empty cells in CSV and nulls in Parquet/NetCDF; nothing is gap-filled unless a flag column says so.
