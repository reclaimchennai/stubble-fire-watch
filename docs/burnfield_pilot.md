# Burnt-field pilot: CEEW "Beyond Single Sources" with open imagery

*2026-10-29. Script: `scripts/burnfield_pilot.py`. Outputs: `data/raw/burnfield_pilot/`.*

## Recommendation: reject

Do not add this as a dashboard layer, and do not run it as a district pilot either. The method was reproduced on CEEW's own
area and dates with free imagery, and it fails every test of whether it sees burning rather than harvested fields:

| Test | What a working burnt-area rule would show | What this rule shows |
|---|---|---|
| Quiet year vs peak year, same box (15 Oct–15 Nov) | 2025 well below 2021 (VIIRS S-NPP 177 vs 3,450 detections) | 2025 **77 %** of cropland flagged, 2021 **74 %** |
| Flagged pixels near VIIRS fires vs cropland in general | clearly enriched (>1) | 1.02 (2021), 1.08 (2024), 0.98 (2025): no association |
| Flagged pixels inside MODIS MCD64A1 burned cells vs cropland in general | clearly enriched (up to ~6× possible in 2024) | 1.02 (2021), 0.88 (2024), 1.01 (2025): no association |
| Landsat gap-fill image taken the **same morning** as Sentinel-2 (no time for new fires) | ~0 "newly burnt" fields | 893 new patches per 100 km², as many as the real next-day gap-fill (945) |
| 42 flagged patches inspected on true-colour chips | mostly black fields | 14 field-shaped darkenings (33 %), the rest edges, haze, gradual or no change; the quiet year has the *highest* share |

The 2–5× gain CEEW report from adding PlanetScope is reproduced with Landsat (Phaguwala Set 1: 23 → 92), but the
same-morning placebo shows that the gain comes from two different rules on two different sensors disagreeing, not from
burns between passes. Nothing here says CEEW's PlanetScope result is wrong in the same way; it says their design cannot
tell, because harvested and tilled fields were left out of training (their §4) and the gap-fill uses a different index
from the Sentinel-2 rule. Their earlier brief (Ignatious et al. 2025, Annexure 2) already shows the symptom at state
scale: 28,120 km² burnt in Punjab in 2021 and 24,573 km² in 2024, roughly 80–90 % of the paddy area in both years, while
VIIRS fell by about 85 %.

MODIS MCD64A1 stays the burned-area layer. A field-scale layer needs a classifier trained on fields known to be burnt,
harvested-unburnt and tilled (very-high-resolution imagery or field visits), as `repo/docs/METHODS.md` §3b already
concluded.

## Method as run

**Area and dates.** CEEW's region of interest (their Fig. 1) lies in the 2–3-day Sentinel-2 strip over Sangrur/Patiala.
The pilot box is 75.95–76.27 E, 29.93–30.42 N: 1,719 km², of which 1,532 km² is cropland. It contains four of CEEW's five
villages as OSM places them: Phaguwala, Khetla, Burar, and Kakrala (two Kakralas fall inside, Samana and Nabha tahsils,
and both are reported). Mansurpur is in neither OSM nor Nominatim near the box, so it is left out. No village boundaries
are open, so each village is a 1.5 km-radius circle (7 km²). Passes from 10 Oct to 20 Nov were processed for 2021, 2024
and 2025; season figures use 15 Oct–15 Nov.

**Rule (CEEW / Ignatious et al. 2025, Annexure 1).** Burnt where BAIS2 ≥ 0.78 and TBI ≤ 0.34:
- BAIS2 = (1 − √(B6·B7·B8 / B4)) · ((B12 − B8A) / √(B12 + B8A) + 1), with B8 as the annexure writes it;
- TBI = 0.3510·B2 + 0.3813·B3 + 0.3437·B4 + 0.7196·B8 + 0.2396·B11 + 0.1949·B12.

Reflectance is L2A surface reflectance. The −1000 offset of processing baseline ≥ 04.00 is removed, which is what GEE's
"harmonized" collection does.

