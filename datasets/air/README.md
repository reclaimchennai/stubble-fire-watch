# Air quality and meteorology at receptor cities

Ground PM2.5/PM10 from CPCB continuous monitoring stations (via OpenAQ), modelled PM/AOD (CAMS) and reanalysis meteorology (ERA5) at seven receptor cities, and published model estimates of the stubble-burning share of Delhi's PM2.5 (IITM SAFAR / DSS / AQEWS).

## Files

### `cpcb_pm25_daily.csv`

33,246 rows. Window: Sep 1 - Jan 31 of each winter with data.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `state` | string |  | state |
| `date` | string | IST date | local calendar day (Asia/Kolkata) |
| `pm25` | float | ug/m3 | city mean of qualifying CPCB station daily means of PM2.5 (null = no qualifying data) |
| `pm10` | float | ug/m3 | same for PM10 |
| `n_stations` | integer | count | stations averaged for PM2.5 that day |
| `n_stations_pm10` | integer | count | stations averaged for PM10 that day |

### `cpcb_pm25_monthly.csv`

2,165 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `state` | string |  | state |
| `month` | string | YYYY-MM | month |
| `pm25` | float | ug/m3 | city mean of qualifying CPCB station daily means of PM2.5 (null = no qualifying data) |
| `pm10` | float | ug/m3 | same for PM10 |
| `n_days_pm25` | integer | days | valid days in the month (PM2.5) / at the station |
| `n_days_pm10` | integer | days | valid days (PM10) |
| `mean_n_stations` | float | count | mean stations per valid day |

### `cpcb_stations.csv`

92 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `state` | string |  | state |
| `station_id` | string |  | station key used in the merge (city|site|agency) |
| `station_name` | string |  | CPCB station name |
| `lat` | float | deg | latitude (WGS84) |
| `lon` | float | deg | longitude (WGS84) |
| `first_pm25` | string | date | first valid PM2.5 day |
| `last_pm25` | string | date | last valid PM2.5 day |
| `first_pm10` | string | date | first valid PM10 day |
| `last_pm10` | string | date | last valid PM10 day |
| `n_days_pm25` | integer | days | valid PM2.5 days at the station |
| `n_days_pm10` | integer | days | valid days (PM10) |
| `openaq_location_ids` | string |  | OpenAQ v3 location ids merged into this station (;-separated) |
| `openaq_sensor_ids` | string |  | OpenAQ v3 sensor ids used (;-separated) |

### `cams_era5_daily.csv`

22,603 rows. Window: Sep 15 - Jan 31 of each season.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `date` | string | IST date | local calendar day (Asia/Kolkata) |
| `pm25_cams` | float | ug/m3 | CAMS model PM2.5, daily mean of hourly values (IST day; null if < 18 valid hours) |
| `pm10_cams` | float | ug/m3 | CAMS model PM10, daily mean |
| `aod` | float | unitless | CAMS aerosol optical depth at 550 nm, daily mean |
| `dust` | float | ug/m3 | CAMS dust concentration, daily mean |
| `wind_ms` | float | m/s | ERA5 10 m wind speed, daily mean |
| `wind_dir` | integer | deg (from) | ERA5 dominant 10 m wind direction |
| `blh_mean_m` | float | m | ERA5 boundary-layer height, daily mean of hourly values |
| `blh_14ist_m` | float | m | ERA5 boundary-layer height at 14:00 IST |
| `t2m` | float | deg C | ERA5 2 m temperature, daily mean |
| `precip_mm` | float | mm | ERA5 precipitation, daily sum |

### `cams_era5_cities.csv`

7 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `lat` | float | deg | latitude (WGS84) |
| `lon` | float | deg | longitude (WGS84) |
| `cams_grid_lat` | float | deg | latitude of the CAMS grid point used |
| `cams_grid_lon` | float | deg | longitude of the CAMS grid point |
| `era5_grid_lat` | float | deg | latitude of the ERA5 grid point used |
| `era5_grid_lon` | float | deg | longitude of the ERA5 grid point |

### `stubble_share_delhi.csv`

