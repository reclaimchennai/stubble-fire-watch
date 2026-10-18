# The data pipeline

Python 3.11+ and shell; GDAL command-line tools for the raster steps. Install with
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, copy `.env.example` to `.env`.

| Script | Reads | Writes | Run |
|---|---|---|---|
| `update.sh` | everything below | everything | once, to build from scratch |
| `refresh.sh` | | | every 6 h (systemd timer) |
| `live.sh` | | | every hour: new FIRMS passes only |
| `fetch_boundaries.sh`, `build_geo.py` | geoBoundaries, Natural Earth | `data/geo/` | once |
| `firms_archive.sh` | NASA FIRMS yearly country files (no key) | `data/raw/firms_country/` | refresh |
| `firms_fetch.py [year] [sensors]` | FIRMS area API (`NASA_FIRMS_MAP_KEY`) | `data/raw/firms_api/` | live, refresh |
| `creams_scrape.py`, `build_creams_dashboard.py` | ICAR-IARI CREAMS bulletin PDFs | `data/creams/` | refresh |
| `build_fires.py` | the three above | `data/fires/` (binary points per season, `summary.json`, `district_daily.json`) | live, refresh |
| `fetch_smoke.py` | NASA GMAO GEOS-FP OPeNDAP (no key) | `data/smoke/` (PNG atlases, `smoke_meta.json`) | refresh |
| `burnscar.py [--season ...]` | NASA MCD64A1 on Planetary Computer (no key) | `data/burnscar/` (XYZ tiles, `burnscar.json`) | refresh (current two seasons) |
| `s2night.py fetch\|detect\|validate` | ESA Sentinel-2A night campaign on CDSE S3 (`CDSE_S3_ACCESS_KEY` / `CDSE_S3_SECRET_KEY`), FIRMS, ESA WorldCover on Planetary Computer | `data/raw/s2night/`, `data/s2night/` (hot spots, validation) | once (two scenes, Dec 2025) |
| `build_db_extras.py [--rice-only\|--aq-only]` | Agri at Source crop statistics; Open-Meteo CAMS + ERA5 | `data/db/extras/rice_area.json`, `aq_met.json` | refresh |
| `build_aq_ground.py [aq\|stubble\|all]` | OpenAQ v3 (`OPENAQ_API_KEY`); IITM DSS, SAFAR (live and Wayback) | `aq_ground.json`, `stubble_share.json` | daily |
| `build_enforcement.py [--offline]` | PPCB, Haryana, CAQM, NGT, Parliament, data.gov.in (`DATAGOV_API_KEY`) | `enforcement.json`, `ENFORCEMENT_SOURCES.md` | daily |
| `build_db.py` | all of the above | `data/db/db.json` (the analysis page) | live, refresh |
| `export_release.py [--datasets\|--assets]` | built data | `release/datasets/`, `release/assets/` | before a release |
| `publish.sh`, `stamp.py` | | `web/` (the served copy, with content-hashed asset URLs) | live, refresh |

Every downloader is polite (about one request per second to government and academic sites), caches what it fetched
and only asks for what is new. Every parser writes what it could not read rather than guessing: see
`data/creams/creams_validation.json` and the `flags` column of the enforcement values.

How each source is processed, and why: [`../docs/METHODS.md`](../docs/METHODS.md).
