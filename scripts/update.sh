#!/bin/sh
# Build everything from the original sources. Safe to re-run: every step caches its downloads.
# First run: ~4.5 GB of downloads and an hour or two. Afterwards scripts/refresh.sh (6-hourly) and
# scripts/live.sh (hourly) keep it current.
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -f .env ] || { echo "copy .env.example to .env and add a NASA FIRMS MAP_KEY first"; exit 1; }
[ -f data/geo/districts.geojson ] || { sh scripts/fetch_boundaries.sh && $PY scripts/build_geo.py; }
[ -f data/db/extras/rice_area.json ] || $PY scripts/build_db_extras.py --rice-only
# NOAA-21 is not in NASA's yearly files: back-fill it from the API for past years
for y in $(seq 2023 $(( $(date +%Y) - 1 ))); do $PY scripts/firms_fetch.py "$y" || true; done
# every MODIS burned-area season (later runs only refresh the current two)
[ -f data/burnscar/burnscar.json ] || $PY scripts/burnscar.py || true
$PY scripts/build_aq_ground.py || true
$PY scripts/build_enforcement.py || true
sh scripts/refresh.sh
echo "Done. Serve the dashboard with: python3 -m http.server 8000"