**Gap-fill (PlanetScope replaced).** CEEW's PlanetScope rule is 35.78 ≤ BAI ≤ 160.08 with BAI = 1/((0.1 − red)² +
(0.06 − NIR)²). PlanetScope has no SWIR or red edge, so the rule needs only red and NIR. The same rule is applied to
NASA HLS Landsat 8/9 (`hls2-l30`, 30 m, harmonised to Sentinel-2), resampled to 10 m.

**Imagery.** Microsoft Planetary Computer: `sentinel-2-l2a` at 10 m, with 20 m bands resampled bilinearly. `hls2-s30`
is used only on dates `sentinel-2-l2a` lacks, which is most of October 2021, so 2021's October passes are 30 m. Landsat
comes from `hls2-l30`. Each pass takes about 30 s to read and classify for the box.

**Masks.** Cropland is the Impact Observatory / ESRI 10 m LULC class "crops". CEEW used 2024, but Planetary Computer stops
at 2023, so 2023 is used for all years. OSM roads (7,171 ways, all classes including tracks) are burnt in with
`all_touched`, and major roads get a 10 m buffer. Clouds come from L2A SCL 8–10 or HLS Fmask cloud/adjacent bits. The
shadow class is only trusted within ~1 km of cloud, because both masks call dark char "shadow". Passes with less than
20 % clear cropland are skipped.

**Fields.** Google ALU polygons are only available through Google's gated research API, so "fields" are 8-connected
patches of ≥ 5 burnt pixels. A patch is **newly burnt** when it has fewer than 5 pixels burnt in all earlier passes of
the sequence and at least half of it was seen clear before. This is CEEW's rule with patches in place of ALU polygons.
Adjacent burnt fields merge into one patch, so patch counts run low in dense burning and the area figures are the more
robust ones.

**Checks.** VIIRS detections come from `data/fires/points_YYYY_rice.bin`. The quantisation box is read from
`data/fires/summary.json` (68.5–89 E, 21–35.5 N), which differs from the constant in `build_fires.py`. S-NPP alone is
used for year-on-year counts, because NOAA-21 only exists from 2023. MODIS MCD64A1 comes from
`data/raw/burnscar/<year>/mosaic.tif`. Planetary Computer has only November for 2025, so a 1–15 Nov window is reported as
well.

## Results

### 1. Quiet year vs peak year

![Year test](../data/raw/burnfield_pilot/figures/year_test.png)

| Box, 15 Oct–15 Nov | 2021 | 2024 | 2025 |
|---|---:|---:|---:|
| VIIRS S-NPP detections | 3,450 | 416 | 177 |
| VIIRS all satellites | 6,956 | 1,410 | 527 |
| MODIS MCD64A1 burned km² | 1,300 | 264 | 703 (Nov only) |
| Rule: cropland ever flagged (Sentinel-2) | **74 %** (1,137 km²) | 43 % (653 km²) | **77 %** (1,178 km²) |
| Rule: cropland ever flagged, passes to 9 Nov | 28 % | 37 % | 71 % |
| Rule with Landsat gap-fill: cropland ever flagged | 78 % | 91 % | 96 % |
| Rule: newly burnt patches (Sentinel-2 only) | 28,893 | 38,133 | 41,739 |
| **1–15 Nov only**: VIIRS S-NPP / MODIS km² / rule first-flagged km² | 3,184 / 1,107 / 998 | 269 / 172 / 399 | 97 / 703 / 883 |

The rule rates 2025 at least as burnt as 2021 by every measure. It also produces more newly burnt patches in 2025 than in
2021, when VIIRS S-NPP saw 19 times as many fires. In the matched window to 9 Nov, 2025 is twice 2024 and 2.5 times
2021. Two honest caveats:
- 2021's October passes are 30 m HLS, which can only lower 2021's numbers.
- MODIS also puts 1–15 Nov 2025 above 2024 in this box (703 vs 172 km²). Some of the 2024→2025 gap may therefore be
  real, late-day burning that VIIRS misses. That would not rescue the 2021 comparison or the spatial tests below.