1,929 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `source` | string |  | series: IITM_DSS, IITM_AQEWS_fire_contribution, IITM_AQEWS_VIIRS_fire_count, SAFAR, SAFAR_fire_count |
| `date` | string | IST date | local calendar day (Asia/Kolkata) |
| `share_pct` | float | % of Delhi PM2.5 | modelled share of Delhi PM2.5 attributed to stubble/biomass burning (null for count-only series) |
| `fire_count` | integer | count | fire count published with the series (IITM: daily VIIRS count for 'northern India'; SAFAR: 'effective' fire count) |
| `date_basis` | string |  | SAFAR only: explicit = date in text; capture = 'today' in an undated bulletin (IST date of the web capture, -1 day if before noon); capture-1 = 'yesterday' |
| `kind` | string |  | SAFAR only: estimate (for the day) or forecast (for a future date) |
| `url` | string |  | page or Internet Archive capture the value was read from |
| `retrieved` | string | UTC timestamp | capture/fetch time |
| `quote` | string |  | SAFAR only: verbatim text containing the number |

## Coverage

CPCB: 36 cities, 92 stations, 2016-09-01..2026-10-18 (winters only; OpenAQ has no CPCB data Oct 2022 - Feb 2025). CAMS/ERA5: 7 cities, 2003-09-15..2026-10-16 (CAMS values null before Aug 2022). Stubble share: 2019-11-04..2026-10-17, sparse.

## Source and licence

- CPCB CAAQMS data redistributed by OpenAQ (https://openaq.org, API v3). OpenAQ requires crediting both OpenAQ and the original provider (CPCB); the data are government data published by CPCB.
- Open-Meteo Air Quality API (CAMS global forecasts, Copernicus Atmosphere Monitoring Service) and Open-Meteo Historical Weather API (ERA5/ERA5-Land, Copernicus Climate Change Service). Open-Meteo data are CC BY 4.0; CAMS/C3S data are under the Copernicus licence - credit 'Contains modified Copernicus Atmosphere Monitoring Service / Climate Change Service information'.
- IITM Pune (Ministry of Earth Sciences) public web pages, mostly via Internet Archive captures:
  - `IITM_DSS`: IITM Decision Support System for Air Quality Management in Delhi. https://ews.tropmet.res.in/dss/index.php WRF-Chem based DSS; the day-0 value is the model run for the previous day; forward days in the page's 4-day forecast (not stored here) use climatological fire emissions
  - `IITM_AQEWS_fire_contribution`: IITM Air Quality Early Warning System - 'Daily Mean Fire Contribution to Air Pollution in Delhi' (PM2.5 dataset). https://ews.tropmet.res.in/ob_fire_count.php inline Chart.js 'data2', dataset 'PM2.5' (%); same model output as the DSS series but published with null for missing days; kept separately, not merged
  - `IITM_AQEWS_VIIRS_fire_count`: IITM AQEWS daily VIIRS fire counts. https://ews.tropmet.res.in/ob_fire_count.php Chart.js datasets 'VIIRS YYYY' (used only where the chart's own date labels are published with equal length); domain described by IITM as 'northern India', not state-specific
  - `SAFAR`: SAFAR (IITM Pune) Delhi short-range forecast bulletin, homepage marquee. http://safar.tropmet.res.in/ 
  - `SAFAR_fire_count`: SAFAR effective fire counts with an explicit date in the text. http://safar.tropmet.res.in/ 

See ../../LICENSES.md.

## Known caveats

- CPCB city means average whichever stations qualified that day; network size changed over time (use n_stations).
- QA: a station-day is kept only with >= 75% of native records and >= 17 h span; days with mean <= 0 or > 1500 ug/m3, or any sub-daily value > 1500, are dropped. No gap filling.
- OpenAQ ingestion gaps (not station downtime): Oct 2022 - Feb 2025 missing, Jan-Jun 2019 nearly empty.
- CAMS/ERA5 values are model/reanalysis output at the nearest grid point, not measurements; CAMS hours 2022-12-13..16 with PM2.5 < 3 ug/m3 were nulled (archive glitch).
- Stubble-share values are model attributions from different systems (SAFAR 2018-2023, IITM DSS 2023+): do not splice them into one series without noting the break.
- ERA5 boundary-layer height is null for January 2024 in the Open-Meteo archive.

## How it was produced

`scripts/build_aq_ground.py` (OpenAQ), `scripts/build_db_extras.py` (Open-Meteo) and the stubble-share builder write `data/db/extras/*.json`; `scripts/export_release.py` flattens them.

## Loading

Python (pandas):

```python
import pandas as pd
pm = pd.read_csv("cpcb_pm25_daily.csv", parse_dates=["date"])
delhi = pm[pm.city == "Delhi"].set_index("date").pm25
```

R:

```r
library(readr)
pm <- read_csv("cpcb_pm25_daily.csv")
```
