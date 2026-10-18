# How the data was made

Every number in this repository comes from a public source and a script in [`scripts/`](../scripts/). This page
explains, source by source, what was taken, how it was processed, how it was checked, and what it cannot tell you.

- [1. Fire detections (NASA FIRMS)](#1-fire-detections-nasa-firms)
- [2. Official counts (ICAR-IARI CREAMS bulletins)](#2-official-counts-icar-iari-creams-bulletins)
- [3. Burned area (NASA MODIS MCD64A1), and a Sentinel-2 method that failed](#3-burned-area)
- [4. Smoke (NASA GMAO GEOS-FP)](#4-smoke-nasa-gmao-geos-fp)
- [5. Air quality and the stubble share](#5-air-quality-and-the-stubble-share)
- [6. Paddy area](#6-paddy-area)
- [7. Enforcement and residue management](#7-enforcement-and-residue-management)
- [8. Boundaries and the map](#8-boundaries-and-the-map)

---

## 1. Fire detections (NASA FIRMS)

**What it is.** NASA's Fire Information for Resource Management System lists every pixel in which a polar-orbiting
satellite saw an active fire: MODIS on Terra (≈10:30 IST pass) and Aqua (≈13:30) at 1 km since 2000, and VIIRS on
S-NPP (2012+), NOAA-20 (2018+) and NOAA-21 (2023+) at 375 m, which also have a night pass near 01:30 IST.
Each detection carries a time, a fire radiative power (FRP, in MW) and a confidence.

**How it was taken** (`scripts/firms_archive.sh`, `scripts/firms_fetch.py`)
- 2003-2025: NASA's public yearly country files for India and Pakistan (no key). The server limits each connection to
  about 110 KB/s, so the 90 files are fetched 12 at a time.
- The current year: the FIRMS area API for 68.5-89 E, 21-35.5 N in 5-day chunks, standard-processing data up to
  June and near-real-time data after. Near-real-time chunks are re-fetched for three days because NASA keeps filling
  them in; empty chunks (a feed outage) are retried on every run.
- NOAA-21 is not in the yearly files, so 2023-2025 NOAA-21 comes from the API.

**How it was processed** (`scripts/build_fires.py`)
- Clip to the study box. Drop FIRMS type 2 (static land sources such as refineries and kilns) and type 3 (offshore).
  Near-real-time rows have no type, so any 1 km cell the archive ever flagged as static is dropped from them too.
- Once a year's yearly file exists, API rows for that year are ignored (except NOAA-21) so nothing is counted twice.
- Assign each detection a district by looking it up in a 0.01° raster of the district polygons.
- **Detections are never merged across satellites.** One burning field can appear five times. Year-on-year
  comparisons use one sensor family: MODIS Terra + Aqua (2003 onward) or VIIRS S-NPP (2012 onward).

**Checked by:** the exported tables reconcile cell by cell (year × sensor × state × day) with the dashboard's
summary, 10,551,119 detections in all. Punjab season totals agree with published FIRMS-based studies.

**Limits.** A detection is a fire burning at overpass time. Fires lit between passes, short fires, fires under thick
smoke or cloud, and very small fires are missed. Hot desert surfaces in west Rajasthan occasionally produce daytime
VIIRS false alarms (e.g. 313 NOAA-20 points near Bikaner on 14 Oct 2026, seen by no other satellite).

![Detections by IST hour of overpass](images/db-timing.png)

*The overpass schedule shapes what is seen. The "night pass share" (detections on the 01:30 IST VIIRS pass) rose from
1.8% in 2012 to 8.7% in 2025, while the Aqua (13:30) to Terra (10:30) ratio climbed: burning has shifted later in the
day, between the passes.*

---

## 2. Official counts (ICAR-IARI CREAMS bulletins)

**What it is.** The Consortium for Research on Agroecosystem Monitoring and Modeling from Space (CREAMS) at the
Indian Agricultural Research Institute publishes a bulletin every day of the paddy (15 Sep - 30 Nov) and wheat
(Apr - May) burning seasons. These are the figures the government quotes.

![A CREAMS bulletin: cover, highlights, daily charts](images/methods/creams_bulletin_p1.png)
![Highlights and the state chart](images/methods/creams_bulletin_p2.png)

**How it was taken** (`scripts/creams_scrape.py`). The crop-residue page links the current bulletins; older seasons
(rice 2018-2026, wheat 2022-2026 and special reports) sit in an open directory listing. All 851 PDFs are downloaded at
one request per second and cached.

**How it was processed.** Each PDF's text layer is parsed (pdfplumber / PyMuPDF), not OCR'd:
- the *Highlights* sentences: "Total 41463 burning events ... 32879, 4216 ... in Punjab, Haryana ...";
- state day-wise tables and the chart data labels;
- district tables with daily and cumulative counts;
- multi-year comparison tables (the same date in earlier seasons), which give 2016-2019 values;
- the annexure listing every event with its district, tehsil, latitude, longitude, satellite and time: 698,696 events.

**Checked by** `data/creams/creams_validation.json`, written by the parser: cumulative series must not decrease;
day counts must equal differences of cumulative counts; district rows must sum to state totals; table totals must
match the highlights sentence; event-list counts must match the day's total. Mismatches are listed, not hidden.
Season totals match CREAMS's published figures (Punjab: 71,304 in 2021, 36,663 in 2023, 10,909 in 2024, 5,114 in 2025).

**Limits.** CREAMS uses its own protocol (VIIRS-based, counting from 15 September; from 1 October before 2020), so its
numbers are lower than raw FIRMS. Some bulletins re-publish revised figures; the latest is used.

---

## 3. Burned area

### 3a. NASA MODIS MCD64A1 (used)

**What it is.** MCD64A1 collection 6.1 maps, for every 500 m pixel and month, the day of the year it burned, using
the drop in surface reflectance plus active-fire evidence. Unlike fire counts it does not depend on whether a
satellite passed while the fire was burning: it maps the scar.

**How it was taken and processed** (`scripts/burnscar.py`). The monthly `Burn_Date` cloud-optimised GeoTIFFs for
September to December are read from Microsoft Planetary Computer (keyless), warped to UTM 43N, and the first burn
between 15 Sep and 5 Dec is kept. District and state hectares come from rasterised district polygons; each pixel is
21.5 ha. Tiles coloured by burn date are cut for the map.

**Checked by.** District sums agree with state sums to within 0.02%. In the peak years 53% (2021) to 59% (2016) of
VIIRS S-NPP detections, and 57-61% of MODIS detections, fall on a mapped burned pixel; in 2024 only 22% of VIIRS
detections did, as burning thinned into scattered small fields the 500 m product misses. Punjab's 2016 total (1.89 million ha, 65% of the rice area) is in the range
of published estimates.

**Limits.** At 500 m, single 1-2 ha fields and short burns are often missed, so the hectares are a lower bound.
Planetary Computer is missing September 2023 and September-October 2025: those seasons are flagged incomplete and
left out of ratios. NASA publishes each month about two months later.

### 3b. A Sentinel-2 field-scale classifier (tested and rejected)

The goal was field-scale burn scars from Sentinel-2 (20 m bands, 2-5 day revisit). The pipeline was built: every
clear pass from September to December, cloud and shadow masks buffered by 120 m, a burn date per pixel.
The rule tested was the textbook one: fresh ash is dark in the near infrared (B8A < 0.19) and has a negative
normalised burn ratio (NBR = (B8A - B12) / (B8A + B12) < -0.05), confirmed by a second pass within 12 days.

![Sentinel-2 false colour (B12, B8A, B4), central Punjab, 8 Nov 2021, with VIIRS detections](images/methods/sentinel2_falsecolour_2021.png)

*Central Punjab, 8 November 2021, in short-wave infrared false colour. Dark maroon fields are harvested paddy, burned
or not; yellow circles are VIIRS fire detections from the previous ten days.*

It failed validation, so it was not published:
- At VIIRS fire locations, "ash-like" pixels were only 1.5 to 2 times as common as in random harvested fields.
- On the same tile and calendar window, the full-season run rated **2025** (a year with about a tenth of the fires)
  at **77%** of paddy burned and **2021** (a peak year) at **1.2%**: the reverse of reality.

![Time series of the pixels the rule called burned](images/methods/sentinel2_rejected_timeseries.png)

*Freshly cut, damp paddy stubble is as dark in the near infrared and as low in NBR as ash, and stays that way for
weeks. A spectral threshold cannot separate burned from merely harvested fields in this landscape; doing so needs a
classifier trained on fields known to have burned (for example from high-resolution imagery or field surveys).*

---

## 4. Smoke (NASA GMAO GEOS-FP)

**What it is.** GEOS-FP is NASA's operational atmospheric model. It assimilates satellite aerosol observations and
uses fire emissions (QFED) to simulate aerosol optical depth (AOD) by species. Organic carbon, black carbon and brown
carbon together are the smoke-dominated part, the NASA counterpart of the Copernicus CAMS "organic matter AOD" field.

**How it was taken** (`scripts/fetch_smoke.py`). From the NCCS OPeNDAP server (keyless): `tavg3_2d_aer_Nx` 3-hourly
OC, BC, BrC and total extinction AOD at 550 nm, and `tavg1_2d_slv_Nx` 10 m wind, over 64-94 E, 18-38 N at
0.3125° × 0.25°, from 15 September through the forecast. Each "seamless" run is analysis followed by forecast; the
recent run with the longest forecast (00Z, about 10 days) is used.

**How it was processed.** The server sometimes truncates a response, returns a chunk of zeros, or pads the forecast
tail with 1e15 fill values. Short chunks are retried and never silently joined; empty frames are filled from a
neighbouring run; fill values are dropped; nothing is interpolated except where flagged. Frames are packed into PNG
atlases (AOD stored as √(AOD/2) for precision at low values); the browser upsamples them with a bicubic B-spline,
interpolates between 3-hourly frames and re-projects to Web Mercator on the GPU.

![One raw GEOS-FP smoke frame before smoothing](images/methods/geosfp_raw_frame.png)

**Limits.** OC includes non-fire sources (vehicles, cooking, industry), so the field is "carbonaceous aerosol",
dominated but not defined by stubble smoke. The model's surface PM2.5 is biased low against ground monitors.
GEOS-FP keeps its seamless archive back to February 2026 only.

---

## 5. Air quality and the stubble share

**CPCB ground PM2.5** (`scripts/build_aq_ground.py`). The Central Pollution Control Board's continuous stations,
read through the OpenAQ v3 API. One physical station often has several OpenAQ ids over the years; these are merged by
site. A day is kept only if it has at least 75% of the sensor's usual record count spanning 17+ hours; values ≤ 0 or
> 1,500 µg/m³ are dropped. The city value is the mean of stations reporting that day (Delhi grew from 6 stations in
2016 to about 40). OpenAQ holds no CPCB data from late 2022 to February 2025.

**Model PM2.5 and weather** (`scripts/build_db_extras.py`). Open-Meteo's CAMS air-quality archive (from August 2022)
and ERA5 reanalysis (2003 onward) for seven receptor cities: daily wind, direction, temperature, rain and the
boundary-layer height at 14:00 IST, which sets how much smoke stays near the ground.

**Stubble share of Delhi PM2.5.** Two IITM Pune products publish how much of Delhi's PM2.5 their models attribute to
stubble burning. The Decision Support System's daily values (2023 onward) are read from the chart data embedded in the
live page and in 42 Wayback Machine captures; SAFAR's bulletin figures (2019-2022) are transcribed from archived
homepage captures, each with its verbatim quote and the capture URL. They are different models and are not joined.

---

## 6. Paddy area

District kharif rice area and production (1997-2025) from the Directorate of Economics and Statistics / UPAg, as
compiled in the [Agri at Source](https://agri.reclaimchennai.city/) catalogue (`scripts/build_db_extras.py`).
All 267 district names were matched to the district map: by name (240), by LGD code history (6), by known renames
such as Ferozepur → Firozpur (13), and new districts carved out after 2021 counted with their parent (8). Year keys
are the agricultural year's start, so kharif rice of year *Y* is the straw burned in season *Y*.

---

## 7. Enforcement and residue management

`scripts/build_enforcement.py` assembles, from Punjab Pollution Control Board action-taken reports to CAQM, Haryana's
enforcement dashboard, CAQM directions and press releases, NGT affidavits and Lok Sabha / Rajya Sabha answers (some
through the data.gov.in API):
- fire events, inspections, burning confirmed, environmental compensation (cases, imposed, recovered), FIRs, red
  entries in land records, action against officials;
- CRM scheme machines, custom hiring centres and funds, residue used in and out of the field;
- a dated timeline of court orders, CAQM directions, incentives and rule changes.

Every value records its source document, the as-of date and whether the figure is cumulative. Figures quoted from
prose are checked against the cached document text; values read off scanned tables are flagged and must add up to
the printed totals. Where several reports cover a season, the latest is marked. A 2022 compensation table whose unit
makes it ten times every other year is flagged and not plotted. Spot-checked: Punjab's 2025 season-end totals
(₹1,25,85,000 compensation, 1,963 FIRs, 2,176 red entries) match the PPCB report of 30.11.2025.

---

## 8. Boundaries and the map

Country outlines are Natural Earth 1:10m from India's point of view; states and districts are geoBoundaries (India
districts from the Local Government Directory, 2021). The map is MapLibre GL (globe, 3D terrain with AWS Terrain
Tiles, elevation tinting), D3 for the side charts and ECharts for the analysis page, with Esri basemaps loaded at
view time and not redistributed.
