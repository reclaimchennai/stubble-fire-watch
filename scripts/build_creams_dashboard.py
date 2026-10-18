#!/usr/bin/env python3
"""Condense data/creams/creams.json (from creams_scrape.py) into what the dashboard's CREAMS card needs.

Output data/creams/creams_dashboard.json:
  cumulative[state][year] = [["MM-DD", cumulative_events], ...]   paddy season, as printed in bulletins
  latest, latest_url, subtitle
Values come straight from the bulletins: the state daily tables where a cumulative figure is printed,
otherwise the multi-year comparison tables. Nothing is interpolated.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "creams" / "creams.json"
OUT = ROOT / "data" / "creams" / "creams_dashboard.json"
STATES = ["Punjab", "Haryana", "Uttar Pradesh", "Delhi", "Rajasthan", "Madhya Pradesh"]


def main():
    d = json.loads(SRC.read_text())
    cum = {}
    for st in STATES:
        series = {}
        for yr, rows in (d.get("year_compare", {}).get("rice", {}).get(st, {}) or {}).items():
            series.setdefault(yr, {}).update({md: v for md, v in rows if v is not None})
        for yr, rows in (d.get("states", {}).get("rice", {}).get(st, {}) or {}).items():
            for date, _day, c in rows:
                if c is not None:
                    series.setdefault(yr, {})[date[5:]] = c
        series = {y: sorted(v.items()) for y, v in series.items() if len(v) > 1}
        if series:
            cum[st] = dict(sorted(series.items()))
    rice = [b for b in d.get("bulletins", []) if b.get("season") == "rice"]
    latest = max(rice, key=lambda b: b["date"]) if rice else None
    out = {
        "cumulative": cum,
        "latest": latest["date"] if latest else None,
        "latest_url": latest["url"] if latest else None,
        "subtitle": "Cumulative paddy residue burning events in CREAMS bulletins (count from 15 Sep; 1 Oct before 2020)",
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"states {list(cum)}; years {sorted({y for s in cum.values() for y in s})}; latest {out['latest']}")


if __name__ == "__main__":
    main()
