#!/bin/sh
# Hourly fast lane, run by fire-live.timer: new FIRMS passes only (they land within ~3 h of the overpass),
# rebuild the fire files and the analysis page data, publish. Smoke, CREAMS and the rest stay on refresh.sh.
# Shares a lock with refresh.sh so the two never write data/ at the same time.
cd "$(dirname "$0")/.."
exec 9>data/.pipeline.lock
flock -w 1800 9 || { echo "!! another run holds the lock; skipping"; exit 0; }
PY=.venv/bin/python
step() { echo "== $(date -u +%FT%TZ) $*"; "$@" || echo "!! failed: $*"; }
step $PY scripts/firms_fetch.py
step $PY scripts/build_fires.py
step $PY scripts/build_db.py
step sh scripts/publish.sh
echo "== done $(date -u +%FT%TZ)"
