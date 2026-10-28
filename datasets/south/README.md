# North to south: Chennai's air, back-trajectories and cyclones

Daily air quality and weather at Chennai and eight other receptor cities from Delhi down the east coast, five-day back-trajectories that say whether the air had passed over burning fields in Punjab, Haryana and Delhi, and every Bay of Bengal / Arabian Sea cyclone since 2003. Built to test whether north Indian farm-fire smoke reaches Chennai, and what cyclones do to the city's air.

## Files

### `cities_daily.csv`

35,577 rows. 2016 to today, every day.

| column | type | unit | meaning |
|---|---|---|---|
| `city` | string |  | receptor city |
| `date` | string | IST date | local calendar day |
| `pm25` | float | ug/m3 | ground PM2.5, city mean of CPCB/TNPCB stations within 35 km (OpenAQ) |
| `pm10` | float | ug/m3 | ground PM10, same |
| `n_stations` | integer | count | stations in the PM2.5 mean |
| `cams_pm25` | float | ug/m3 | CAMS model PM2.5, daily mean |
| `cams_pm10` | float | ug/m3 | CAMS PM10 |
| `cams_co` | float | ug/m3 | CAMS carbon monoxide, a combustion tracer |
| `cams_aod` | float | unitless | CAMS AOD 550 nm |
| `cams_dust` | float | ug/m3 | CAMS dust |
| `wind` | float | m/s | ERA5 10 m wind speed |
| `wdir` | integer | deg (from) | ERA5 10 m wind direction |
| `precip` | float | mm | ERA5 precipitation, daily sum |
| `blh` | float | m | ERA5 boundary-layer height, daily mean |
| `blh14` | float | m | ERA5 boundary-layer height at 14:00 IST |

### `chennai_hourly.csv`

24,301 rows. From 31 Aug 2026, long format.

| column | type | unit | meaning |
|---|---|---|---|
| `time_ist` | string | YYYY-MM-DDTHH | start of the hour, IST |
| `source` | string |  | URL of the bulletin for operational fixes |
| `variable` | string |  | pm25, pm10, co (mg/m3 for OpenAQ CPCB, ug/m3 for CAMS), no2, pm25_24h, pm10_24h, co_8h, no2_24h, wind, wdir, precip, blh, aod |
| `value` | float |  | value in the variable's unit |

### `chennai_reference_stations.csv`

9 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `uid` | string |  | station id in the pol database / OpenAQ |
| `name` | string |  | storm name |
| `lat` | float | deg | latitude |
| `lon` | float | deg | longitude |
| `source` | string |  | network: tnpcb, cpcb, airnow |
| `kind` | string |  | monitor type |
| `first_hour` | string | IST hour | first hour with data |
| `last_hour` | string | IST hour | last hour with data |
| `n_hours` | integer | hours | hours with PM2.5 |
| `qa_excluded` | boolean |  | true if left out of the city mean (distance or QA) |

### `trajectory_exposure_daily.csv`

8,172 rows. 15 Sep - 31 Dec of 2018 onward.

| column | type | unit | meaning |
|---|---|---|---|
| `receptor` | string |  | city the trajectories arrive at |
| `date` | string | IST date | local calendar day |
| `n_trajectories` | integer | count | trajectories of the four daily arrivals (00, 06, 12, 18 UTC) that ran at least 24 h |
| `fire_all` | float | detection-days | VIIRS S-NPP detections in the 0.5 deg cells under the hourly trajectory points, same day and day before, mean over the day's trajectories |
| `fire_nw` | float | detection-days | the same, only inside the Punjab-Haryana-Delhi box 73.5-78.5 E, 27.5-32.5 N |
| `igp_hours` | float | hours | hours of the 120 h spent over the Indo-Gangetic Plain box 74-88 E, 24-31 N |
| `nw_hours` | float | hours | hours over the Punjab-Haryana-Delhi box |
| `sea_hours` | float | hours | hours over the Bay of Bengal / Arabian Sea (coastline approximation) |

### `trajectory_paths_chennai.csv`

17,230 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `arrival_date` | string | UTC date | arrival at Chennai at 06 UTC (11:30 IST) |
| `hours_back` | integer | h | hours before arrival |
| `lon` | float | deg | longitude |
| `lat` | float | deg | latitude |

### `cyclones.csv`

