#!/bin/sh
# Server refresh, run by fire-refresh.timer every 6 hours: new FIRMS detections, the latest GEOS-FP run,
# new CREAMS bulletins, receptor-city air quality; rebuild everything and publish to web/.
# A failing source is logged and skipped so the others still update.
cd "$(dirname "$0")/.."
exec 9>data/.pipeline.lock              # shared with live.sh: one writer at a time
flock -w 3600 9 || { echo "!! another run holds the lock; skipping"; exit 0; }
PY=.venv/bin/python
step() { echo "== $(date -u +%FT%TZ) $*"; "$@" || echo "!! failed: $*"; }
step sh scripts/firms_archive.sh            # no-op once the yearly files are present
step $PY scripts/firms_fetch.py
step $PY scripts/creams_scrape.py
step $PY scripts/build_creams_dashboard.py
step $PY scripts/build_fires.py
step $PY scripts/fetch_smoke.py
step $PY scripts/burnscar.py --season $(( $(date +%Y) - 1 )) $(date +%Y)   # MODIS burned area lags ~2 months
step $PY scripts/build_db_extras.py --aq-only
# ground PM2.5 and enforcement figures change slowly: once a day, on the 00:40 UTC run
if [ "$(date -u +%H)" = "00" ]; then
  [ -f scripts/build_aq_ground.py ] && step $PY scripts/build_aq_ground.py
  step $PY scripts/build_aq_south.py --pol-export   # Chennai + east-coast receptors; reads the pol database read-only
  step $PY scripts/build_cyclones.py
  if [ -f scripts/build_enforcement.py ]; then
    E=data/db/extras/enforcement.json
    cp "$E" "$E.prev" 2>/dev/null
    step $PY scripts/build_enforcement.py
    # never publish a thinner dataset than the last good one (a damaged cache once halved it)
    $PY -c "import json,sys; n=lambda f: len(json.load(open(f))['metrics']); sys.exit(0 if n('$E') >= n('$E.prev') else 1)" 2>/dev/null \
      || { [ -f "$E.prev" ] && mv "$E.prev" "$E" && echo "!! enforcement shrank; kept the previous file"; }
  fi
fi
step $PY scripts/transport.py winds $(date +%Y)    # GEOS-FP 850 hPa winds for this season (past seasons are cached)
step $PY scripts/transport.py run
step $PY scripts/build_db.py
[ -f data/db/extras/aq_south.json ] && step $PY scripts/build_south.py
step sh scripts/publish.sh
# Open-Meteo responses are cached under their end date; drop superseded ones (older than 3 days, not season ends)
find data/raw/extras/openmeteo -name '*.json' -mtime +3 ! -name '*-01-31.json' -delete 2>/dev/null
echo "== done $(date -u +%FT%TZ)"
