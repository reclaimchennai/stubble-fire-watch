# Night hot spots from ESA's Sentinel-2A night campaign, Punjab, 9 and 12 Dec 2025

Short-wave infrared hot spots at 20 m in the two Sentinel-2A night scenes that cover Punjab, with a FIRMS cross-check and a land-cover setting. 111 hot spots pass the two-band rule; none pass on the negated images. On 9 Dec they recovered 32 of 34 VIIRS night detections made three hours later (32 of 32 in the Himachal/Uttarakhand hills, 0 of 2 in the plains). They are heat sources (fires, furnaces, flares), not a crop-fire census. 100 m B12/B11 maximum rasters per scene are release assets `s2night_<scene>_b12_max_100m.tif` / `_b11_max_100m.tif`.

## Files

### `hot_spots.csv`

193 rows. Also as `hot_spots.geojson`.

| column | type | unit | meaning |
|---|---|---|---|
| `scene` | string |  | R012 = 9 Dec 2025 17:02 UTC (22:32 IST), R055 = 12 Dec 2025 17:12 UTC (22:42 IST); Sentinel-2A relative orbit |
| `date` | string | date | acquisition date (UTC; same calendar day in IST) |
| `tier` | string |  | both = bright in B12 and B11 (each >= 0.5 W m-2 sr-1 um-1 over background; the map layer); b12 = B12 >= 1.0 only, unconfirmed (includes streak artefacts) |
| `class` | string |  | industrial (FIRMS static source within 1 km, or same spot on both nights); built-up (WorldCover built-up share >= 0.25); open (everything else) |
| `lon` | float | deg | radiance-weighted cluster centre, WGS84 |
| `lat` | float | deg | radiance-weighted cluster centre, WGS84 |
| `npix` | integer | pixels | 20 m pixels in the cluster (B12 excess >= 0.15) |
| `l12_peak` | float | W m-2 sr-1 um-1 | peak B12 (2.19 um) radiance above the 1 km background median |
| `l11_peak` | float | W m-2 sr-1 um-1 | peak B11 (1.61 um) excess within one pixel of the cluster |
| `l12_sum` | float | W m-2 sr-1 um-1 x px | sum of B12 excess over the cluster |
| `saturated` | boolean |  | true if the peak B12 pixel is at or above 29 (B12 saturates near 30 at night) |
| `t_k` | float | K | single-temperature fit from the B11/B12 ratio at the peak (indicative; mixed pixels bias it) |
| `area_m2` | float | m2 | emitting area implied by t_k (indicative) |
| `frp_mw` | float | MW | radiative power implied by t_k and area_m2 (indicative, not comparable to FIRMS FRP) |
| `b04_peak` | float | W m-2 sr-1 um-1 | peak B04 (red) radiance in a 110 m box at 10 m, for clusters with a peak >= 0.5 (null otherwise) |
| `b08_peak` | float | W m-2 sr-1 um-1 | peak B08 (NIR) radiance in a 110 m box at 10 m (null as above) |
| `landcover` | string |  | ESA WorldCover 2021 v200 class at the point |
| `built_share` | float | fraction | WorldCover built-up share within 310 x 310 m |
| `district` | string |  | district (datasets/boundaries) |
| `state` | string |  | state / province |
| `country` | string |  | India or Pakistan |
| `firms_km` | float | km | distance to the nearest FIRMS detection (any sensor, scene date +/- 4 days); null if none in the scene |
| `firms_dt_h` | float | h | time of that FIRMS detection relative to Sentinel-2, if within 1 km |
| `firms_sat` | string |  | its instrument:satellite, if within 1 km |
| `static_km` | float | km | distance to the nearest FIRMS VIIRS static (type 2) detection, 2012-2025 |
| `both_nights` | boolean |  | true if a hot spot was also seen within 300 m on the other night (overlap strip only) |

### `validation.csv`

49 rows.

| column | type | unit | meaning |
|---|---|---|---|
| `scene` | string |  | R012 = 9 Dec 2025 17:02 UTC (22:32 IST), R055 = 12 Dec 2025 17:12 UTC (22:42 IST); Sentinel-2A relative orbit |
| `scene_utc` | string | UTC | Sentinel-2 acquisition start |
| `tier` | string |  | both = bright in B12 and B11 (each >= 0.5 W m-2 sr-1 um-1 over background; the map layer); b12 = B12 >= 1.0 only, unconfirmed (includes streak artefacts) |
| `comparison` | string |  | viirs_night_after (VIIRS night pass 0-6 h after), viirs_night_before (0-24 h before), same_day_daytime (0-12 h before), modis_night_same_pass (+/-3 h), viirs_night_after_hills / _plains (Himachal + Uttarakhand vs the rest), s2_with_firms_pm4d (hot spots with any FIRMS within 1 km, +/-4 days), chance_displaced_5_30km (same, after moving each hot spot 5-30 km at random, mean of 20 draws), mirror_test_negated_image (clusters passing the tier's rule on the negated image; n = negated candidates) |
| `n` | integer | count | FIRMS detections (recall rows), hot spots (s2_with_firms, chance rows) or negated candidates (mirror rows) |
| `matched` | float | count | of n, those with a counterpart within 1 km |
| `n_excl_static` | float | count | n without FIRMS type-2 static sources |
| `matched_excl_static` | float | count | matched without static sources |

## Coverage

Scenes R012 (74.94-78.53 E, 29.59-32.32 N) and R055 (72.56-76.12 E, 29.15-31.89 N), about 71,000 km2 of valid pixels each.

## Source and licence

Contains modified Copernicus Sentinel data 2025, ESA Sentinel-2 night-time campaign (CDSE collection sentinel-2-night-time-acquisitions; processing note doi:10.5281/zenodo.20286476); Copernicus data are free and open. ESA WorldCover 2021 v200 (CC BY 4.0). NASA FIRMS (open).

## Known caveats

- Two December nights after the paddy season: this says nothing about burning in October-November.
- Most FIRMS-matched hot spots on 9 Dec were forest/scrub fires in the hills. In the Punjab-Haryana plains there was no same-night FIRMS detection that Sentinel-2 confirmed, and many hot spots sit on steel mills, furnaces and industrial estates.
- `open` hot spots can still be lone factories or brick kilns in farmland; WorldCover does not map most kilns as built-up.
- Tier `b12` is not confirmed by a second band and includes dotted-streak artefacts; it is not on the map.
- Geolocation uses no ground control (ESA note): expect offsets of tens of metres.
- Night lights (VNIR) were not usable: detector striping exceeds the city signal in these products.

## How it was produced

`scripts/s2night.py fetch|detect|validate` writes `data/raw/s2night/` and `data/s2night/`; this script flattens them. The method, thresholds and checks are in docs/METHODS.md.

## Loading

Python (pandas):

```python
import pandas as pd
hs = pd.read_csv("hot_spots.csv")
hs[hs.tier == "both"].groupby(["scene", "class"]).size()
```

R:

```r
library(readr)
hs <- read_csv("hot_spots.csv")
```