![Share of clear cropland flagged on each pass](../data/raw/burnfield_pilot/figures/per_pass_flagged.png)

The share flagged on each pass climbs to 50–68 % by mid-November in every year, regardless of fire activity. It also
jumps between passes two days apart: 26.5 % on 2 Nov 2025, 1.8 % on 4 Nov (a hazy S2C pass that SCL did not mask), and
32 % on 6 Nov. The rule tracks harvest stage and atmosphere, not fire.

![First pass on which each pixel was flagged](../data/raw/burnfield_pilot/figures/first_burn_maps.png)

### 2. No spatial association with fires

If flagged pixels were burnt fields, they would sit near VIIRS detections and inside MODIS burned cells more often than
cropland does in general. Enrichment (share of flagged cropland within 750 m of a season VIIRS detection, divided by the
same share for all cropland) is 1.02 / 1.08 / 0.98 for 2021 / 2024 / 2025. Against MODIS burned cells it is
1.02 / 0.88 / 1.01. MODIS covers 16 % of cropland in 2024, so a rule with skill could have scored up to about 6.

### 3. Gap-filling with Landsat, and the placebo

Open imagery fills only one of CEEW's four PlanetScope days: Landsat 9 on 24 Oct (Set 1), clear over the western half of
the box (swath edge). Nothing open exists for 29 Oct, 5 Nov or 6 Nov 2024, so Sets 2 and 3 are Sentinel-2 only.

| Newly burnt, CEEW sets 2024 | Phaguwala | Khetla | Kakrala (Samana / Nabha) | Burar | Mansurpur |
|---|---|---|---|---|---|
| CEEW S2, Sets 1/2/3 | 18 / 24 / 36 | 36 / 58 / 31 | 33 / 27 / 35 | 18 / 12 / 8 | 32 / 47 / 5 |
| Pilot S2 patches, Sets 1/2/3 | 23 / 25 / 21 | 28 / 23 / 12 | 16·31 / 18·23 / 12·24 | 39 / 31 / 18 | not located |
| CEEW S2 + PlanetScope | 50 / 34 / 167 | 69 / 82 / 457 | 78 / 102 / 338 | 38 / 53 / 134 | 113 / 81 / 124 |
| Pilot S2 + Landsat (Set 1 only) | 92 | 49 | 16·99 | 53 | — |

![Village counts vs CEEW](../data/raw/burnfield_pilot/figures/villages_vs_ceew.png)

The Sentinel-2-only counts land in the same range as CEEW's, so the reproduction is faithful. Adding Landsat on 24 Oct
multiplies them 1.3–4× (whole box: 6,974 → 11,968), just as PlanetScope did for CEEW.

**Placebo.** Landsat 8 passed on 25 Oct 2024 about 20 minutes before Sentinel-2, both before the afternoon burning. Run
as a "gap-fill" after that same morning's Sentinel-2 image, it should find no new fires. It finds 13,674 newly burnt
patches (893 per 100 km² of jointly clear cropland; Phaguwala 87). The real gap-fill, 24 Oct after 23 Oct, finds 945
per 100 km² (Phaguwala 89). On 2 Nov 2024 the same-morning placebo gives 677 per 100 km². The fused gain is therefore an
artefact of switching rule and sensor.

Same-day agreement says the same. On 25 Oct 2024 the Landsat BAI rule flags 19.5 % of clear cropland and the Sentinel-2
rule 9.8 %, with an IoU of 0.26. On 2 Nov they flag 36 % and 7 % (IoU 0.12). By 10 Nov the BAI rule flags 88 % of
cropland: it marks almost any harvested field. Full numbers are in `results.json` under `cross_sensor` and
`placebo_same_day`.

### 4. Spot check: 42 patches on true-colour chips

I drew 12 random newly burnt patches (≥ 20 pixels) each from 2021 and 2025, 14 from 2024, and 6 Landsat gap-fill
detections from 24 Oct 2024; 42 fell fully inside the box. Each chip shows true colour (fixed stretch, 500 m) on the pass
before, the detection pass and the next clear pass, with the patch outlined. Chips are in
`data/raw/burnfield_pilot/chips/` (contact sheets `sheet_0*.png`), and labels and notes are in `chips.csv`.

