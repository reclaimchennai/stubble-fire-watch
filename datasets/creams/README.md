# ICAR-IARI CREAMS crop-residue burning bulletins

Official daily counts of paddy- and wheat-residue burning events in Punjab, Haryana, Uttar Pradesh, Delhi, Rajasthan and Madhya Pradesh, as printed in the daily bulletins of the Consortium for Research on Agroecosystem Monitoring and Modeling from Space (CREAMS), ICAR-Indian Agricultural Research Institute, New Delhi. CREAMS applies the standard ISRO/CAQM protocol to VIIRS/MODIS detections and filters them to agricultural residue burning. The ~700k geolocated events listed in the bulletin annexures are a release asset (`creams_events.parquet`).

## Files

### `state_daily.csv`

5,458 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `date` | string | IST date | date the count refers to (bulletin day) |
| `season` | string |  | rice (paddy, autumn) or wheat (spring) burning season |
| `season_year` | integer |  | year of the season |
| `state` | string |  | state as printed in the bulletin |
| `fires_day` | integer | count | residue-burning events detected that day (null = not reported) |
| `fires_cumulative` | integer | count | cumulative events from the season start as printed (null = not reported) |
| `cum_start` | string | date | start date of the cumulative count, where printed |
| `day_source` | string |  | bulletin table the daily value came from (datewise_table, event_list_count = count of rows in the annexed event list) |
| `cum_source` | string |  | bulletin table the cumulative value came from (highlights, datewise_table_total, district_table) |
| `fires_day_as_published` | integer | count | daily value as printed when it differs from the value kept in fires_day |
| `bulletin_file` | string |  | file name of the bulletin PDF the value was read from (see bulletins.csv for its URL) |
| `cum_bulletin_file` | string |  | bulletin the cumulative value was read from |

### `district_daily.csv`

194,903 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `date` | string | IST date | date the count refers to (bulletin day) |
| `season` | string |  | rice (paddy, autumn) or wheat (spring) burning season |
| `season_year` | integer |  | year of the season |
| `state` | string |  | state as printed in the bulletin |
| `district` | string |  | district as printed in the bulletin |
| `fires_day` | integer | count | residue-burning events detected that day (null = not reported) |
| `fires_cumulative` | integer | count | cumulative events from the season start as printed (null = not reported) |
| `cum_period_start` | string | date | start date of the cumulative count |
| `day_source` | string |  | bulletin table the daily value came from (datewise_table, event_list_count = count of rows in the annexed event list) |
| `cum_source` | string |  | bulletin table the cumulative value came from (highlights, datewise_table_total, district_table) |
| `cum_check` | string |  | sum_ok = district cumulative values add up to the state total printed in the same bulletin |
| `bulletin_file` | string |  | file name of the bulletin PDF the value was read from (see bulletins.csv for its URL) |
| `cum_bulletin_file` | string |  | bulletin the cumulative value was read from |

### `year_compare.csv`

5,447 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `season` | string |  | rice (paddy, autumn) or wheat (spring) burning season |
| `state` | string |  | state as printed in the bulletin |
| `as_of_date` | string | MM-DD | calendar day the cumulative comparison refers to |
| `year` | integer |  | season year being compared |
| `cumulative_fires` | integer | count | cumulative events from period_start to as_of_date in that year, as printed in the bulletin's multi-year table |
| `period_start` | string | MM-DD | start of the cumulative window |
| `source` | string |  | bulletin table the value came from |
| `bulletin_date` | string | date | date of the bulletin that printed the comparison |
| `bulletin_file` | string |  | file name of the bulletin PDF the value was read from (see bulletins.csv for its URL) |
| `note` | string |  | parsing note |

### `bulletins.csv`

852 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `date` | string | IST date | date the count refers to (bulletin day) |
| `season` | string |  | rice (paddy, autumn) or wheat (spring) burning season |
| `kind` | string |  | bulletin (daily bulletin) or other publication type |
| `bulletin_no` | string |  | running bulletin number in the season |
| `file` | string |  | PDF file name |
| `url` | string |  | public URL of the PDF on creams.iari.res.in |
| `pages` | string |  | number of pages |
| `primary` | string |  | 1 = the bulletin used for that date (0 = duplicate/superseded copy) |
| `duplicate_of` | string |  | file this one duplicates |
| `error` | string |  | parse error, if the PDF could not be read |

### `monthly_reports.csv`

876 rows. From CREAMS special reports (2020): monthly events by state.

| column | type | unit | meaning |
|---|---|---|---|
| `state` | string |  | state as printed in the bulletin |
| `month` | string | YYYY-MM | month |
| `events` | integer | count | events in the month as printed in the special annual burn-statistics report |
| `report_file` | string |  | report PDF file name |
| `url` | string |  | public URL of the PDF on creams.iari.res.in |

### `bulletin_state_tables_long.csv.gz`

538,853 rows. Every cell of the date-wise multi-year state tables printed in each bulletin (one row per bulletin x state x year x day); the raw material of state_daily / year_compare.

| column | type | unit | meaning |
|---|---|---|---|
| `bulletin_date` | string | date | date of the bulletin |
| `season` | string |  | rice (paddy, autumn) or wheat (spring) burning season |
| `state` | string |  | state as printed in the bulletin |
| `target_year` | integer |  | year the printed column refers to |
| `month_day` | string | MM-DD | day of the printed row |
| `value` | integer | count | value printed in the bulletin's date-wise multi-year state table |
| `bulletin_file` | string |  | file name of the bulletin PDF the value was read from (see bulletins.csv for its URL) |

### `validation.json`

Consistency checks run when the bulletins were parsed: bulletins that failed, event lists vs highlight counts, table sums vs printed totals, district sums vs state totals, non-monotonic cumulative series, duplicates, date mismatches, unmatched district names, and a `summary` block.

## Coverage

Bulletins 2018-2026 (rice seasons from 2018, wheat seasons where published); multi-year tables in bulletins reach back to 2016. 852 PDFs listed.

## Source and licence

Bulletins published at https://creams.iari.res.in/ (page_id=1123). They are public documents with no explicit licence; cite ICAR-IARI CREAMS and the bulletin (URLs in bulletins.csv). Licence status: uncertain, treat as public government information with attribution.

## Known caveats

- Values are exactly as printed; nothing interpolated. Bulletins sometimes revise earlier days; both the daily and the cumulative values are kept with their bulletin.
- The CREAMS protocol window is Sep 15-Nov 30 (rice) and Apr 1-May 31 (wheat); some parliamentary/state numbers use Oct 1-Nov 30.
- Counts are protocol-filtered events, not raw FIRMS detections; they are not comparable one-to-one with ../fires/.
- District names are as printed (spelling varies across years); see validation.json for names that did not match the boundary file.

## How it was produced

PDFs downloaded and parsed by `scripts/creams_scrape.py` into `data/creams/*.csv`; `scripts/export_release.py` copies them with numeric typing and selected columns.

## Loading

Python (pandas):

```python
import pandas as pd
sd = pd.read_csv("state_daily.csv", parse_dates=["date"])
punjab_2024 = sd.query("state == 'Punjab' and season == 'rice' and season_year == 2024")
```

R:

```r
library(readr)
sd <- read_csv("state_daily.csv")
```
