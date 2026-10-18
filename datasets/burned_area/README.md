# Burned area (NASA MODIS MCD64A1) in Punjab and Haryana, paddy seasons

Area of cropland mapped as burned each paddy season (Sep 15 - Dec 5) by NASA's MODIS burned-area product, per state, district and burn date, with a cross-check against FIRMS active-fire detections. Per-season rasters are release assets `burned_area_<season>.tif`.

## Files

### `state_season.csv`

92 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `season` | integer |  | paddy season year (Sep 15 - Dec 5 of that year) |
| `state` | string |  | Punjab, Haryana, Delhi or Chandigarh (India) |
| `burned_ha` | integer | ha | MCD64A1 burned area: number of burned 500 m pixels x pixel_ha, rounded |
| `complete` | boolean |  | true if all MCD64A1 months Sep-Nov were available (false: area is a lower bound for missing months) |
| `missing_months` | string | YYYY-MM;... | monthly MCD64A1 files not available on the source when built |
| `months_used` | string | YYYY-MM;... | monthly files used |
| `through` | string | date | last date covered |
| `pixel_ha` | float | ha | area of one pixel in the UTM 43N mosaic (463.3 m)^2 |
| `product` | string |  | product name and version |

### `district_season.csv`

1,265 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `season` | integer |  | paddy season year (Sep 15 - Dec 5 of that year) |
| `district_id` | integer |  | id in ../boundaries/districts.geojson |
| `district` | string |  | district name |
| `state` | string |  | Punjab, Haryana, Delhi or Chandigarh (India) |
| `burned_ha` | integer | ha | MCD64A1 burned area: number of burned 500 m pixels x pixel_ha, rounded |
| `complete` | boolean |  | true if all MCD64A1 months Sep-Nov were available (false: area is a lower bound for missing months) |

### `daily_by_state.csv`

7,544 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `season` | integer |  | paddy season year (Sep 15 - Dec 5 of that year) |
| `date` | string | date | burn date (MCD64A1 Burn_Date) |
| `season_day` | integer | days | days since Sep 15 |
| `state` | string |  | Punjab, Haryana, Delhi or Chandigarh (India) |
| `burned_ha` | float | ha | area that burned on that date |
| `complete` | boolean |  | true if all MCD64A1 months Sep-Nov were available (false: area is a lower bound for missing months) |

### `firms_check.csv`

37 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `season` | integer |  | paddy season year (Sep 15 - Dec 5 of that year) |
| `sensor_group` | string |  | modis (Terra+Aqua) or viirs_snpp FIRMS detections used in the cross-check |
| `detections` | integer | count | FIRMS detections in the region during the season |
| `share_on_burned_pixel` | float | fraction | share of those detections that fall on a pixel MCD64A1 maps as burned |

## Coverage

Seasons 2003-2025; 2 season(s) incomplete (see `complete`, `missing_months`).

## Source and licence

NASA MODIS/Terra+Aqua Burned Area Monthly L3 Global 500 m, MCD64A1 Collection 6.1 (doi:10.5067/MODIS/MCD64A1.061), accessed through Microsoft Planetary Computer. NASA LP DAAC data have no restrictions on use; cite the product DOI.

## Known caveats

- 500 m pixels are coarse for Punjab's 1-2 ha fields: many small, short burns are missed. Read areas as a lower bound and the spatial/temporal pattern as the signal.
- Seasons with missing months (e.g. 2023: Sep missing, 2025: Sep-Oct missing at the source) understate burned area.
- Only Punjab, Haryana, Delhi and Chandigarh are mapped (UTM 43N mosaic).

## How it was produced

`scripts/burnscar.py` builds `data/burnscar/burnscar.json`; `scripts/export_release.py` flattens it.

## Loading

Python (pandas):

```python
import pandas as pd
ss = pd.read_csv("state_season.csv")
ss.pivot(index="season", columns="state", values="burned_ha")
```

R:

```r
library(readr)
ss <- read_csv("state_season.csv")
```
