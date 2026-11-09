# Geostationary active fire for Punjab and Haryana: pilot

*2026-11-03. Pilot script: `scripts/geostationary_pilot.py` (outputs `data/raw/geostationary/pilot*.json`). Production
fetch: `scripts/fetch_geostationary.py`, which writes `data/geostationary/geostationary.json` for the /db page.*

## Recommendation: add it, as a separate index of the larger fires and their timing

Meteosat's 15-minute fire product (LSA SAF MSG-IODC FRP-PIXEL) sees the Punjab burning, and sees it at hours the polar
satellites miss. It passes every test the pilot set:

| Test (Punjab + Haryana, 15 Oct-15 Nov) | 2021 | 2024 | 2025 |
|---|---|---|---|
| Meteosat detections (15-min slots) | 34,715 | 17,659 | 34,199 |
| Meteosat fire cells (~4 km cell, counted once per day) | 13,245 | 7,128 | 11,874 |
| Meteosat fire energy, TJ | 1,693 | 734 | 1,940 |
| Fire cells first seen 15:00-01:00 IST | **39 %** | **94 %** | **97 %** |
| Meteosat detections within ±1 h of a VIIRS pass that sit within one cell of a VIIRS fire | 90 % | 68 % | 47 % |
| Aqua fires with a Meteosat detection within 6 km and 45 min | 52 % | 18 % | 46 % |
| Daily correlation with VIIRS (fire cells vs detections) | 0.80 | 0.73 | 0.79 |
| District correlation with VIIRS | 0.94 | 0.79 | 0.64 |
| VIIRS S-NPP + NOAA-20 detections | 158,592 | 14,046 | 9,059 |
| MODIS MCD64A1 burned area, ha | 1,746,873 | 569,254 | n/a† |
| **Placebo, 5-25 Dec (almost no burning)**: Meteosat detections | | 344 | 46 |

† No MCD64A1 for October 2025 on Planetary Computer.

Three results matter:

1. **Burning moved into the late afternoon.** In 2021, 39 % of Meteosat fire cells were first seen after 15:00 IST. In
   2024 the figure was 94 %, and in 2025 it was 97 %. The peak moved from 14:00-15:00 to 16:00-17:00, just after the
   VIIRS (13:00-14:59) and Aqua (14:00-15:59 since its drift) passes. Almost nothing is seen after 18:00: the fires are
   lit late, not after dark. Part of this shift also appears in Pakistani Punjab (see the control below), so the
   timing is the less certain of the two results.
2. **The decline in VIIRS counts overstates the decline in burning.** From 2021 to 2025, VIIRS detections fell 94 %.
   Meteosat's fire cells fell 10 %, and its fire energy rose 15 %. In 2024 both Meteosat (-46 % cells, -57 % energy) and
   MCD64A1 (-67 %) fell far less than VIIRS (-91 %). Meteosat sees only the larger fires and changed satellite in
   2022, so these numbers are not a burned-area estimate. The direction matches what the /db page already infers from
   the night-pass share and from detections per burned hectare.
3. **It is not an artefact.** Across the border in Pakistani Punjab, Meteosat and VIIRS keep a steady ratio in every
   season 2017-2025 (see below), while on the Indian side the ratio jumps more than tenfold in 2024-25.
   The December placebo, with the same box, hours and algorithm, has no afternoon peak and
   under 2 % of a season's detections. Daily and district counts track VIIRS. Where the two sensors look at the same
   time, Meteosat detections mostly sit on VIIRS fires: 90 % in 2021, when fires burned at the pass hour, and less in
   2024-25, when fewer fires burn at 13:30.

Wired in: `scripts/fetch_geostationary.py` runs in `scripts/refresh.sh`. During 1 Oct-30 Nov it downloads new 15-min
files, keeps the fire pixels for Indian Punjab, Haryana and Pakistani Punjab, and deletes the HDF5. The /db page has a
section "Watching all afternoon" showing the hour each fire cell was first seen, and VIIRS vs Meteosat indexed to the
peak season. Meteosat counts are never added to FIRMS counts.

## Every season, and a control: Pakistani Punjab

`scripts/fetch_geostationary.py` backfilled 2017-2025 (1 Oct-30 Nov; 2-52 slots a season missing from the archive).
Figures are for 15 Oct-15 Nov. VIIRS here is the /db page's S-NPP series, with industrial sources removed.

