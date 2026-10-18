#!/usr/bin/env python3
"""
ICAR-IARI CREAMS crop-residue fire bulletin scraper + parser.

Sources
  * https://creams.iari.res.in/?page_id=1123  (Crop Residue Burning page, links current bulletins)
  * https://creams.iari.res.in/Creams_website_bulletin/  (open Apache directory listing that also
    holds unlinked earlier seasons: rice 2018-2026, wheat 2022/2023/2025/2026, plus special reports)
  * ?page_id=6243 / ?page_id=7587 (Creams_Report / Wheat_Report) only embed Google Looker Studio
    dashboards (iframes) -> no static data, not scraped.

Usage (run with the project venv):
  .venv/bin/python scripts/creams_scrape.py              # discover + download new + parse + build outputs
  .venv/bin/python scripts/creams_scrape.py --no-download  # parse/build from the local cache only
  .venv/bin/python scripts/creams_scrape.py --download-only
  .venv/bin/python scripts/creams_scrape.py --reparse    # ignore the per-PDF parse cache

Outputs (data/creams/): see build_outputs() and README notes in the final report.
Nothing is interpolated: every number comes from a bulletin's text layer (highlights sentences,
tables, or chart data labels). Values derived by counting event rows are flagged in `source`.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
import traceback
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "creams"
OUT = ROOT / "data" / "creams"
CACHE = RAW / "_parsed"
BASE = "https://creams.iari.res.in/"
LIST_PAGE = BASE + "?page_id=1123"
INDEX_ROOT = BASE + "Creams_website_bulletin/"
UA = "Mozilla/5.0 (compatible; farm-fire-dashboard/1.0; research use; +local)"
RATE = 1.0  # seconds between requests
PARSER_VERSION = 9

_last_req = [0.0]
_session = requests.Session()
_session.headers["User-Agent"] = UA


def polite_get(url: str, **kw) -> requests.Response:
    wait = RATE - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            r = _session.get(url, timeout=120, **kw)
            _last_req[0] = time.time()
            return r
        except requests.RequestException as e:  # retry with backoff
            _last_req[0] = time.time()
            if attempt == 3:
                raise
            print(f"  retry {attempt+1} {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


# ----------------------------------------------------------------------------- discovery
BULLETIN_RE = re.compile(
    r"(?P<no>\d+)\.(?P<crop>Rice|Wheat)ResidueFireBulletin_(?P<day>\d{1,3})\s*(?P<mon>[A-Za-z]{3})_(?P<year>\d{4})_ICAR",
    re.I,
)


def crawl_index() -> list[dict]:
    """Walk the open directory listing; return PDF entries (url, rel path, modified, size)."""
    out, stack, seen = [], [INDEX_ROOT], set()
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        rel_dir = u[len(INDEX_ROOT):]
        # only folders that hold fire bulletins / reports
        if rel_dir and not re.match(r"(Fire_bulletin_\d{4}|wheat|paddy)", rel_dir):
            continue
        r = polite_get(u)
        if r.status_code != 200:
            print(f"  index {u} -> {r.status_code}", file=sys.stderr)
            continue
        for row in re.findall(r"<tr>.*?</tr>", r.text, re.S):
            h = re.search(r'href="([^"?/][^"]*)"', row)
            if not h:
                continue
            full = urllib.parse.urljoin(u, h.group(1))
            if full.endswith("/"):
                stack.append(full)
                continue
            if not full.lower().endswith(".pdf"):
                continue
            cells = [re.sub("<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            out.append({"url": full, "rel": urllib.parse.unquote(full[len(INDEX_ROOT):]),
                        "modified": cells[2] if len(cells) > 2 else "", "size": cells[3] if len(cells) > 3 else ""})
    return out


def page_links() -> set[str]:
    r = polite_get(LIST_PAGE)
    r.raise_for_status()
    links = set()
    for h in re.findall(r'href="([^"]+\.pdf)"', r.text, re.I):
        links.add(urllib.parse.urljoin(BASE, h.strip()))
    return links


def discover() -> list[dict]:
    print("Discovering bulletins ...")
    linked = page_links()
    entries = {e["url"]: e for e in crawl_index()}
    for u in linked:  # links on the page that the index did not show
        if u not in entries and "Creams_website_bulletin/" in u:
            entries[u] = {"url": u, "rel": urllib.parse.unquote(u.split("Creams_website_bulletin/", 1)[1]),
                          "modified": "", "size": ""}
    for e in entries.values():
        e["linked_on_page"] = int(e["url"] in linked)
        e["local"] = str(RAW / e["rel"])
    man = sorted(entries.values(), key=lambda e: e["rel"])
    with open(RAW / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rel", "url", "modified", "size", "linked_on_page", "local"])
        w.writeheader()
        w.writerows(man)
    print(f"  {len(man)} PDFs in index ({len(linked)} linked on page_id=1123)")
    return man


def download(man: list[dict]) -> None:
    todo = [e for e in man if not Path(e["local"]).exists() or Path(e["local"]).stat().st_size == 0]
    print(f"Downloading {len(todo)} new PDFs ({len(man) - len(todo)} cached)")
    for i, e in enumerate(todo, 1):
        p = Path(e["local"])
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = polite_get(e["url"])
        except Exception as ex:  # noqa
            print(f"  FAIL {e['rel']}: {ex}", file=sys.stderr)
            continue
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            print(f"  FAIL {e['rel']}: HTTP {r.status_code}", file=sys.stderr)
            continue
        tmp = p.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.rename(p)
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} {e['rel']}", flush=True)


def load_manifest() -> list[dict]:
    with open(RAW / "manifest.csv") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------- normalisation
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

STATE_ALIASES = [  # (regex, canonical) — order matters (longest first)
    (r"uttar\s*pradesh\s*ncr|up\s*ncr", "NCR"),
    (r"rajasthan\s*ncr|haryana\s*ncr", "NCR"),
    (r"uttar\s*pradesh|\bu\.?\s?p\.?\b", "Uttar Pradesh"),
    (r"madhya\s*pradesh|\bm\.?\s?p\.?\b", "Madhya Pradesh"),
    (r"uttarakhand|uttrakhand|\buk\b", "Uttarakhand"),
    (r"rajasthan|rajashtan", "Rajasthan"),
    (r"nct\s*of\s*delhi|delhi", "Delhi"),
    (r"haryana", "Haryana"),
    (r"punjab", "Punjab"),
    (r"\btotal\b", "Total"),
]


def norm_state(s: str) -> str | None:
    s = s.strip().lower()
    for rx, canon in STATE_ALIASES:
        if re.fullmatch(rf"\s*(\(\w\)\s*)?({rx})\s*", s):
            return canon
    return None


def find_states(text: str) -> list[str]:
    """All state names appearing in text, in order."""
    out = []
    pat = "|".join(f"(?P<g{i}>{rx})" for i, (rx, _) in enumerate(STATE_ALIASES))
    for m in re.finditer(pat, text, re.I):
        for i, (_, canon) in enumerate(STATE_ALIASES):
            if m.group(f"g{i}"):
                out.append(canon)
                break
    return out


# District spelling normalisation: canonical title-case names. Keys are UPPER-CASE squashed
# (letters only) variants seen in bulletins; values are canonical spellings.
DISTRICT_FIX = {
    # Punjab
    "FEROZEPUR": "Firozpur", "FIROZEPUR": "Firozpur", "FIROZPUR": "Firozpur", "FEROZPUR": "Firozpur",
    "SASNAGAR": "SAS Nagar", "SAHIBZADAAJITSINGHNAGAR": "SAS Nagar", "MOHALI": "SAS Nagar", "SASNAGARMOHALI": "SAS Nagar",
    "SBSNAGAR": "SBS Nagar", "SHAHIDBHAGATSINGHNAGAR": "SBS Nagar", "NAWANSHAHR": "SBS Nagar", "NAWANSHAHAR": "SBS Nagar",
    "MUKTSAR": "Sri Muktsar Sahib", "SRIMUKTSARSAHIB": "Sri Muktsar Sahib", "SHRIMUKTSARSAHIB": "Sri Muktsar Sahib",
    "TARNTARAN": "Tarn Taran", "TARANTARAN": "Tarn Taran", "FATEHGARHSAHIB": "Fatehgarh Sahib",
    "RUPNAGAR": "Rupnagar", "ROPAR": "Rupnagar", "MALERKOTLA": "Malerkotla",
    # Haryana
    "GURGAON": "Gurugram", "GURUGRAM": "Gurugram", "MEWAT": "Nuh", "NUH": "Nuh", "CHARKHIDADRI": "Charkhi Dadri",
    "YAMUNANAGAR": "Yamunanagar", "YAMUNA NAGAR": "Yamunanagar", "MAHENDRAGARH": "Mahendragarh",
    # Uttar Pradesh
    "ALLAHABAD": "Prayagraj", "PRAYAGRAJ": "Prayagraj", "FAIZABAD": "Ayodhya", "AYODHYA": "Ayodhya",
    "GAUTAMBUDHNAGAR": "Gautam Buddha Nagar", "GAUTAMBUDDHANAGAR": "Gautam Buddha Nagar", "GAUTAMBUDDHNAGAR": "Gautam Buddha Nagar",
    "PILHIBHIT": "Pilibhit", "PILIBHIT": "Pilibhit", "GAZIPUR": "Ghazipur", "GHAZIPUR": "Ghazipur",
    "MUZAFARNAGAR": "Muzaffarnagar", "MUZAFFARNAGAR": "Muzaffarnagar", "RAIBEARELI": "Rae Bareli", "RAEBARELI": "Rae Bareli",
    "RAIBARELI": "Rae Bareli", "SHARANPUR": "Saharanpur", "SAHARANPUR": "Saharanpur", "SHAMALI": "Shamli", "SHAMLI": "Shamli",
    "BADAUN": "Budaun", "BUDAUN": "Budaun", "BARABANKI": "Barabanki", "AMBEDKARNAGAR": "Ambedkar Nagar",
    "SANTKABIRNAGAR": "Sant Kabir Nagar", "SANTRAVIDASNAGAR": "Bhadohi", "SANTRAVIDASNAGARBHADOHI": "Bhadohi", "BHADOHI": "Bhadohi",
    "SHRAWASTI": "Shravasti", "SHRAVASTI": "Shravasti", "MAHARAJGANJ": "Maharajganj", "MAHRAJGANJ": "Maharajganj",
    "KHERI": "Lakhimpur Kheri", "LAKHIMPURKHERI": "Lakhimpur Kheri", "KUSHINAGAR": "Kushinagar", "KUSHI NAGAR": "Kushinagar",
    "JYOTIBAPHULENAGAR": "Amroha", "AMROHA": "Amroha", "KANSHIRAMNAGAR": "Kasganj", "KASGANJ": "Kasganj",
    "SIDDHARTHNAGAR": "Siddharthnagar", "CHITRAKOOT": "Chitrakoot", "KANPURDEHAT": "Kanpur Dehat", "KANPURNAGAR": "Kanpur Nagar",
    "SONBHADRA": "Sonbhadra", "BULANDSHAHAR": "Bulandshahr", "BULANDSHAHR": "Bulandshahr", "BAHRAICH": "Bahraich",
    # Madhya Pradesh
    "HOSHANGABAD": "Narmadapuram", "NARMADAPURAM": "Narmadapuram", "NIMACH": "Neemuch", "NEEMUCH": "Neemuch",
    "EASTNIMAR": "Khandwa", "KHANDWA": "Khandwa", "WESTNIMAR": "Khargone", "KHARGONE": "Khargone",
    "NARSIMHAPUR": "Narsinghpur", "NARSINGHPUR": "Narsinghpur", "AGARMALWA": "Agar Malwa",
    # Rajasthan
    "GANGANAGAR": "Sri Ganganagar", "SRIGANGANAGAR": "Sri Ganganagar", "CHITTAURGARH": "Chittorgarh", "CHITTORGARH": "Chittorgarh",
    "SWAIMADHOPUR": "Sawai Madhopur", "SAWAIMADHOPUR": "Sawai Madhopur", "DHAULPUR": "Dholpur", "DHOLPUR": "Dholpur",
    "JHUNJHUNUN": "Jhunjhunu", "JHUNJHUNU": "Jhunjhunu", "JAISELMER": "Jaisalmer", "JAISALMER": "Jaisalmer",
    "RAJSMAND": "Rajsamand", "RAJSAMAND": "Rajsamand", "JALOR": "Jalore", "JALORE": "Jalore",
    "YAMUNANAGA": "Yamunanagar", "LACKNOW": "Lucknow", "VIDESHA": "Vidisha", "VIDISHA": "Vidisha",
    "NARSHIMAPUR": "Narsinghpur", "NARSHIMAPURA": "Narsinghpur", "NARSIMHAPURA": "Narsinghpur",
    "BULANDSAHAR": "Bulandshahr", "KAPURTHALA": "Kapurthala", "NAWASHAHR": "SBS Nagar", "NAWASHAHRSBSNAGAR": "SBS Nagar",
    "SASNAGARMOHALI": "SAS Nagar", "SOUTHWESTDELHI": "South West", "WESTDELHI": "West", "NORTHDELHI": "North",
    "SOUTHDELHI": "South", "EASTDELHI": "East", "NORTHEASTDELHI": "North East", "SOUTHEASTDELHI": "South East",
    "CENTRALDELHI": "Central", "SHAHDARADELHI": "Shahdara",
    # Delhi
    "SHAHADRA": "Shahdara", "SHAHDARA": "Shahdara", "NORTHWESTDELHI": "North West", "NORTHWEST": "North West",
}


def norm_district(name: str) -> str:
    raw = re.sub(r"\s+", " ", name.replace("…", "").replace("_", " ")).strip(" .*-")
    key = re.sub(r"[^A-Z]", "", raw.upper())
    if key in DISTRICT_FIX:
        return DISTRICT_FIX[key]
    t = raw.title()
    t = re.sub(r"\bSas\b", "SAS", t)
    t = re.sub(r"\bSbs\b", "SBS", t)
    return t


def parse_date(s: str, default_year: int | None = None) -> dt.date | None:
    s = s.strip().replace("Sept", "Sep").replace("  ", " ")
    m = re.match(r"(\d{1,2})\s*[-/ ]\s*([A-Za-z]{3})[A-Za-z]*\.?\s*(?:[-/ ,]\s*(\d{2,4}))?$", s)
    if m:
        y = m.group(3)
        y = int(y) + (2000 if y and len(y) == 2 else 0) if y else default_year
        mon = MONTHS.get(m.group(2).lower())
        if y and mon:
            try:
                return dt.date(y, mon, int(m.group(1)))
            except ValueError:
                return None
    return None


DATE_IN_TEXT = r"\d{1,2}\s*[-/ ]\s*[A-Za-z]{3,9}\.?\s*[-/ ,]\s*\d{2,4}"


# ----------------------------------------------------------------------------- page geometry
def page_lines(page) -> list[tuple[float, list[tuple]]]:
    """Group words into visual lines: [(y_center, [(x0, x1, text), ...sorted by x])]."""
    ws = page.get_text("words")
    ws = sorted(ws, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[list] = []
    for w in ws:
        yc = (w[1] + w[3]) / 2
        if lines and abs(lines[-1][0] - yc) < 2.6:
            lines[-1][1].append((w[0], w[2], w[4]))
        else:
            lines.append([yc, [(w[0], w[2], w[4])]])
    for ln in lines:
        ln[1].sort()
    return [(ln[0], ln[1]) for ln in lines]


def is_int(t: str) -> bool:
    return bool(re.fullmatch(r"\d{1,6}", t))


YEAR_RX = re.compile(r"^[,(]?((?:19|20)\d\d)[,)]?$")


# ----------------------------------------------------------------------------- highlights
def parse_highlights(text: str, ev_date: dt.date | None) -> dict:
    """Parse the 'Highlights' sentences. Returns {'day': {state: n}, 'cum': {state: n},
    'cum_start': date, 'total_day': n, 'total_cum': n}."""
    t = re.sub(r"\s+", " ", text)
    t = re.sub("[\uf0b7\uf076\uf0d8\uf0a7\u25cf\u25aa\u2022\uf0fc]", "•", t)
    out: dict = {"day": {}, "cum": {}}
    m = re.search(r"Satellites? detected (?:a total of )?([\d,]+) (?:residue )?burning events? in (?:the )?.{0,60}? on ("
                  + DATE_IN_TEXT + ")", t, re.I)
    if m:
        out["total_day"] = int(m.group(1).replace(",", ""))

    def split_sentences(s):
        return re.split(r"(?:•|\.\s+(?=[A-Z•])|\.\s*$)", s)

    for sent in split_sentences(t):
        sent = sent.strip()
        low = sent.lower()
        if re.search(r"events? detected on " + DATE_IN_TEXT + r"\s*(?:are|were|is|was)\b", sent, re.I) and "between" not in low:
            body = re.split(r"detected on " + DATE_IN_TEXT + r"\s*(?:are|were|is|was)\s", sent, maxsplit=1, flags=re.I)[-1]
            vals = _parse_state_list(body)
            if vals:
                out["day"] = vals
        elif low.startswith("total") and "between" in low and "distributed" in low:
            m2 = re.search(r"total\s+([\d,]+)", sent, re.I)
            if m2:
                out["total_cum"] = int(m2.group(1).replace(",", ""))
            m3 = re.search(r"between\s+(" + DATE_IN_TEXT + r")\s+(?:and|to)\s+(" + DATE_IN_TEXT + ")", sent, re.I)
            if m3:
                out["cum_start"] = parse_date(m3.group(1))
                out["cum_end"] = parse_date(m3.group(2))
            body = sent.split("distributed as", 1)[1]
            vals = _parse_state_list(body)
            if vals:
                out["cum"] = vals
    return out


def _parse_state_list(body: str) -> dict:
    body = body.strip().rstrip(".")
    # style A: "10 in Punjab, 01 in Haryana, 07 in UP ... and 03 in MP"
    pairs = re.findall(r"(\d+)\s+in\s+([A-Za-z][A-Za-z ]*?)(?=\s*(?:,|\band\b|;|$|\())", body)
    if len(pairs) >= 2:
        d = {}
        for v, st in pairs:
            ns = norm_state(st)
            if ns and ns != "Total":
                d[ns] = int(v.replace(",", ""))
        if len(d) == len(pairs):
            return d
    # style B: "5565, 2843 and 1494 in Punjab, Haryana and UP, respectively"
    m = re.match(r"(.*?)\s+in\s+(?:the\s+)?(.*)", body)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        sts = [norm_state(x) for x in re.split(r",|\band\b|&", re.sub(r"respectively.*", "", m.group(2), flags=re.I))
               if x.strip()]
        sts = [x for x in sts if x]
        if nums and len(nums) == len(sts):
            return {s: int(n.replace(",", "")) for s, n in zip(sts, nums)}
    return {}


# ----------------------------------------------------------------------------- year-column tables
def year_run(toks) -> list[int]:
    """First run of distinct consecutive-calendar year tokens on a header line (x order)."""
    yrs = []
    for tok in toks:
        m = YEAR_RX.match(tok[2])
        y = int(m.group(1)) if m else None
        if y and 2010 <= y <= 2035 and y not in yrs and (not yrs or abs(y - yrs[-1]) == 1):
            yrs.append(y)
        elif yrs:
            break
    return yrs


def is_header_line(toks) -> bool:
    words = [t[2] for t in toks]
    if any(w.lower() in ("detected", "events", "during", "between", "comparison", "number") for w in words):
        return False
    run = year_run(toks)
    if len(run) < 2:
        return False
    other_ints = sum(1 for w in words if is_int(w) and not YEAR_RX.match(w))
    return len(run) >= 3 or other_ints == 0


def _labels_with_x(toks) -> list[tuple[float, str]]:
    out = []
    if toks and len(toks[0]) > 3:
        toks = sorted(toks, key=lambda t: (round(t[3] / 6), t[0]))
    i = 0
    while i < len(toks):
        for n in (4, 3, 2, 1):
            if i + n <= len(toks):
                seg = " ".join(t[2] for t in toks[i:i + n])
                st = norm_state(seg)
                if st:
                    out.append(((toks[i][0] + toks[i + n - 1][1]) / 2, st))
                    i += n
                    break
        else:
            i += 1
    return out


def page_hrules(page) -> list[tuple[float, float, float]]:
    """Horizontal table rules (y, x0, x1) from vector drawings."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa
        return out
    for dr in drawings:
        for it in dr["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 1 and abs(a.x - b.x) > 5:
                    out.append((a.y, min(a.x, b.x), max(a.x, b.x)))
            elif it[0] == "re":
                r = it[1]
                if r.height < 2 and r.width > 5:
                    out.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
    return out


def band_lines(lines, rules):
    """Regroup words of table lines into row bands delimited by horizontal rules covering each
    word (handles wrapped names and side-by-side tables with different row heights).
    Returns a list of token lists [(x0, x1, text, y)] in reading order."""
    bands: dict = {}
    for y, toks in lines:
        for x0, x1, t in toks:
            xc = (x0 + x1) / 2
            above = [ry for ry, rx0, rx1 in rules if rx0 - 1 <= xc <= rx1 + 1 and ry < y]
            below = [ry for ry, rx0, rx1 in rules if rx0 - 1 <= xc <= rx1 + 1 and ry > y]
            if above and below:
                key = ("R", round(max(above), 0), round(min(below), 0))
            else:
                key = ("L", round(y, 1))
            bands.setdefault(key, []).append((x0, x1, t, y))
    # merge 'R' bands that share the same top rule y but come from different column extents
    bl = [list(t) for t in bands.values()]

    def strip_years(tk):
        srt = sorted(tk, key=lambda t: t[0])
        run = year_run(srt)
        if len(run) >= 3:
            ys = {str(y) for y in run}
            return [t for t in srt if t[2].strip(",()") not in ys]
        return tk

    def kind(tk):
        tk = strip_years(tk)
        if not tk:
            return "names"
        ints = [t for t in tk if is_int(t[2].rstrip("*")) or re.fullmatch(r"[-–—]+", t[2])]
        if len(ints) == len(tk):
            return "ints"
        return "names" if not ints else "mixed"

    def my(tk):
        return sum(t[3] for t in tk) / len(tk)

    def alternates(tk):
        seq = [("i" if (is_int(t[2].rstrip("*")) or re.fullmatch(r"[-–—]+", t[2])) else "n")
               for t in sorted(strip_years(tk), key=lambda t: t[0])]
        runs = [k for i, k in enumerate(seq) if i == 0 or seq[i - 1] != k]
        return runs and runs[0] == "n" and runs[-1] == "i"

    kinds = [kind(t) for t in bl]
    cands = []
    for i in range(len(bl)):
        for j in range(len(bl)):
            if i >= j and not (kinds[i] == "names" and kinds[j] == "ints"):
                pass
            if i == j:
                continue
            dy = abs(my(bl[i]) - my(bl[j]))
            if dy >= 4.5 or _x_overlap(bl[i], bl[j]):
                continue
            if kinds[i] == "names" and kinds[j] == "ints" and alternates(bl[i] + bl[j]):
                lo, hi = min(t[0] for t in bl[i]), max(t[1] for t in bl[j])
                nx1, ix0 = max(t[1] for t in bl[i]), min(t[0] for t in bl[j])
                # nothing else at this height may sit between the label cell and the value cells
                blocked = any(nx1 < (t[0] + t[1]) / 2 < ix0 for k, b in enumerate(bl)
                              if b and k not in (i, j) and abs(my(b) - my(bl[i])) < 6 for t in b)
                if not blocked:
                    cands.append((max(0.0, ix0 - nx1), dy, i, j))
            elif kinds[i] == kinds[j] == "names" and i < j and _labels_with_x(bl[i]) and _labels_with_x(bl[j]):
                cands.append((0.0, dy, i, j))  # '(a) Punjab' / '(b) Haryana' captions on one visual line
    used = set()
    for gap, dy, i, j in sorted(cands):
        if i in used or j in used:
            continue
        bl[i].extend(bl[j])
        used.update((i, j))
        bl[j] = None
    out = [t for t in bl if t]
    out.sort(key=my)
    for toks in out:
        toks.sort(key=lambda t: t[0])
    return out


def _x_overlap(a, b) -> bool:
    for t in a:
        for u in b:
            if t[0] < u[1] - 1 and u[0] < t[1] - 1:
                return True
    return False


def seq_groups(toks, skip_years: list[int] | None = None, strip_dates: bool = True):
    """Split a line into [(name, [ints], x_left, x_right)] groups: name words followed by ints."""
    groups = []
    name, nums, xl, xr = [], [], None, []
    toks = list(toks)
    # drop a header year run that shares the line with data (staggered multi-column layouts)
    if skip_years:
        k = len(skip_years)
        for i in range(len(toks) - k + 1):
            if [t[2].strip(",()") for t in toks[i:i + k]] == [str(y) for y in skip_years]:
                toks = toks[:i] + toks[i + k:]
                break
    for tok in toks:
        x0, x1, t = tok[0], tok[1], tok[2]
        if re.fullmatch(r"\d+\*+", t):
            t = t.rstrip("*")  # footnote markers like 0* / 0** (value kept as printed)
        if is_int(t):
            nums.append(int(t))
            xr.append(x1)
            if xl is None:
                xl = x0
        elif re.fullmatch(r"[-–—]+|NA|N/A", t):
            nums.append(None)
            xr.append(x1)
        else:
            if nums:
                groups.append((_join_name(name), nums, xl, xr))
                name, nums, xl, xr = [], [], None, []
            if re.fullmatch(r"(year|years|districts?|date|s\.?no\.?|to|–|-|sep|sept|oct|nov|dec|apr|april|may|"
                            r"september|october|november|\(period:?)", t.strip(",()"), re.I) and strip_dates \
                    or strip_dates and re.search(r"\d", t):
                continue
            name.append(tok)
            if xl is None:
                xl = x0
    if nums or name:
        groups.append((_join_name(name), nums, xl, xr))
    return groups


def _join_name(toks) -> str:
    """Join name tokens in reading order (wrapped cells: by line, then x)."""
    if not toks:
        return ""
    if len(toks[0]) > 3:
        toks = sorted(toks, key=lambda t: (round(t[3] / 4), t[0]))
    return " ".join(t[2] for t in toks)


# ----------------------------------------------------------------------------- events tables
EV_FLOAT = re.compile(r"^-?\d{1,3}\.\d{2,6}$")
EV_TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def parse_event_line(toks):
    """Return event dict if the line is an event row, else None."""
    if len(toks) < 6 or not is_int(toks[0][2]):
        return None
    texts = [t for _, _, t in toks]
    fl = [i for i, t in enumerate(texts) if EV_FLOAT.match(t)]
    if not fl:
        return None
    if len(fl) >= 2 and fl[1] == fl[0] + 1:
        a, b = float(texts[fl[0]]), float(texts[fl[1]])
        lat, lon = (a, b) if a < b else (b, a)
        if not (5 < lat < 40 and 60 < lon < 100):
            return None
    else:  # some 2018 tables print only one coordinate (longitude)
        a = float(texts[fl[0]])
        if not (60 < a < 100):
            return None
        lat, lon = None, a
        fl = [fl[0], fl[0]]
    sat_i = fl[0] - 1
    if sat_i < 2:
        return None
    sat = texts[sat_i]
    mid = toks[1:sat_i]
    # split district / tehsil by large horizontal gaps
    cells, cur = [], [mid[0]]
    for prev, w in zip(mid, mid[1:]):
        if w[0] - prev[1] > 9:
            cells.append(cur)
            cur = [w]
        else:
            cur.append(w)
    cells.append(cur)
    rest = texts[fl[1] + 1:]
    ev = {"sno": int(texts[0]), "lat": lat, "lon": lon, "satellite": sat,
          "_cells": [(c[0][0], c[-1][1], " ".join(x[2] for x in c)) for c in cells]}
    for t in rest:
        if EV_TIME.match(t) and "time" not in ev:
            ev["time"] = t
        elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", t):
            ev["acq_date"] = t
        elif t in ("D", "N"):
            ev["daynight"] = t
        elif re.fullmatch(r"-?\d+(\.\d+)?", t) and "time" in ev:
            ev["frp"] = float(t)
    return ev


# ----------------------------------------------------------------------------- per-PDF parse
def parse_pdf(path: Path) -> dict:
    import pymupdf
    doc = pymupdf.open(path)
    res: dict = {"file": path.name, "pages": doc.page_count, "warnings": []}
    texts = [doc[i].get_text() for i in range(doc.page_count)]
    res["text_chars"] = sum(len(t) for t in texts)
    if res["text_chars"] < 200:
        res["error"] = "no text layer (scanned/image-only)"
        return res
    head = re.sub(r"\s+", " ", " ".join(texts[:3]))
    m = re.search(r"Events?\s*Date\s*:?\s*(" + DATE_IN_TEXT + ")", head, re.I)
    ev_date = parse_date(m.group(1)) if m else None
    if not ev_date:
        m = re.search(r"Highlights\s+for\s+(" + DATE_IN_TEXT + ")", head, re.I)
        ev_date = parse_date(m.group(1)) if m else None
    m = re.search(r"Bulletin\s*No\.?\s*:?\s*(\d+)", head, re.I)
    res["bulletin_no"] = int(m.group(1)) if m else None
    res["season"] = "wheat" if re.search(r"wheat", head, re.I) else "rice"
    res["events_date"] = ev_date.isoformat() if ev_date else None
    # highlights: page containing "Highlights for" (not the cover)
    hl_text = ""
    for t in texts[:4]:
        if re.search(r"highlights\s+for", t, re.I) and re.search(r"satellites? detected", t, re.I):
            hl_text = t
            break
    if not hl_text:
        hl_text = " ".join(texts[:3])
    res["highlights"] = parse_highlights(hl_text, ev_date)
    for k in ("cum_start", "cum_end"):
        if isinstance(res["highlights"].get(k), dt.date):
            res["highlights"][k] = res["highlights"][k].isoformat()

    datewise, district, events = [], [], []
    st_ = {"section": None, "years": [], "labels": [], "ev_state": None}

    def flush(seg, rules, pi):
        if seg and st_["section"] in ("datewise", "district"):
            for band in band_lines(seg, rules):
                table_band(band, pi)
        seg.clear()

    def table_band(toks, pi):
        """toks: [(x0,x1,text,y)] of one table row band."""
        section = st_["section"]
        text = " ".join(t[2] for t in toks)
        has_int = any(is_int(t[2]) for t in toks)
        labs = _labels_with_x(toks)
        hdr = is_header_line([t[:3] for t in toks])
        non_year_ints = sum(1 for t in toks if is_int(t[2]) and not YEAR_RX.match(t[2]))
        if labs and all(l[1] == "Total" for l in labs):
            labs = []
        if labs and non_year_ints == 0 and not re.search(r"detected|number of|events", text, re.I):
            if st_.get("prev_label") and not hdr:
                st_["labels"] = st_["labels"] + labs
            else:
                st_["labels"] = labs
            st_["prev_label"] = not hdr
            if not hdr:
                return
        st_["prev_label"] = False
        if re.search(r"\bdate\b", text, re.I) and not has_int:
            st_["mode"] = "datewise"
        elif re.search(r"\bdistricts?\b", text, re.I) and not re.search(r"district-?\s?wise", text, re.I):
            st_["mode"] = "district"
        section = st_.get("mode") or section
        if hdr:
            yr = year_run([t[:3] for t in toks])
            ty = st_.get("title_years") or []
            if len(yr) >= 2 and (not ty or len(yr) == len(ty) or not set(yr) <= set(ty)):
                st_["years"] = yr
            elif ty and set(yr) <= set(ty):
                st_["years"] = list(ty)  # header tokens broken (e.g. '202' '4'): fall back to title order
            if section == "datewise":
                return
        years, labels = st_["years"], st_["labels"]
        if not years:
            return
        k = len(years)
        grps = seq_groups(toks, skip_years=years, strip_dates=(section != "datewise"))
        if section == "datewise":
            if not grps:
                return
            name0 = grps[0][0]
            is_total = name0.lower().startswith("total")
            d = None if is_total else parse_date(name0, 2000)
            if not (is_total or d):
                return
            nums = [v for g in grps for v in g[1]]
            states = [s_ for _, s_ in sorted(labels)]
            if not states or len(nums) != k * len(states):
                res["warnings"].append(f"p{pi+1}: datewise row '{name0}' has {len(nums)} values, expected {k}x{len(states)}")
                return
            for si, stt in enumerate(states):
                for yi, y in enumerate(years):
                    v = nums[si * k + yi]
                    if v is None or stt in ("Total", None):
                        continue
                    datewise.append({"state": stt, "row": "total" if is_total else d.strftime("%m-%d"),
                                     "year": y, "value": v, "page": pi + 1})
        else:
            for name, nums, xl, xr in grps:
                if not name or not nums:
                    continue
                if not re.search(r"[A-Za-z]{2}", name) or re.fullmatch(r"(districts?|date|s\.? ?no\.?)", name, re.I):
                    continue
                MON = r"(sep|oct|nov|dec|apr|may|jun|sept|april|september|october|november)"
                if re.match(r"^\d{1,2}\s?-?\s?[A-Za-z]{3}", name) or re.search(r"district|period|year", name, re.I) \
                        or re.fullmatch(rf"[\d\s\-–]*{MON}[a-z]*([\s\-–\d]*{MON}[a-z]*)?[\s\-–\d]*", name, re.I):
                    continue  # period header like '15 Sep-24 Oct'
                xs = list(xr)
                if len(nums) == k + len(years) and nums[k:] == years:
                    nums, xs = nums[:k], xs[:k]  # a neighbouring table's header years on the same line
                elif len(nums) == k + len(years) and nums[:len(years)] == years:
                    nums, xs = nums[len(years):], xs[len(years):]
                if len(nums) != k:
                    res["warnings"].append(f"p{pi+1}: district '{name}' has {len(nums)} values, expected {k}")
                    continue
                if not labels:
                    res["warnings"].append(f"p{pi+1}: district '{name}' without state label")
                    continue
                cx = (xl + xs[-1]) / 2
                stt = min(labels, key=lambda l: abs(l[0] - cx))[1] if len(labels) > 1 else labels[0][1]
                if name.lower().startswith("total"):
                    name = "TOTAL"
                for y, v in zip(years, nums):
                    if v is not None:
                        district.append({"state": stt, "district": name, "year": y, "value": v, "page": pi + 1})

    for pi in range(doc.page_count):
        page = doc[pi]
        lines = page_lines(page)
        rules = None
        seg = []
        page_rows = []  # events on this page for orphan attachment
        orphans = []
        for li, line in enumerate(lines):
            toks = line[1]
            text = " ".join(t for _, _, t in toks)
            low = text.lower()
            if re.search(r"division of agricultural physics|creams lab|indian agricultural research institute", low):
                continue  # running page header
            # ---- section switches
            new_sec = "keep"
            if re.search(r"state-?\s?wise.{0,25}date-?\s?wise", low):
                new_sec = "datewise"
            elif re.search(r"district-?\s?wise", low) and "detected in" in low and re.search(r"(19|20)\d\d", text):
                new_sec = "district"
            elif re.search(r"district-?\s?wise", low):
                new_sec = None  # chart pages (no table)
            elif re.search(r"study used (data|images)|satellite name|map of cum+ulative|satellite image", low):
                new_sec = None
            m = re.search(r"details of .*?events? in (.+?) on (" + DATE_IN_TEXT + ")", text, re.I)
            if m:
                new_sec = "events"
            if new_sec != "keep":
                flush(seg, rules, pi)
                st_["section"] = new_sec
                if new_sec in ("datewise", "district"):
                    ttl = text
                    if li + 1 < len(lines):
                        nxt = " ".join(t for _, _, t in lines[li + 1][1])
                        if re.match(r"^\s*((19|20)\d\d|\(period)", nxt, re.I):
                            ttl += " " + nxt.split("(")[0] if not nxt.lower().startswith("(period") else ""
                    ty = [int(y) for y in re.findall(r"(?<!\d)(20[12]\d)(?!\d)", ttl.split("(Period")[0])]
                    st_["title_years"] = list(dict.fromkeys(ty))
                    st_["years"] = list(st_["title_years"])
                    st_["labels"], st_["mode"] = [], new_sec
                if m:
                    stl = find_states(m.group(1))
                    st_["ev_state"] = stl[0] if stl else None
                    hd = parse_date(m.group(2))
                    st_["ev_date"] = hd.isoformat() if hd else None
                    if st_["ev_state"] is None:
                        res["warnings"].append(f"p{pi+1}: unknown state in events heading '{m.group(1)}'")
                continue
            section = st_["section"]
            if section in ("datewise", "district"):
                if rules is None:
                    rules = page_hrules(page)
                seg.append(line)
            elif section == "events":
                ev = parse_event_line(toks)
                if ev:
                    ev["state"] = st_["ev_state"]
                    ev["heading_date"] = st_.get("ev_date")
                    ev["_y"] = line[0]
                    ev["page"] = pi + 1
                    page_rows.append(ev)
                elif toks and all(not EV_FLOAT.match(t) for _, _, t in toks) and len(toks) <= 4 \
                        and toks[0][0] > 80 and not re.search(r"district|tehsil|satellite|latitude|longitude|block|power|night|\(ist\)|w/m2|s\. ?no|acq|time", low):
                    orphans.append((line[0], toks))  # possible wrapped district/tehsil fragment
        flush(seg, rules, pi)
        # attach orphans to nearest event row
        for y, toks in orphans:
            if not page_rows:
                continue
            near = min(page_rows, key=lambda e: abs(e["_y"] - y))
            if abs(near["_y"] - y) > 13:
                continue
            near.setdefault("_orph", []).append((y, toks))
        events.extend(page_rows)
    # finalise events: district / tehsil
    for ev in events:
        cells = ev.pop("_cells")
        orph = ev.pop("_orph", [])
        ev.pop("_y", None)
        if len(cells) >= 2:
            dist_x = (cells[0][0], cells[0][1])
            district_name, tehsil = cells[0][2], " ".join(c[2] for c in cells[1:])
        else:
            dist_x = (cells[0][0], cells[0][1])
            district_name, tehsil = cells[0][2], ""
        for y, toks in sorted(orph):
            frag = " ".join(t for _, _, t in toks)
            if toks[0][0] < dist_x[1] + 5 and len(cells) >= 2:
                district_name = district_name + " " + frag
            elif len(cells) == 1 and toks[0][0] < dist_x[1] + 5:
                district_name = district_name + " " + frag
            else:
                tehsil = (tehsil + " " + frag).strip()
        ev["district_raw"] = district_name
        ev["tehsil"] = tehsil
    res["datewise"] = datewise
    res["district"] = district
    res["events"] = events
    return res


MON3 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_report(path: Path) -> dict:
    """Special/summary reports: monthly state tables and 'events detected ... in the years' sentences."""
    import pymupdf
    doc = pymupdf.open(path)
    res = {"file": path.name, "pages": doc.page_count, "kind": "report", "monthly": [], "season_totals": [],
           "warnings": []}
    text = re.sub(r"\s+", " ", " ".join(p.get_text() for p in doc))
    res["text_chars"] = len(text)
    if len(text) < 200:
        res["error"] = "no text layer (scanned/image-only)"
        return res
    # monthly tables: header 'STATE Jan 2020 Feb 2020 ... Total 2020'
    for m in re.finditer(r"STATE((?:\s+[A-Z][a-z]{2}\s+20\d\d){6,12})\s+Total\s+[\d\-]+", text):
        months = re.findall(r"([A-Z][a-z]{2})\s+(20\d\d)", m.group(1))
        cols = [f"{y}-{MON3.index(mo.lower()) + 1:02d}" for mo, y in months]
        body = text[m.end():]
        n = len(cols)
        for rm in re.finditer(r"([A-Z][A-Z&.\- ]+?)\s+((?:\d+\s+){%d}\d+)(?=\s|$)" % n, body):
            name = rm.group(1).strip()
            vals = [int(x) for x in rm.group(2).split()]
            if name in ("TOTAL",):
                st = "All India"
            else:
                st = norm_state(name) or name.title()
            if len(vals) != n + 1:
                continue
            if sum(vals[:n]) != vals[n]:
                res["warnings"].append(f"monthly row {name}: months sum {sum(vals[:n])} != total {vals[n]}")
            for c, v in zip(cols, vals[:n]):
                res["monthly"].append({"state": st, "month": c, "events": v})
            if name == "TOTAL":
                break
    # season totals: 'burning events detected in Punjab were 102379, 67079, and 59695 in the years 2016, 2017 and 2018'
    for m in re.finditer(r"burning events detected in (?:the )?([A-Za-z ]+?) were ([\d,\sand]+?) in the years ([\d,\sand]+)",
                         text):
        nums = [int(x) for x in re.findall(r"\d{3,}", m.group(2))]
        yrs = [int(x) for x in re.findall(r"20\d\d", m.group(3))]
        st = norm_state(m.group(1)) or ("Punjab+Haryana+Uttar Pradesh" if "three" in m.group(1).lower() else None)
        if st and len(nums) == len(yrs):
            for y, v in zip(yrs, nums):
                res["season_totals"].append({"state": st, "year": y, "value": v})
    m = re.search(r"between\s+(\d{1,2})-\s*([A-Za-z]{3})[a-z]*-(\d{4}) and (\d{1,2})-([A-Za-z]{3})[a-z]*-(\d{4})", text)
    if m:
        res["period"] = [f"{MON3.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}",
                         f"{MON3.index(m.group(5).lower()) + 1:02d}-{int(m.group(4)):02d}"]
    return res


def parse_cached(path: Path, reparse=False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    st = path.stat()
    rel = str(path.relative_to(RAW)) if path.is_relative_to(RAW) else str(path)
    rh = hashlib.md5(rel.encode()).hexdigest()[:10]
    key = hashlib.md5(f"{st.st_size}|{int(st.st_mtime)}|{PARSER_VERSION}".encode()).hexdigest()[:10]
    cp = CACHE / f"{rh}.{key}.json"
    if cp.exists() and not reparse:
        return json.loads(cp.read_text())
    for old in CACHE.glob(f"{rh}.*.json"):
        old.unlink()
    try:
        res = parse_pdf(path) if BULLETIN_RE.search(path.name.replace("%20", " ")) else parse_report(path)
    except Exception as e:  # noqa
        res = {"file": path.name, "error": f"exception: {e!r}", "trace": traceback.format_exc()[-800:]}
    res["rel"] = rel
    cp.write_text(json.dumps(res))
    return res


def _parse_worker(args):
    path, reparse = args
    return parse_cached(Path(path), reparse)


# ----------------------------------------------------------------------------- build outputs
STATE_ORDER = ["Punjab", "Haryana", "Uttar Pradesh", "Delhi", "Rajasthan", "Madhya Pradesh", "Uttarakhand"]


def _bulletin_record(e: dict, r: dict) -> dict:
    fn = Path(e["rel"]).name
    m = BULLETIN_RE.search(fn.replace("%20", " "))
    rec = {"rel": e["rel"], "file": fn, "url": e["url"], "modified": e.get("modified", ""),
           "linked_on_page": int(e.get("linked_on_page", 0) or 0), "kind": "bulletin" if m else "report",
           "pages": r.get("pages"), "error": r.get("error"), "bulletin_no": r.get("bulletin_no"),
           "warnings": len(r.get("warnings", []))}
    fdate = None
    if m:
        try:
            fdate = dt.date(int(m.group("year")), MONTHS[m.group("mon").lower()[:3]], int(m.group("day")))
        except (ValueError, KeyError):
            fdate = None
        rec["season"] = "rice" if m.group("crop").lower() == "rice" else "wheat"
    else:
        rec["season"] = r.get("season")
    if r.get("season") and m and r["season"] != rec["season"]:
        rec["note_season"] = f"filename says {rec['season']}, text says {r['season']}"
    rec["file_date"] = fdate.isoformat() if fdate else None
    rec["date"] = r.get("events_date") or rec["file_date"]
    if rec["file_date"] and r.get("events_date") and rec["file_date"] != r["events_date"]:
        rec["note_date"] = f"filename date {rec['file_date']} != events date {r['events_date']}"
    rec["revision"] = 1 if re.search(r"_r\d", fn) else 0
    rec["canonical_folder"] = 1 if e["rel"].startswith("Fire_bulletin_") else 0
    return rec


def _w(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def run_parse_and_build(man: list[dict], reparse=False, only=None, workers=6) -> None:
    from concurrent.futures import ProcessPoolExecutor
    ents = [e for e in man if Path(e["local"]).exists() and (not only or re.search(only, e["rel"]))]
    print(f"Parsing {len(ents)} PDFs (cache: {CACHE})")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_parse_worker, [(e["local"], reparse) for e in ents], chunksize=2))
    recs = []
    for e, r in zip(ents, results):
        rec = _bulletin_record(e, r)
        rec["_r"] = r
        recs.append(rec)

    # ---- choose one primary bulletin per (season, date)
    groups = defaultdict(list)
    for rec in recs:
        if rec["kind"] == "bulletin" and rec["date"] and not rec["error"]:
            groups[(rec["season"], rec["date"])].append(rec)
    primary = []
    for key, g in groups.items():
        g.sort(key=lambda x: (x["revision"], x["canonical_folder"], x["modified"]), reverse=True)
        g[0]["primary"] = 1
        for o in g[1:]:
            o["primary"] = 0
            o["duplicate_of"] = g[0]["rel"]
        primary.append(g[0])
    primary.sort(key=lambda x: (x["season"], x["date"]))
    dist_canon = _district_canon(primary)

    # ---- long table of every date-wise cell (provenance) + year totals
    table_long, latest_cell, totals = [], {}, {}
    for rec in primary:
        r = rec["_r"]
        bdate = rec["date"]
        rows = r.get("datewise", [])
        # as-of per (state, year): last MM-DD row in this bulletin
        asof = defaultdict(str)
        first = defaultdict(lambda: "99-99")
        for c in rows:
            if c["row"] != "total":
                asof[(c["state"], c["year"])] = max(asof[(c["state"], c["year"])], c["row"])
                first[(c["state"], c["year"])] = min(first[(c["state"], c["year"])], c["row"])
        seen = set()
        for c in rows:
            st = "Uttar Pradesh" if c["state"] == "Uttar Pradesh NCR" else c["state"]
            k = (st, c["year"], c["row"])
            if k in seen:
                continue
            seen.add(k)
            table_long.append({"bulletin_date": bdate, "season": rec["season"], "state": st,
                               "target_year": c["year"], "month_day": c["row"], "value": c["value"],
                               "bulletin_file": rec["file"]})
            if c["row"] == "total":
                totals[(rec["season"], st, asof[(c["state"], c["year"])], c["year"])] = {
                    "season": rec["season"], "state": st, "as_of_date": asof[(c["state"], c["year"])],
                    "year": c["year"], "cumulative_fires": c["value"],
                    "period_start": first[(c["state"], c["year"])], "source": "datewise_table_total",
                    "bulletin_file": rec["file"], "bulletin_date": bdate}
            else:
                d = f"{c['year']}-{c['row']}"
                prev = latest_cell.get((rec["season"], st, d))
                if prev is None or prev["bulletin_date"] <= bdate:
                    latest_cell[(rec["season"], st, d)] = {"value": c["value"], "bulletin_date": bdate,
                                                           "bulletin_file": rec["file"]}

    # ---- highlights
    hl = {}
    for rec in primary:
        h = rec["_r"].get("highlights", {})
        for st in set(h.get("day", {})) | set(h.get("cum", {})):
            hl[(rec["season"], st, rec["date"])] = {
                "day": h.get("day", {}).get(st), "cum": h.get("cum", {}).get(st),
                "cum_start": h.get("cum_start"), "file": rec["file"]}
        # highlights cumulative also feeds the year comparison when no date-wise table exists
        for st, v in h.get("cum", {}).items():
            k = (rec["season"], st, rec["date"][5:], int(rec["date"][:4]))
            if k not in totals:
                totals[k] = {"season": rec["season"], "state": st, "as_of_date": rec["date"][5:],
                             "year": int(rec["date"][:4]), "cumulative_fires": v,
                             "period_start": (h.get("cum_start") or "")[5:], "source": "highlights",
                             "bulletin_file": rec["file"], "bulletin_date": rec["date"]}

    # ---- state daily
    keys = set(latest_cell) | set(hl)
    state_rows = []
    for key in sorted(keys, key=lambda k: (k[0], k[2], STATE_ORDER.index(k[1]) if k[1] in STATE_ORDER else 99)):
        season, st, d = key
        if st not in STATE_ORDER:
            continue
        lc, h = latest_cell.get(key), hl.get(key)
        tot = totals.get((season, st, d[5:], int(d[:4])))
        row = {"date": d, "season": season, "season_year": int(d[:4]), "state": st}
        if lc:
            row.update(fires_day=lc["value"], day_source="datewise_table", bulletin_file=lc["bulletin_file"])
        elif h and h["day"] is not None:
            row.update(fires_day=h["day"], day_source="highlights", bulletin_file=h["file"])
        if h:
            row["fires_day_as_published"] = h["day"]
        if h and h["cum"] is not None:
            row.update(fires_cumulative=h["cum"], cum_start=h["cum_start"], cum_source="highlights",
                       cum_bulletin_file=h["file"])
        elif tot:
            row.update(fires_cumulative=tot["cumulative_fires"], cum_start=f"{d[:4]}-{tot['period_start']}",
                       cum_source="datewise_table_total", cum_bulletin_file=tot["bulletin_file"])
        state_rows.append(row)

    # ---- districts
    dcum, dday, dtot = {}, {}, defaultdict(dict)
    events_out = []
    unmatched = Counter()
    known = _known_districts(primary, dist_canon)
    for rec in primary:
        r = rec["_r"]
        md = rec["date"][5:]
        seen = set()
        ok = _district_reconcile(rec)
        for c in r.get("district", []):
            st = c["state"]
            if st not in STATE_ORDER:
                continue
            if ok.get((st, c["year"])) is False:
                continue  # district rows do not add up to the printed total: not trusted
            if c["district"] == "TOTAL":
                dtot[(rec["season"], st, f"{c['year']}-{md}")][rec["file"]] = c["value"]
                continue
            dn = dist_canon.get((st, _dkey(c["district"])), norm_district(c["district"]))
            d = f"{c['year']}-{md}"
            k = (rec["season"], st, dn, d)
            if (k, rec["file"]) in seen:
                continue
            seen.add((k, rec["file"]))
            prev = dcum.get(k)
            if prev is None or prev["bulletin_date"] <= rec["date"]:
                dcum[k] = {"value": c["value"], "bulletin_date": rec["date"], "file": rec["file"],
                           "raw": c["district"], "period_start": _period_start(r, rec),
                           "check": "sum_ok" if ok.get((st, c["year"])) else "no_total"}
        cnt = Counter()
        for ev in r.get("events", []):
            if ev.get("heading_date") and ev["heading_date"] != rec["date"]:
                continue  # event list belongs to another day (source error); reported in validation
            st = ev.get("state")
            dn, teh, matched = _resolve_event_district(st, ev, known, dist_canon)
            unmatched[(st, dn)] += 0 if matched else 1
            cnt[(st, dn)] += 1
            events_out.append({"date": rec["date"], "season": rec["season"], "state": st, "district": dn,
                               "district_matched": int(matched),
                               "tehsil": teh.title(), "latitude": ev["lat"], "longitude": ev["lon"],
                               "satellite": ev.get("satellite"), "acq_time": ev.get("time"),
                               "day_night": ev.get("daynight"), "frp_w_m2": ev.get("frp"),
                               "bulletin_file": rec["file"], "page": ev.get("page")})
        for (st, dn), n in cnt.items():
            dday[(rec["season"], st, dn, rec["date"])] = {"value": n, "file": rec["file"]}
    # rows whose district cell was not captured (wrapped cells) but whose tehsil is known: infer district
    teh_map = defaultdict(Counter)
    for e in events_out:
        if e["district_matched"] == 1 and e["tehsil"]:
            teh_map[(e["state"], _dkey(e["tehsil"]))][e["district"]] += 1
    for e in events_out:
        if e["district_matched"] == 0:
            cand = teh_map.get((e["state"], _dkey(e["district"])))
            if cand and len(cand) == 1:
                dn = next(iter(cand))
                k_old = (e["season"], e["state"], e["district"], e["date"])
                if k_old in dday:
                    dday[k_old]["value"] -= 1
                    if dday[k_old]["value"] == 0:
                        del dday[k_old]
                k_new = (e["season"], e["state"], dn, e["date"])
                dday.setdefault(k_new, {"value": 0, "file": e["bulletin_file"]})["value"] += 1
                e["tehsil"], e["district"], e["district_matched"] = e["district"], dn, 2
    dist_rows = []
    for key in sorted(set(dcum) | set(dday), key=lambda k: (k[0], k[3], STATE_ORDER.index(k[1]) if k[1] in STATE_ORDER else 99, k[2])):
        season, st, dn, d = key
        c, e = dcum.get(key), dday.get(key)
        dist_rows.append({"date": d, "season": season, "season_year": int(d[:4]), "state": st, "district": dn,
                          "fires_day": e["value"] if e else None,
                          "day_source": "event_list_count" if e else None,
                          "fires_cumulative": c["value"] if c else None,
                          "cum_period_start": f"{d[:4]}-{c['period_start']}" if c and c["period_start"] else None,
                          "cum_source": "district_table" if c else None,
                          "cum_check": c["check"] if c else None,
                          "bulletin_file": (e or c)["file"],
                          "cum_bulletin_file": c["file"] if c else None})

    monthly = []
    for rec in recs:
        if rec["kind"] != "report":
            continue
        r = rec["_r"]
        for mrow in r.get("monthly", []):
            monthly.append({**mrow, "report_file": rec["file"], "url": rec["url"]})
        per = r.get("period") or ["", ""]
        for t in r.get("season_totals", []):
            if t["state"] not in STATE_ORDER:
                continue
            k = (rec["season"] or "rice", t["state"], per[1], t["year"])
            if k not in totals:
                totals[k] = {"season": rec["season"] or "rice", "state": t["state"], "as_of_date": per[1],
                             "year": t["year"], "cumulative_fires": t["value"], "period_start": per[0],
                             "source": "season_summary_report", "bulletin_file": rec["file"], "bulletin_date": ""}
            elif totals[k]["cumulative_fires"] != t["value"]:
                totals[k]["note"] = f"summary report {rec['file']} says {t['value']}"
    year_rows = sorted(totals.values(), key=lambda x: (x["season"], STATE_ORDER.index(x["state"])
                                                        if x["state"] in STATE_ORDER else 99, x["as_of_date"], x["year"]))
    year_rows = [y for y in year_rows if y["state"] in STATE_ORDER]

    # ---- write CSVs
    OUT.mkdir(parents=True, exist_ok=True)
    _w(OUT / "creams_state_daily.csv", state_rows,
       ["date", "season", "season_year", "state", "fires_day", "fires_cumulative", "cum_start", "day_source",
        "cum_source", "fires_day_as_published", "bulletin_file", "cum_bulletin_file"])
    _w(OUT / "creams_state_table_long.csv", table_long,
       ["bulletin_date", "season", "state", "target_year", "month_day", "value", "bulletin_file"])
    _w(OUT / "creams_district_daily.csv", dist_rows,
       ["date", "season", "season_year", "state", "district", "fires_day", "fires_cumulative", "cum_period_start",
        "day_source", "cum_source", "cum_check", "bulletin_file", "cum_bulletin_file"])
    _w(OUT / "creams_year_compare.csv", year_rows,
       ["season", "state", "as_of_date", "year", "cumulative_fires", "period_start", "source", "bulletin_date",
        "bulletin_file", "note"])
    _w(OUT / "creams_reports_monthly.csv", monthly, ["state", "month", "events", "report_file", "url"])
    _w(OUT / "creams_events.csv", events_out,
       ["date", "season", "state", "district", "district_matched", "tehsil", "latitude", "longitude", "satellite",
        "acq_time", "day_night", "frp_w_m2", "bulletin_file", "page"])
    blist = sorted(recs, key=lambda x: (x["kind"], x.get("season") or "", x.get("date") or "", x["rel"]))
    _w(OUT / "creams_bulletins.csv", blist,
       ["date", "season", "kind", "bulletin_no", "file", "rel", "url", "linked_on_page", "pages", "primary",
        "duplicate_of", "error", "warnings", "note_date", "note_season", "modified"])

    # ---- validation
    val = validate(primary, recs, state_rows)
    um = Counter()
    for e in events_out:
        if e["district_matched"] == 0:
            um[(e["state"], e["district"])] += 1
    val["event_districts_unmatched"] = [{"state": k[0], "district": k[1], "events": n} for k, n in um.most_common()]
    val["summary"]["event_rows_district_unmatched"] = sum(um.values())
    (OUT / "creams_validation.json").write_text(json.dumps(val, indent=1, default=str))

    # ---- compact JSON for the dashboard
    js = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
          "source": "ICAR-IARI CREAMS crop residue burning bulletins (https://creams.iari.res.in/?page_id=1123)",
          "notes": ["Counts are satellite-detected residue-burning events as printed in bulletins; nothing interpolated.",
                    "states[season][state][year] = [[date, fires_day, fires_cumulative], ...] (null = not reported)",
                    "districts[season][state][district][year] = [[date, fires_day, fires_cumulative], ...]",
                    "year_compare[season][state][year] = [[MM-DD, cumulative], ...]"],
          "bulletins": [{"date": b["date"], "season": b["season"], "no": b["bulletin_no"], "url": b["url"]}
                        for b in primary],
          "reports": [{"file": b["file"], "url": b["url"]} for b in recs if b["kind"] == "report"],
          "states": {}, "districts": {}, "year_compare": {}, "monthly_reports": {}}
    for r in state_rows:
        js["states"].setdefault(r["season"], {}).setdefault(r["state"], {}).setdefault(str(r["season_year"]), []).append(
            [r["date"], r.get("fires_day"), r.get("fires_cumulative")])
    for r in dist_rows:
        js["districts"].setdefault(r["season"], {}).setdefault(r["state"], {}).setdefault(r["district"], {}).setdefault(
            str(r["season_year"]), []).append([r["date"], r.get("fires_day"), r.get("fires_cumulative")])
    for r in year_rows:
        js["year_compare"].setdefault(r["season"], {}).setdefault(r["state"], {}).setdefault(str(r["year"]), []).append(
            [r["as_of_date"], r["cumulative_fires"]])
    for mrow in monthly:
        js["monthly_reports"].setdefault(mrow["state"], {})[mrow["month"]] = mrow["events"]
    (OUT / "creams.json").write_text(json.dumps(js, separators=(",", ":")))
    print(f"Wrote: state_daily={len(state_rows)} district_daily={len(dist_rows)} year_compare={len(year_rows)} "
          f"table_long={len(table_long)} events={len(events_out)} bulletins={len(recs)} (primary {len(primary)})")
    print(json.dumps(val["summary"], indent=1))


