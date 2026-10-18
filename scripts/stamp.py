#!/usr/bin/env python3
"""Append ?v=<content hash> to the stylesheet and script URLs in web/, so a redeploy is never served stale
from a browser or Cloudflare cache (same idea as covid19's tools/stamp.py)."""
import hashlib
import re
import sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "web")
ASSETS = ["styles.css", "theme.css", "app.js", "db/app.js", "db/db.css"]
h = {a: hashlib.md5((W / a).read_bytes()).hexdigest()[:8] for a in ASSETS if (W / a).exists()}


def stamp(page: Path, refs: dict):
    s = page.read_text()
    for url, asset in refs.items():
        s = re.sub(rf'(["(]){re.escape(url)}(\?v=[0-9a-f]+)?([")])', rf'\g<1>{url}?v={h[asset]}\g<3>', s)
    page.write_text(s)


stamp(W / "styles.css", {"theme.css": "theme.css"})
h["styles.css"] = hashlib.md5((W / "styles.css").read_bytes()).hexdigest()[:8]
stamp(W / "index.html", {"styles.css": "styles.css", "app.js": "app.js"})
stamp(W / "db" / "index.html", {"../theme.css": "theme.css", "db.css": "db/db.css", "app.js": "db/app.js"})
print("stamped", h)