| Season | Satellite | VIIRS S-NPP, Punjab + Haryana | Meteosat fire cells | first seen after 15:00 | VIIRS S-NPP, Pakistani Punjab | Meteosat fire cells | first seen after 15:00 |
|---|---|---|---|---|---|---|---|
| 2017 | Meteosat-8 | 52,329 | 5,488 | 48 % | 3,121 | 344 | 4 % |
| 2018 | Meteosat-8 | 56,865 | 4,742 | 52 % | 7,292 | 434 | 15 % |
| 2019 | Meteosat-8 | 44,443 | 3,386 | 80 % | 6,451 | 471 | 10 % |
| 2020 | Meteosat-8 | 73,224 | 12,962 | 40 % | 7,632 | 656 | 17 % |
| 2021 | Meteosat-8 | 79,670 | 13,315 | 39 % | 15,742 | 1,299 | 32 % |
| 2022 | Meteosat-9 | 44,724 | 5,384 | 74 % | 7,926 | 645 | 48 % |
| 2023 | Meteosat-9 | 26,198 | 4,270 | 79 % | 6,349 | 381 | 58 % |
| 2024 | Meteosat-9 | 6,287 | 7,166 | 94 % | 6,597 | 483 | 52 % |
| 2025 | Meteosat-9 | 4,354 | 11,923 | 97 % | 11,048 | 941 | 38 % |

Pakistani Punjab is the control. The same satellites see it at a similar view angle. There, the ratio of Meteosat fire
cells to VIIRS detections stays at 0.06-0.11 in every season, under both Meteosat-8 and Meteosat-9. In Punjab and
Haryana the ratio is 0.08-0.18 through 2023, then 1.14 in 2024 and 2.74 in 2025. A sensor or satellite-change artefact
would move both sides of the border; this moved only the Indian side, and only after VIIRS counts collapsed.

The timing evidence is weaker. The share first seen after 15:00 also rose in Pakistani Punjab, from 4-17 % (2017-2020)
to 32-58 % (2021-2025), with a step at the 2022 satellite change. Part of the Indian rise may be regional (later
harvests, weather) or instrumental. Pakistan's after-15:00 share is still 38-58 % against 94-97 % on the Indian side,
and India already reached 80 % in 2019 under Meteosat-8.

## Candidates