def _known_districts(primary, dist_canon) -> dict:
    """state -> {squashed key: canonical name} for districts seen in district tables."""
    known = defaultdict(dict)
    for rec in primary:
        for c in rec["_r"].get("district", []):
            if c["district"] != "TOTAL" and c["state"] in STATE_ORDER:
                k = _dkey(c["district"])
                known[c["state"]][k] = dist_canon.get((c["state"], k), norm_district(c["district"]))
    for st in known:  # canonical spellings are keys too (e.g. 'Gurugram' vs 'GURGAON')
        for k, v in list(known[st].items()):
            known[st].setdefault(_dkey(v), v)
    return known


def _resolve_event_district(st, ev, known, dist_canon):
    """Split 'district + tehsil' text of an event row using the state's known district names."""
    words = (ev["district_raw"] + " " + ev.get("tehsil", "")).split()
    words = [w for w in words if not EV_TIME.match(w)]
    kd = known.get(st, {})
    for n in range(min(5, len(words)), 0, -1):
        k = _dkey(" ".join(words[:n]))
        canon = kd.get(k) or (DISTRICT_FIX.get(k) if DISTRICT_FIX.get(k) in kd.values() else None)
        if not canon:
            k2 = _dkey(re.sub(r"\(.*?\)", "", " ".join(words[:n])))
            canon = kd.get(k2) or (DISTRICT_FIX.get(k2) if DISTRICT_FIX.get(k2) in kd.values() else None)
        if canon:
            return canon, " ".join(words[n:]), True
    raw = " ".join(w for w in ev["district_raw"].split() if not EV_TIME.match(w))
    return dist_canon.get((st, _dkey(raw)), norm_district(raw)), ev.get("tehsil", ""), False


