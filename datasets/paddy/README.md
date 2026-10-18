# Rice (paddy) area and production, by state and district

Kharif and all-season rice area and production from India's official Area-Production-Yield statistics, for the states in the dashboard. Kharif rice area is the main driver of the autumn residue load.

## Files

### `rice_area_state.csv`

508 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `state` | string |  | state |
| `year` | integer |  | agricultural-year start: 2023 = 2023-24 (July-June); kharif rice of year Y is harvested Oct-Nov of Y, i.e. burned in the Y fire season |
| `agri_year` | string |  | agricultural year label |
| `kharif_area_ha` | integer | ha | kharif rice area |
| `kharif_prod_t` | integer | t | kharif rice production (milled rice, not paddy) |
| `total_area_ha` | integer | ha | all-season rice area |
| `total_prod_t` | integer | t | all-season rice production (milled rice) |
| `district_sum_kharif_area_ha` | integer | ha | sum of the district kharif values in rice_area_district.csv (1997+), for comparison with the official state figure |
| `district_sum_kharif_prod_t` | integer | t | same for production |
| `estimate_type` | string |  | Final Estimate, or the advance-estimate round for the latest year |

### `rice_area_district.csv`

6,674 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `state` | string |  | state |
| `district` | string |  | district name as reported by UPAg/DES (current LGD name) |
| `lgd_code` | integer |  | Local Government Directory district code |
| `district_id` | integer |  | matching polygon id in ../boundaries/districts.geojson (several APY districts can share one) |
| `district_id_name` | string |  | name of that polygon |
| `match_method` | string |  | name | lgd_crosswalk | alias_rename | alias_parent (district created after 2021 mapped to its parent polygon; sum children with the parent) |
| `year` | integer |  | agricultural-year start: 2023 = 2023-24 (July-June); kharif rice of year Y is harvested Oct-Nov of Y, i.e. burned in the Y fire season |
| `agri_year` | string |  | agricultural year label |
| `kharif_area_ha` | integer | ha | kharif rice area |
| `kharif_prod_t` | integer | t | kharif rice production (milled rice, not paddy) |
| `total_area_ha` | integer | ha | all-season rice area |
| `total_prod_t` | integer | t | all-season rice production (milled rice) |

## Coverage

States: 1966-2025 (9 states). Districts: 1997-2024 (267 district series).

## Source and licence

District: UPAg 'Complete APY Dataset (District Level)', DA&FW/DES, Final Estimates 1998-99..2024-25 (retrieved 2026-10-08); 1997-98 from DES Crop Production Statistics via India Data Portal mirror (ODC-By). State: UPAg 'Crop Profile: State-wise APY' (official), 1997-98..2025-26.

UPAg (Unified Portal for Agricultural Statistics, upag.gov.in) and DES (Directorate of Economics and Statistics, Ministry of Agriculture & Farmers Welfare) publish these as open government data; the Government Open Data License - India (GODL) is assumed (licence not stated on every UPAg download; see ../../LICENSES.md).

## Known caveats

- Bihar 1998-2022: UPAg district rows label winter (aghani) rice as 'rabi'; these are counted in kharif here because district kharif+rabi matches the official state kharif figure. Other states: rabi/summer rice excluded from kharif, included in *_total.
- as reported (not benchmarked to state totals); district boundaries as reported each year; district sums differ from state totals in many years.
- Delhi has no district-level rice rows in the APY district data; state-level only.
- Production is milled rice (not paddy); multiply by ~1.5 for paddy.
- District boundaries change over time (e.g. Malerkotla 2021); use match_method / district_id to aggregate consistently.

## How it was produced

`scripts/build_db_extras.py` builds `data/db/extras/rice_area.json`; `scripts/export_release.py` flattens it.

## Loading

Python (pandas):

```python
import pandas as pd
d = pd.read_csv("rice_area_district.csv")
punjab = d.query("state == 'Punjab'").pivot_table(index="year", columns="district", values="kharif_area_ha")
```

R:

```r
library(readr)
d <- read_csv("rice_area_district.csv")
```