| | LSA SAF MSG-IODC FRP-PIXEL | MOSDAC INSAT-3DR / 3DS fire | JAXA Himawari WLF |
|---|---|---|---|
| Satellite and position | SEVIRI on Meteosat-8 at 41.5 E (MSG1, to mid-2022), Meteosat-9 at 45.5 E (MSG2) since | INSAT imager at 74 E (3DR) / 82 E (3DS) | AHI on Himawari-9 at 140.7 E |
| Covers Punjab (~75 E)? | Yes. View zenith 37-60 deg (median ~51); pixel area 12-16 km² (median 14-15) | Yes. View zenith ~35 deg, the best geometry of the three | **No.** The product's disk is 80 E-160 W ([README](https://www.eorc.jaxa.jp/ptree/documents/README_H08_L2WLF.txt)). Punjab would sit at a view zenith of ~77 deg even if it were included |
| Resolution | 3 km at nadir | 4 km MIR/TIR at nadir | 2 km at nadir |
| Cadence | 15 min | 15-30 min (3DR and 3DS staggered) | 10 min |
| Detection floor | ~30-40 MW at nadir, higher off-nadir (PUM §1); median detection here 41-48 MW | Not published | n/a |
| Archive | 2017 onward, one HDF5 list file per 15 min (~94 KB). 2 of ~3,170 slots missing per season | Not public; ordered through MOSDAC UOPS | n/a |
| Latency | Files appear about 1 h after the slot | NRT through standing orders | n/a |
| Access | Free registration ([signup](https://mokey.lsasvcs.ipma.pt/auth/signup)); HTTPS with user and password (`LSASAF_USER`, `LSASAF_PASSWORD` in `.env`). Listings are open, files return 401 without an account | Free MOSDAC single sign-on account; HDF5/KML; download API `mdapi` | Free P-Tree FTP account |
| Licence | CC BY 4.0, cite LSA SAF | Free for non-commercial use | Free for research and non-commercial use |
| Status | "Demonstration" for IODC, still produced daily | Operational; no published validation over Punjab | n/a |

INSAT was surveyed after the pilot (see "Other satellites" below). INSAT-3DR's product is effectively blank over
Punjab. INSAT-3DS confirms the late-afternoon peak, and its KML pilot waits for a MOSDAC account.

## Method

**Region and window.** Indian Punjab + Haryana polygons (`data/geo/states.geojson`), 15 Oct-15 Nov IST. The placebo
uses 5-25 Dec.

**Meteosat.** Every 15-min `FRP-PIXEL-ListProduct_IODC-Disk` file for the window, ~3,170 per season. Each file is
read with its stored scale factors (LATITUDE, LONGITUDE, FRP, FIRE_CONFIDENCE, PIXEL_VZA, PIXEL_SIZE), and fire pixels
inside the polygons are kept. All detections are used; there is no confidence or view-angle cut. A *detection* is one
pixel in one slot, so a fire burning an hour counts four times. A *fire cell* is a 0.04 deg (~4 km) cell with at least
one detection on an IST day, counted once; its *first-seen hour* is the earliest slot that day. Fire energy is FRP ×
900 s summed over slots.

**Polar baseline.** FIRMS yearly country files (MODIS, VIIRS S-NPP, VIIRS NOAA-20), the same polygons and window, by
IST hour of acquisition. FIRMS industrial sources are not removed in the pilot, so these counts are a little higher
than the /db page's.

**Matching.** IODC vs VIIRS: for each Meteosat detection within ±1 h of a VIIRS pass, check for a VIIRS 4 km cell within
one cell. IODC vs Aqua and VIIRS vs IODC: an event of the other sensor within 6 km and 45 min.

## Polar sensors by hour

Hour of acquisition in IST:

| | 2024 detections | 14:00-01:00 IST | 15:00-01:00 IST | 2025 detections | 14:00-01:00 IST | 15:00-01:00 IST |
|---|---|---|---|---|---|---|
| MODIS (Terra + Aqua) | 2,943 | 97.6 % | 1.5 % | 3,189 | 99.3 % | **75.9 %** |
| VIIRS S-NPP | 6,328 | 19.5 % | 0 % | 4,406 | 32.9 % | 0 % |
| VIIRS NOAA-20 | 7,718 | 23.3 % | 0 % | 4,653 | 22.9 % | 0 % |

The window the brief asked for, 14:00-01:00 IST, contains polar passes. VIIRS swaths reach 14:59 IST at their edges,
and Aqua has drifted later, crossing Punjab at 14:00-14:59 IST in 2024 and mostly 15:00-15:59 in 2025. Meteosat
figures therefore use 15:00-01:00, where VIIRS has no detections. Both shares are in `pilot.json`. In 2025 the drifted
Aqua pass is itself a partial late-afternoon look, and it is what makes the Aqua cross-check above possible. It ends
when Aqua is retired.

## Caveats

- **Detection floor.** Only about 1 % of VIIRS 4 km clusters reach 40 MW. Meteosat sees the largest fires, or fires
  that grow large, so a fire first seen at 16:00 may have been lit earlier and been too small until then. That is why
  the agreement with VIIRS at 13:30 is low in 2024-25 (4-7 % of VIIRS fires have a Meteosat match) even though most
  Meteosat detections at that hour sit on VIIRS fires.
- **Two satellites.** The change from Meteosat-8 to Meteosat-9 in 2022 moved the view by 4 deg of longitude and changed
  the instrument. The view zenith over Punjab is similar (median 51 deg vs 51-52 deg), but compare the 2017-2021 and
  2022 onward seasons with care.
- **Persistent cells.** In 2025, 79 % of detections come from cells seen on 10 or more of the 32 days. A 16 km² cell
  holds hundreds of fields, so repeat burning across the season is expected, but some cells may hold industrial
  sources. The December placebo shows a small flat night-time residue (≈25 detections per hour in 2024). Industrial
  sources are not removed.
- **District mismatch in 2025.** Amritsar has 486 VIIRS detections but 81 Meteosat fire cells, while Bathinda and
  Muktsar have the reverse. This fits the view angle and pixel size growing towards the north, plus larger fields in the
  south-west, but it is unexplained.

## INSAT-3DS from the open gallery (added 2026-11-04)

MOSDAC publishes every INSAT-3DS fire slot as a map image without a login
(`mosdac.gov.in/look/3S_IMG/preview/<yyyy>/<ddMON>/3SIMG_<ddMONyyyy>_<hhmm>_L2P_FIR_V01R00.jpg`, UTC, every 30 min).
`scripts/fetch_insat3ds.py` reads the red fire markers back off these maps.

- **Georeferencing.** The map is Mercator. Parallels sit at y = 1912 − 1870.5·ln tan(45° + lat/2) (to within 2 px at
  0-40 N), and meridians are 30.54 px per degree. Fitted against Meteosat detections in the same half hour (267 slots
  from 20 Oct 2025, 15,054 marker centres), the best fit puts the 70 E column at x = 782 with a 3 px row offset. Then 88 %
  of marker centres lie within 0.1° of a Meteosat detection and 96 % within 0.2°.
- **Markers.** A marker is ~13 × 11 px (~0.35°, ~36 km). Red pixels are eroded by a 9 px disc to marker centres, then
  binned to 0.1° cells. A cell's first-seen time is the first half-hour of the IST day it holds a centre.
- **Coverage.** The 2025 season (1 Oct-30 Nov) has 2,655 images and 273 missing slots, spread evenly over the hours (about
  60 of 64 slots per hour in 15 Oct-15 Nov). **2024 is unusable:** across 24 afternoon maps in Oct-Nov 2024 there are at
  most 37 red pixels, while Meteosat saw hundreds of Punjab fires in the same hours. The product only became
  usable in 2025.

| 15 Oct-15 Nov 2025 | INSAT-3DS fire cells (0.1°) | first seen after 15:00 IST | Meteosat: after 15:00 |
|---|---|---|---|
| Punjab | 3,787 | 99 % | 97 % (Punjab + Haryana) |
| Haryana | 589 | 87 % | |
| Pakistani Punjab | 341 | 69 % | 38 % |

An independent satellite, at 82 E and seeing Punjab at ~36° from vertical rather than ~51°, puts the Indian Punjab
burning in the same 15:00-18:00 IST window, with the peak hour 16:00-17:00. INSAT-3DS is on the /db hour chart (dashed)
and is a map layer ("Fire cells, INSAT-3DS every 30 min"). Its counts are cells of marker centres, not fire pixels, and
are not added to anything else.

## Other satellites (surveyed 2026-11-03)

View zenith angles are for central Punjab (30.5 N, 75.5 E). Local solar time there is about IST − 27 min.

| Source | What it adds | Over Punjab | Access | Verdict |
|---|---|---|---|---|
| **INSAT-3DR fire** (`3RIMG_L2P_FIR`, MOSDAC) | Second geostationary view, from 74 E | View zenith 36 deg, 4 km MIR at nadir; a file every ~30 min | Catalogue search is open (`mosdac.gov.in/apios/datasets.json`); KML download needs a free MOSDAC account. Rendered maps are open at `mosdac.gov.in/look/3R_IMG/preview/<yyyy>/<ddMON>/...jpg` | **Drop.** It draws no fire over Punjab in 18 sampled afternoon slots (28 Oct, 2 and 6 Nov 2021 and 2025, 14:15-16:45 IST), while Meteosat had 40-340 detections in the same slots. Its 2021 KMLs average ~10 KB, i.e. nearly empty |
| **INSAT-3DS fire** (`3SIMG_L2P_FIR`) | Same, from 82 E, since 2024 | View zenith 36 deg; ~44 files a day. Whole-disk point counts on its gallery images track Meteosat in Punjab: 2 Nov 2025, 15:00 IST, 8 vs 33; 16:00-16:30, 77-284 vs 162-309 | KML needs the MOSDAC account; rendered maps (2002 × 2242 px, `COUNT:N POINTS` in the title) are open from Nov 2024 | **Pilot with KML** (`scripts/insat_pilot.py`, waiting for `MOSDAC_USER`/`MOSDAC_PASSWORD`). An independent satellite with better geometry already shows the same 16:00-17:00 IST peak. Markers on the maps merge into one blob over Punjab, so the maps cannot replace the KML for locations |
| **Sentinel-3A/B SLSTR FRP** (Copernicus) | ~22:00 local night pass and ~10:00 day pass, 1 km MIR | Polar; 2 satellites, so about one night pass a day | CDSE; the account is already on raw (`~/.config/cdse/.env`) | Worth a short test: would show whether fires persist after dark at a far lower detection floor than Meteosat (Meteosat sees almost nothing after 18:00) |
| **FY-3E MERSI-LL** (CMA) | The only dusk orbit: ~17:30 local, about 18:00 IST, right after the Meteosat peak | 1 km MIR (4.05 µm) | NSMC portal lists L1 only; no public fire product; registration | Ideal timing, but it needs our own fire algorithm on L1. Later |
| **FY-4B AGRI fire/hot spot** (CMA, 105 E) | Third geostationary view | View zenith 48 deg; 2 km MIR, 15 min | NSMC registration; FY-4A's equivalent was ~50 % accurate against VIIRS (2021 study) | Possible, not better geometry than Meteosat |
| GK2A AMI (128.2 E) | n/a | View zenith 66 deg | KMA NMSC key | Too oblique |
| Himawari-9 WLF (140.7 E) | n/a | Outside the product disk | n/a | Out |
| Elektro-L N2 (76 E) | Near-nadir geostationary | View zenith 36 deg | No open fire product | Out |
| VIIRS NOAA-21 (FIRMS) | Already in the FIRMS API pipeline | Same 13:30 orbit, ~50 min from NOAA-20 | `NASA_FIRMS_MAP_KEY` | No new hours |
| OroraTech, FireSat | Commercial constellations with many crossing times | n/a | Paid, or not yet released | Out for an open dashboard |

Meteosat-9 has been the prime IODC satellite since 1 June 2022. No end date for the IODC service was found, so check
EUMETSAT notices once a year. INSAT is the natural fallback if the service stops.

## Not done

- A filter for persistent industrial cells, for example cells active in the December placebo.
- INSAT-3DS KML pilot (needs a MOSDAC account; script ready) and a Sentinel-3 SLSTR night-FRP check (CDSE, already available).
- MCD64A1 for October 2025 from NASA LP DAAC instead of Planetary Computer, to complete the 2025 burned-area comparison.