def _district_reconcile(rec) -> dict:
    """(state, year) -> True if district rows sum to the TOTAL row / state total, False if they
    don't, missing if there is nothing to compare with."""
    r = rec["_r"]
    sums, tot, seen = defaultdict(int), {}, set()
    for c in r.get("district", []):
        if c["district"] == "TOTAL":
            tot[(c["state"], c["year"])] = c["value"]
        elif (c["state"], _dkey(c["district"]), c["year"]) not in seen:
            seen.add((c["state"], _dkey(c["district"]), c["year"]))
            sums[(c["state"], c["year"])] += c["value"]
    st_tot = {(c["state"], c["year"]): c["value"] for c in r.get("datewise", []) if c["row"] == "total"}
    yr = int(rec["date"][:4])
    for st, n in r.get("highlights", {}).get("cum", {}).items():
        st_tot.setdefault((st, yr), n)
    out = {}
    for k, s_ in sums.items():
        refs = [x for x in (tot.get(k), st_tot.get(k)) if x is not None]
        if refs:
            out[k] = s_ in refs
    return out


def _period_start(r, rec) -> str | None:
    rows = [c["row"] for c in r.get("datewise", []) if c["row"] != "total"]
    if rows:
        return min(rows)
    cs = r.get("highlights", {}).get("cum_start")
    return cs[5:] if cs else None