| Label | 2021 (11) | 2024 S2 (14) | 2025 (12) | 2024 Landsat (5) |
|---|---:|---:|---:|---:|
| A: field-shaped, abrupt, uniform darkening (burn **or** fresh tillage/irrigation) | 4 | 3 | **6** | 1 |
| G: gradual or already dark, little change | 2 | 6 | 2 | 0 |
| E: edge, road, canal, village, merged across fields | 3 | 1 | 3 | 1 |
| H: haze or smoke artefact | 0 | 3 | 0 | 0 |
| F: no visible darkening, or brightening | 0 | 1 | 1 | 3 |
| U: unclear (30 m) | 2 | 0 | 0 | 0 |

![Sample chips](../data/raw/burnfield_pilot/chips/sheet_03.png)

At most 14 of 42 (33 %) look like a burnt field, and that is an upper bound. At 10 m, a freshly burnt field and a field
just tilled and watered for wheat both turn uniformly dark grey. The A fields have a median red reflectance of
0.09–0.15 (most 0.11), the same as everything else flagged (0.09–0.15 in every year). The quiet year 2025 has the most A cases (6 of
12). That is either burning VIIRS missed or tillage, and 10 m true colour cannot say which. That is exactly why the rule
cannot be validated without very-high-resolution imagery or field data.

## Deviations from CEEW

- ALU field polygons were replaced by connected patches, and villages by 1.5 km circles. This affects counts, not the
  year, placebo or enrichment tests.
- LULC 2023 instead of 2024. Bhuvan's double-crop layer (used in the 2025 brief) was not used.
- PlanetScope (3 m, 4 dates) was replaced by Landsat 8/9 (30 m, 1 of 4 dates). The placebo shows the cross-sensor step is
  the problem whatever the resolution.
- Their Sentinel-2 scenes came from GEE "harmonized"; these come from Planetary Computer with the same offset correction.
  October 2021 uses HLS S30.

## What a Punjab + Haryana run would take

For the record, should a trained classifier replace the threshold rule:

- **Area**: about 94,600 km², 55 times the pilot box. A season (15 Sep–30 Nov) has about 15–25 Sentinel-2 passes per
  point (more since S2C), plus about 10 Landsat passes.
- **Compute**: the pilot read and classified 75 passes for the box in 24 min on the laptop (I/O-bound, 10 threads,
  ~30 s per 10 m pass, ~6 s per Landsat pass). Scaled up, that is roughly 12–16 h per season on one machine, or 3–4 h
  with four parallel streams on the server. The data is keyless on Planetary Computer and needs no download quota.
- **Storage**: about 1.5 MB per pass of classification (deflate) and about 4 MB per pass of true-colour quicklook for the
  box. That is about 3 GB per season for classification only, or about 10 GB with quicklooks; the pilot used 400 MB.
- **What is actually missing**: labelled training and test fields. That means very-high-resolution imagery (≤ 1 m) on
  several dates in the season, with at least a few hundred fields each of burnt, harvested-unburnt, tilled/irrigated and
  standing crop, drawn from both a high-fire and a low-fire year. Commercial imagery is the cost driver: CEEW quote about
  INR 250/km², INR 1.2 crore for one day over Punjab. Sampling about 20 × 25 km² village blocks on four dates is about
  2,000 km², roughly INR 5 lakh per season at that rate. A rule that also passes the year, placebo and enrichment tests
  above could then be considered.

## Reproduce

```bash
.venv/bin/python scripts/burnfield_pilot.py aux                          # cropland, OSM roads, villages
.venv/bin/python scripts/burnfield_pilot.py scenes --year 2024 2021 2025  # ~25 min, Planetary Computer
.venv/bin/python scripts/burnfield_pilot.py analyse                       # results.json, figures/
.venv/bin/python scripts/burnfield_pilot.py chips                         # chips/ (labels are added by hand)
```

`scipy` and `matplotlib` were added to `.venv` for this.
