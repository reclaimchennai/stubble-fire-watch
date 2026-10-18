# Enforcement and crop-residue-management indicators (Punjab, Haryana)

Field inspections, environmental compensation, FIRs, red entries, action against officials, CRM machines, custom hiring centres, funds and residue management, as stated in official documents (parliament answers, CAQM directions and reports, NGT/Supreme Court filings, state agriculture departments, PPCB/HSPCB, data.gov.in tables), with a timeline of policy events.

## Files

### `values.csv`

1,690 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `metric` | string |  | metric key (see metrics.csv) |
| `label` | string |  | metric description |
| `unit` | string |  | unit of value (count, Rs, Rs crore, lakh t, ...) |
| `state` | string |  | Punjab, Haryana, or a combined key such as 'Punjab+Haryana+UP-NCR (combined)' - never add combined rows to state totals |
| `district` | string |  | district (empty = state-level value) |
| `season` | integer |  | paddy season year (Sep-Nov); FY 2018-19 figures are assigned to 2018 |
| `as_of` | string | date | date the figure refers to (cumulative 'as on' date, end of window, or answer date) |
| `value` | float | see unit | value as stated in the source (converted to the metric unit) |
| `preferred` | boolean |  | true = latest-published entry for (metric, place, season, period) |
| `latest` | boolean |  | true = the one preferred entry per (metric, place, season) with the latest as_of: use for one-number-per-season charts |
| `flags` | string |  | transcribed_from_image (read from a scanned page, arithmetic-checked); derived (computed, e.g. sum of districts); suspect_unit; non_afl |
| `source_id` | string |  | id in sources.csv |
| `period` | string |  | reporting window as stated |
| `basis` | string |  | seasonal | cumulative | financial year | stock | period total |
| `name_in_source` | string |  | place name as printed |
| `value_as_stated` | string |  | original value and unit where a conversion was made |
| `derived` | string |  | how a derived value was computed |
| `note` | string |  | curator's note |
| `quote` | string |  | verbatim passage containing the number (checked against the source at build time) |

### `metrics.csv`

41 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `metric` | string |  | metric key (see metrics.csv) |
| `label` | string |  | metric description |
| `unit` | string |  | unit of value (count, Rs, Rs crore, lakh t, ...) |
| `note` | string |  | definition notes |

### `sources.csv`

91 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `id` | string |  | source id |
| `title` | string |  | document title |
| `publisher` | string |  | issuing body |
| `kind` | string |  | document type |
| `date` | string | date | document / event date |
| `url` | string |  | public URL of the document |
| `retrieved` | string | date | date fetched |
| `note` | string |  | note about the document |

### `events.csv`

36 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `date` | string | date | document / event date |
| `date_precision` | string |  | precision of the event date where not a full date |
| `state` | string |  | Punjab, Haryana, or a combined key such as 'Punjab+Haryana+UP-NCR (combined)' - never add combined rows to state totals |
| `district` | string |  | district (empty = state-level value) |
| `title` | string |  | document title |
| `detail` | string |  | event description |
| `source_id` | string |  | id in sources.csv |
| `quote` | string |  | verbatim passage containing the number (checked against the source at build time) |
| `listing_title` | string |  | title of the listing the document came from |
| `transcribed_from_image` | string |  | event read from a scanned page |
| `other` | string |  | JSON object with event-specific fields (e.g. incentive rates, EC rates, sowing dates) |

### `ENFORCEMENT_SOURCES.md`

Human-readable list of every source document with what it contributed, and the caveats on counting bases.

## Coverage

Seasons 2016-2026; 41 metrics; 91 source documents.

## Source and licence

Public government documents (Lok Sabha / Rajya Sabha answers, CAQM, NGT, Supreme Court, Punjab and Haryana government departments, data.gov.in under GODL). Each value carries its source URL; cite the original document. See ../../LICENSES.md.

## Known caveats

- Different counting bases: CAQM/ISRO-protocol fire counts (Sep 15-Nov 30) differ from state lists and Oct 1-Nov 30 windows.
- Cumulative vs seasonal vs financial-year figures are mixed; read `basis` and `period`. Use `latest` for one value per season.
- EC imposed vs recovered are separate metrics; FIR basis changed from IPC s.188 to BNS s.223 on 1 July 2024.
- Ministries revise numbers between answers; all published versions are kept (preferred/latest flag the newest).
- Values from scanned pages (flag transcribed_from_image) were typed from images and checked against row/column totals.

## How it was produced

`scripts/build_enforcement.py` builds `data/db/extras/enforcement.json` and `ENFORCEMENT_SOURCES.md` from cached documents; `scripts/export_release.py` flattens them (local cache paths removed).

## Loading

Python (pandas):

```python
import pandas as pd
v = pd.read_csv("values.csv")
ec = v.query("metric == 'ec_cases' and district.isna() and latest", engine="python")
```

R:

```r
library(readr); library(dplyr)
v <- read_csv("values.csv")
v %>% filter(metric == "ec_cases", is.na(district), latest)
```