def _dkey(name: str) -> str:
    k = re.sub(r"[^A-Z]", "", name.replace("…", "").upper())
    k = re.sub(r"(DISTRICT|DIST)$", "", k)
    return k


def _district_canon(primary) -> dict:
    """(state, squashed-key) -> canonical display name. Wrapped/split spellings share a key."""
    variants = defaultdict(Counter)
    for rec in primary:
        r = rec["_r"]
        for c in r.get("district", []):
            st = "Uttar Pradesh" if c["state"] == "Uttar Pradesh NCR" else c["state"]
            if c["district"] != "TOTAL":
                variants[(st, _dkey(c["district"]))][c["district"]] += 1
        for ev in r.get("events", []):
            variants[(ev.get("state"), _dkey(ev["district_raw"]))][ev["district_raw"]] += 1
    out = {}
    for (st, k), cnt in variants.items():
        # prefer spellings with spaces (more readable), then most frequent
        best = max(cnt.items(), key=lambda kv: (kv[1], " " in kv[0]))[0]
        best = re.sub(r"\s*\(.*?\)\s*", " ", best).strip() if not k in DISTRICT_FIX else best
        out[(st, k)] = DISTRICT_FIX.get(k) or DISTRICT_FIX.get(_dkey(best)) or norm_district(best)
    return out


