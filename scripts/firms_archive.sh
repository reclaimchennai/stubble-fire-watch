#!/bin/sh
# Download public NASA FIRMS yearly country files (no key needed) for India and Pakistan.
# MODIS 2003+, VIIRS S-NPP 2012+, VIIRS NOAA-20 (jpss1) 2018+. Re-run safely: existing files are skipped.
cd "$(dirname "$0")/../data/raw/firms_country" 2>/dev/null || { mkdir -p "$(dirname "$0")/../data/raw/firms_country"; cd "$(dirname "$0")/../data/raw/firms_country"; }
LAST=$(( $(date +%Y) - 1 ))
{
  for c in India Pakistan; do
    for y in $(seq 2003 $LAST); do echo "modis/$y/modis_${y}_${c}.csv"; done
    for y in $(seq 2012 $LAST); do echo "viirs-snpp/$y/viirs-snpp_${y}_${c}.csv"; done
    for y in $(seq 2018 $LAST); do echo "viirs-jpss1/$y/viirs-jpss1_${y}_${c}.csv"; done
  done
} | xargs -P 12 -I{} sh -c 'f=$(basename {}); [ -s "$f" ] || { curl -sf --retry 3 -o "$f.part" "https://firms.modaps.eosdis.nasa.gov/data/country/{}" && mv "$f.part" "$f" || rm -f "$f.part"; }'
ls | wc -l | xargs echo "files:"
