#!/bin/sh
# Download the boundary sources build_geo.py needs: geoBoundaries (India, Pakistan ADM1/ADM2) and
# Natural Earth 10m admin-0 from India's point of view.
set -e
cd "$(dirname "$0")/../"
D=data/raw/boundaries
mkdir -p "$D"
G=https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen
for c in IND PAK; do for l in ADM1 ADM2; do
  [ -s "$D/$c-$l.geojson" ] || curl -sfL -o "$D/$c-$l.geojson" "$G/$c/$l/geoBoundaries-$c-${l}_simplified.geojson"
done; done
[ -s "$D/ne_ind.zip" ] || curl -sfL -o "$D/ne_ind.zip" https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries_ind.zip
unzip -oq "$D/ne_ind.zip" -d "$D/ne_ind"
echo "boundaries in $D"