247 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `storm_id` | string |  | IBTrACS storm id (SID) |
| `name` | string |  | storm name |
| `season` | integer | year | season |
| `basin` | string |  | basin where the storm started |
| `grade_max` | string |  | highest IMD grade: D, DD, CS, SCS, VSCS, ESCS, SuCS |
| `imd_wind_max_kt` | float | kt | IMD peak 3-min sustained wind |
| `imd_pres_min_hpa` | float | hPa | IMD minimum central pressure |
| `start_utc` | string | UTC | first track point |
| `end_utc` | string | UTC | last track point |
| `landfall_utc` | string | UTC | first crossing of the coast (IBTrACS land flag; IMD statement for the current storm) |
| `landfall_lat` | float | deg | landfall latitude |
| `landfall_lon` | float | deg | landfall longitude |
| `closest_to_chennai_km` | float | km | minimum distance of the track line to Chennai (13.08 N, 80.27 E) |
| `closest_time_utc` | string | UTC | time of the closest approach |
| `bearing_from_chennai` | string |  | direction of the closest point seen from Chennai |
| `status` | string |  | best track, or operational (not best track) for the current storm |

### `cyclone_track_points.csv`

9,026 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `storm_id` | string |  | IBTrACS storm id (SID) |
| `name` | string |  | storm name |
| `time_utc` | string | UTC | fix time |
| `lat` | float | deg | latitude |
| `lon` | float | deg | longitude |
| `wind_kt` | float | kt | IMD 3-min wind (USA 1-min where IMD has none) |
| `pres_hpa` | float | hPa | central pressure |
| `grade` | string |  | IMD grade at the fix: D, DD, CS, SCS, VSCS, ESCS, SuCS (blank where IMD has none) |
| `kind` | string |  | best (IBTrACS best track), observed (operational fix), forecast (IMD forecast point) |
| `agency` | string |  | agency of the fix |
| `source` | string |  | URL of the bulletin for operational fixes |

## Coverage

Cities: 9, 2016-01-01..2026-10-27. Trajectories: 9 receptors, 2018-09-15..2026-10-28 (seasons only). Cyclones: 247 storms 2003-2026, 9,026 track points.

## Source and licence

- CPCB / TNPCB monitors via OpenAQ (credit OpenAQ and CPCB); TNPCB running means as collected by pol.reclaimchennai.city.
- CAMS and ERA5 via Open-Meteo (CC BY 4.0; contains modified Copernicus Atmosphere Monitoring Service / Climate Change Service information).
- Winds for the trajectories: NASA GMAO GEOS-FP assimilation (tavg1_2d_slv_Nx U850/V850), public domain.
- Fires: NASA FIRMS VIIRS S-NPP (public domain; cite FIRMS).
- Cyclones: IBTrACS v04r01 (Knapp et al. 2010; NOAA NCEI) and IMD RSMC New Delhi advisories (Government of India).

See ../../LICENSES.md.

## Known caveats

- Trajectories are 2-D at 850 hPa: no vertical motion, mixing, rain-out or chemistry. A path over Punjab says the air could have carried smoke, not that it did.
- OpenAQ has no CPCB data from Nov 2022 to Feb 2025 and thin coverage in 2018, early 2019 and 2021; CAMS fills the gap but reads Chennai PM2.5 about half of the monitors.
- The TNPCB values collected by pol.reclaimchennai.city are AQI sub-indices of 24 h (PM, NO2) and 8 h (CO) running means; they are converted back to concentrations here with CPCB breakpoints.
- OpenAQ labels CPCB CO and NO2 as ppb; the values are CPCB's mg/m3 and ug/m3.
- aqi.in low-cost sensor readings used on the website are not redistributed here.
- IBTrACS landfall times can be hours off for storms that hug the coast (Michaung 2023, Fengal 2024); the current storm is operational data, not best track.

## How it was produced

`scripts/build_aq_south.py`, `scripts/transport.py`, `scripts/build_cyclones.py` write `data/db/extras/*.json`; `scripts/export_release.py` flattens them.

## Loading

Python (pandas):

```python
import pandas as pd
t = pd.read_csv("trajectory_exposure_daily.csv", parse_dates=["date"])
a = pd.read_csv("cities_daily.csv", parse_dates=["date"])
chn = t[t.receptor == "Chennai"].merge(a[a.city == "Chennai"], on="date")
```

R:

```r
library(readr)
t <- read_csv("trajectory_exposure_daily.csv")
```