def validate(primary, recs, state_rows) -> dict:
    v = {"failed": [], "events_vs_highlights": [], "table_total_vs_highlights": [], "table_sum_vs_total": [],
         "district_sum_vs_total": [], "non_monotonic_cumulative": [], "cum_delta_vs_day": [], "no_highlights": [],
         "date_mismatch": [], "duplicates": [], "event_list_other_date": []}
    for rec in recs:
        if rec["error"]:
            v["failed"].append({"file": rec["rel"], "reason": rec["error"]})
        if rec.get("note_date"):
            v["date_mismatch"].append({"file": rec["rel"], "note": rec["note_date"]})
        if rec.get("duplicate_of"):
            v["duplicates"].append({"file": rec["rel"], "duplicate_of": rec["duplicate_of"]})
    for rec in primary:
        r = rec["_r"]
        h = r.get("highlights", {})
        if not h.get("day") and not h.get("cum"):
            v["no_highlights"].append(rec["rel"])
        other = Counter(e.get("heading_date") for e in r.get("events", [])
                        if e.get("heading_date") and e["heading_date"] != rec["date"])
        if other:
            v.setdefault("event_list_other_date", []).append({"file": rec["file"], "bulletin_date": rec["date"],
                                                             "event_list_dates": dict(other)})
        ev = Counter(e.get("state") for e in r.get("events", [])
                     if not e.get("heading_date") or e["heading_date"] == rec["date"])
        if r.get("events"):
            for st, n in h.get("day", {}).items():
                if ev.get(st, 0) != n:
                    v["events_vs_highlights"].append({"file": rec["file"], "state": st, "highlights": n,
                                                      "event_rows": ev.get(st, 0)})
        yr = int(rec["date"][:4])
        sums, tots = defaultdict(int), {}
        for c in r.get("datewise", []):
            if c["row"] == "total":
                tots[(c["state"], c["year"])] = c["value"]
            else:
                sums[(c["state"], c["year"])] += c["value"]
        for k, t in tots.items():
            if sums.get(k) != t:
                v["table_sum_vs_total"].append({"file": rec["file"], "state": k[0], "year": k[1], "sum_rows": sums.get(k),
                                                "total_row": t})
        for st, n in h.get("cum", {}).items():
            if (st, yr) in tots and tots[(st, yr)] != n:
                v["table_total_vs_highlights"].append({"file": rec["file"], "state": st, "highlights_cum": n,
                                                       "table_total": tots[(st, yr)]})
    # district sums vs TOTAL rows and vs state totals, per bulletin (raw parsed rows)
    for rec in primary:
        r = rec["_r"]
        sums, tot, seen = defaultdict(int), {}, set()
        for c in r.get("district", []):
            if c["state"] not in STATE_ORDER:
                continue
            if c["district"] == "TOTAL":
                tot[(c["state"], c["year"])] = c["value"]
            elif (c["state"], _dkey(c["district"]), c["year"]) not in seen:
                seen.add((c["state"], _dkey(c["district"]), c["year"]))
                sums[(c["state"], c["year"])] += c["value"]
        st_tot = {(c["state"], c["year"]): c["value"] for c in r.get("datewise", []) if c["row"] == "total"}
        yr = int(rec["date"][:4])
        for st, n in r.get("highlights", {}).get("cum", {}).items():
            st_tot.setdefault((st, yr), n)
        for k, s_ in sums.items():
            ref = tot.get(k, st_tot.get(k))
            if ref is not None and ref != s_:
                v["district_sum_vs_total"].append({"file": rec["file"], "state": k[0], "year": k[1],
                                                   "sum_districts": s_, "total_row": tot.get(k),
                                                   "state_total": st_tot.get(k)})
    # monotonic cumulative & daily-vs-delta within season (published highlights)
    series = defaultdict(list)
    for r in state_rows:
        if r.get("cum_source") == "highlights":
            series[(r["season"], r["season_year"], r["state"])].append(r)
    for key, rows in series.items():
        rows.sort(key=lambda x: x["date"])
        for a, b in zip(rows, rows[1:]):
            if b["fires_cumulative"] < a["fires_cumulative"]:
                v["non_monotonic_cumulative"].append({"season": key[0], "state": key[2], "date": b["date"],
                                                      "prev": a["fires_cumulative"], "cum": b["fires_cumulative"]})
            da, db = dt.date.fromisoformat(a["date"]), dt.date.fromisoformat(b["date"])
            if (db - da).days == 1 and b.get("fires_day_as_published") is not None:
                delta = b["fires_cumulative"] - a["fires_cumulative"]
                if delta != b["fires_day_as_published"]:
                    v["cum_delta_vs_day"].append({"season": key[0], "state": key[2], "date": b["date"],
                                                  "cum_delta": delta, "day": b["fires_day_as_published"]})
    v["summary"] = {k: len(x) for k, x in v.items() if isinstance(x, list)}
    v["summary"]["primary_bulletins"] = len(primary)
    v["summary"]["pdfs"] = len(recs)
    return v


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--only", help="regex on relative path to restrict parsing (debug)")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.no_download:
        man = load_manifest()
    else:
        man = discover()
        download(man)
    if a.download_only:
        return
    run_parse_and_build(man, reparse=a.reparse, only=a.only)


if __name__ == "__main__":
    main()
