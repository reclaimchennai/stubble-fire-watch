#!/bin/sh
# Assemble the public site in web/: the app shell, vendored libraries and the built data only.
# Never .env, data/raw/ or scripts/ — web/ is the only directory the web server can see.
set -e
cd "$(dirname "$0")/.."
W=web
mkdir -p "$W/data/creams" "$W/data/db"
cp index.html styles.css theme.css app.js favicon.svg "$W/"
rsync -a --delete --exclude 'icons/' vendor/ "$W/vendor/"
rsync -a --delete db/ "$W/db/"
rsync -a --delete south/ "$W/south/"
for d in fires smoke geo burnscar s2night; do [ -d "data/$d" ] && rsync -a --delete --exclude '.*.tmp' "data/$d/" "$W/data/$d/"; done
cp data/creams/creams_dashboard.json "$W/data/creams/"
cp data/db/db.json "$W/data/db/"
[ -f data/db/south.json ] && cp data/db/south.json "$W/data/db/"
.venv/bin/python scripts/stamp.py "$W" || python3 scripts/stamp.py "$W"
echo "published to $W/ ($(du -sh "$W" | cut -f1))"
