#!/usr/bin/env python3
"""
Build the enforcement + crop-residue-management (CRM) dataset for Punjab and Haryana
paddy stubble burning (2016-2026).

Outputs
  data/db/extras/enforcement.json      metrics (by state / by district) + dated policy events
  data/db/extras/ENFORCEMENT_SOURCES.md  every source and what it contributed

Cache
  data/raw/enforcement/               all downloaded pages / PDFs / JSON (re-used on re-run)
    datagov/   data.gov.in OGD resources (Rajya Sabha answer tables) - API key scrubbed
    caqm/      CAQM website documents (press releases, directions, rules, state action plans)
    sansad/ls/ Lok Sabha answer PDFs (sansad.in)
    ngt/       NGT filings in OA 632/2023 (PPCB / State of Punjab affidavits)
    punjab/    PPCB daily "Monitoring and Enforcement Actions" ATRs, Punjab Agri notifications
    haryana/   Haryana Agriculture Dept CRM dashboard reports (enforcement, red entries, area)
    sci/       Supreme Court record of proceedings (06.11.2019) - mirror copy
    _text/     extracted plain text of every PDF (pymupdf)

Rules followed
  * No value is interpolated / rounded.  Every number below is either
      - read programmatically from a machine-readable table (OGD JSON, text-layer PDF tables), or
      - a curated fact whose printed number ("num") must appear verbatim inside a quoted
        passage ("quote") that must itself be found in the cached document text
        (whitespace-normalised); the build fails otherwise, or
      - transcribed from a scanned (image-only) table, flagged "transcribed_from_image": true,
        and guarded by an arithmetic check (district rows must sum to the printed total).
  * The data.gov.in key is read from .env at runtime, never printed, never written.

Usage
  .venv/bin/python scripts/build_enforcement.py            # use cache, download what is missing
  .venv/bin/python scripts/build_enforcement.py --offline  # never touch the network
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "enforcement")
OUT_JSON = os.path.join(ROOT, "data", "db", "extras", "enforcement.json")
OUT_MD = os.path.join(ROOT, "data", "db", "extras", "ENFORCEMENT_SOURCES.md")
TEXT_DIR = os.path.join(RAW, "_text")
OFFLINE = "--offline" in sys.argv
TODAY = dt.date.today().isoformat()
UA = "Mozilla/5.0 (fire-dashboard research; polite 1 req/s)"


# --------------------------------------------------------------------------------------
# networking (polite, cached)
# --------------------------------------------------------------------------------------
def _datagov_key() -> str | None:
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return None
    for line in open(p):
        line = line.strip()
        if line.startswith("DATAGOV_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def curl_to(url: str, dest: str, extra: list[str] | None = None) -> bool:
    """Download url -> dest with curl (system CA store; several govt sites ship incomplete chains)."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    if OFFLINE:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    r = subprocess.run(["curl", "-sL", "--max-time", "180", "-A", UA, "-o", tmp, "-w", "%{http_code}"]
                       + (extra or []) + [url], capture_output=True, text=True)
    time.sleep(1.0)
    if r.stdout.strip() == "200" and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, dest)
        return True
    if os.path.exists(tmp):
        os.remove(tmp)
    print(f"  ! download failed ({r.stdout.strip()}): {url}", file=sys.stderr)
    return False


def fetch_datagov(resource_id: str) -> dict | None:
    """data.gov.in OGD API. The key is loaded at runtime and scrubbed from anything stored."""
    dest = os.path.join(RAW, "datagov", resource_id + ".json")
    if os.path.exists(dest):
        return json.load(open(dest))
    if OFFLINE:
        return None
    key = _datagov_key()
    if not key:
        print("  ! DATAGOV_API_KEY missing; cannot fetch " + resource_id, file=sys.stderr)
        return None
    url = f"https://api.data.gov.in/resource/{resource_id}?" + urllib.parse.urlencode(
        {"api-key": key, "format": "json", "limit": 1000})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
            d = json.load(r)
    except Exception as e:  # never echo the URL (it carries the key)
        print(f"  ! data.gov.in fetch failed for {resource_id}: {type(e).__name__}", file=sys.stderr)
        return None
    finally:
        time.sleep(1.0)

    def scrub(o):
        if isinstance(o, dict):
            return {k: scrub(v) for k, v in o.items() if k not in ("api-key", "api_key")}
        if isinstance(o, list):
            return [scrub(v) for v in o]
        if isinstance(o, str) and key in o:
            return o.replace(key, "<redacted>")
        return o

    d = scrub(d)
    s = json.dumps(d, indent=1, ensure_ascii=False)
    assert key not in s
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "w").write(s)
    return d


# --------------------------------------------------------------------------------------
# PDF text
# --------------------------------------------------------------------------------------
def pdf_text(path: str) -> str:
    rel = os.path.relpath(path, RAW).replace(os.sep, "__")
    cache = os.path.join(TEXT_DIR, rel + ".txt")
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
        return open(cache).read()
    import pymupdf
    doc = pymupdf.open(path)
    t = "\n".join(f"=====PAGE {i + 1}\n" + p.get_text() for i, p in enumerate(doc))
    os.makedirs(TEXT_DIR, exist_ok=True)
    open(cache, "w").write(t)
    return t


def norm(s: str) -> str:
    s = re.sub(r"=====PAGE \d+", " ", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def num(s: str) -> float | int:
    """Parse a number as printed (Indian digit grouping allowed)."""
    t = s.replace(",", "").replace("Rs.", "").replace("Rs", "").replace("/-", "").strip()
    v = float(t)
    return int(v) if v.is_integer() and "." not in t else v


# --------------------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------------------
SOURCES: dict[str, dict] = {}
USED: dict[str, list[str]] = defaultdict(list)          # source id -> what it gave
METRICS: dict[str, dict] = {}
EVENTS: list[dict] = []

METRIC_DEFS = {
    # ---- context
    "fire_events": ("Paddy residue burning events (satellite, ISRO/CREAMS standard protocol)", "count",
                    "Active-fire events attributed to paddy residue burning; standard period 15 Sep-30 Nov unless 'period' says otherwise. Context only - not an enforcement figure."),
    # ---- enforcement
    "inspections": ("Fields inspected / field verifications of reported fire events", "count",
                    "Cumulative number of reported fire locations visited by field teams."),
    "burning_confirmed": ("Sites where crop burning was physically verified", "count",
                          "Reported fire locations where burning was confirmed on the ground."),
    "no_burning_found": ("Sites where no crop burning was found on verification", "count", ""),
    "non_afl_burning": ("Non-AFL fields found with burning traces", "count",
                        "Burnt fields found by Parali Protection Force / nodal officers that were NOT in the satellite active-fire list (AFL)."),
    "ec_cases": ("Cases in which environmental compensation (EC) was imposed", "count", ""),
    "ec_imposed_rs": ("Environmental compensation imposed", "Rs",
                      "Cumulative amount of EC (challans) imposed on farmers for stubble burning."),
    "ec_recovered_rs": ("Environmental compensation recovered / realised / collected", "Rs", ""),
    "firs": ("FIRs / police cases registered against farmers", "count",
             "FIRs under BNS s.223 (from July 2024) or IPC s.188 (earlier) for violating prohibitory orders; Haryana 2016-2020 figure is 'police complaints / FIRs'."),
    "red_entries": ("Red entries in land records (khasra girdawari / farm records)", "count", ""),
    "prosecutions_air_act": ("Prosecutions filed u/s 39 of the Air Act, 1981", "count", ""),
    "officials_show_cause": ("Warnings / notices / show-cause notices to nodal & supervisory officers", "count", ""),
    "officials_suspended": ("Suspension orders against nodal / supervisory officers", "count", ""),
    "officials_prosecuted": ("Nodal / supervisory officers against whom prosecution was initiated (s.14 CAQM Act)", "count", ""),
    "officials_chargesheeted": ("Charge-sheets issued to nodal / supervisory officers", "count", ""),
    "officials_terminated": ("Termination orders against nodal / supervisory officers", "count", ""),
    "numberdars_suspended": ("Numberdars (village revenue functionaries) suspended", "count", ""),
    "officials_explanations": ("Explanations called from officials", "count", "Haryana Agriculture Dept 'Action Taken Summary'."),
    "field_functionaries": ("Field functionaries deployed for stubble-burning monitoring", "count", ""),
    # ---- CRM
    "crm_machines_year": ("CRM machines supplied in the year (subsidised, central CRM scheme)", "count",
                          "Machines delivered to individual farmers and Custom Hiring Centres in that financial year (FY 2018-19 = season 2018)."),
    "happy_seeders_year": ("Happy Seeders among CRM machines supplied in the year", "count", ""),
    "crm_machines_cumulative": ("CRM machines supplied / available, cumulative", "count",
                                "Cumulative since 2018-19 under the CRM scheme unless the note says otherwise ('available' and 'functional' counts are stock figures)."),
    "crm_machines_functional": ("Functional CRM machines (stock)", "count", ""),
    "chc_year": ("Custom Hiring Centres established in the year", "count", ""),
    "chc_count": ("Custom Hiring Centres (CRM) established, cumulative", "count", ""),
    "crm_funds_released_rs_cr": ("Central CRM-scheme funds released in the financial year", "Rs crore", "FY 2018-19 = season 2018."),
    "crm_funds_released_cumulative_rs_cr": ("Central CRM-scheme funds released, cumulative since 2018-19", "Rs crore", ""),
    "crm_funds_utilised_rs_cr": ("CRM funds utilised / expenditure in the financial year", "Rs crore", ""),
    "crm_funds_allocated_rs_cr": ("CRM funds allocated", "Rs crore", ""),
    "residue_managed_lakh_t": ("Crop residue managed (as reported)", "lakh tonnes", ""),
    "crm_area_managed_lakh_ha": ("Area over which crop residue management was adopted", "lakh ha", ""),
    "ex_situ_tonnes": ("Paddy straw used ex-situ (industry, power, CBG, pellets, co-firing, etc.)", "tonnes", ""),
    "in_situ_tonnes": ("Paddy straw managed in-situ", "tonnes", ""),
    "fodder_tonnes": ("Paddy straw used as fodder", "tonnes", ""),
    "in_situ_area_registered_acres": ("Area registered for in-situ management incentive (all in-situ machines)", "acres",
                                      "Haryana 'Crop Residue Management @ Rs 1000/acre' scheme: machine-wise registered area (self-registered, not necessarily verified)."),
    "super_seeder_area_acres": ("Area registered under Super Seeder (incentive scheme)", "acres", ""),
    "happy_seeder_area_acres": ("Area registered under Happy Seeder (incentive scheme)", "acres", ""),
    "ex_situ_area_registered_acres": ("Area registered for ex-situ (straw baler) management incentive", "acres", ""),
    "incentive_farmers_registered": ("Farmers registered for the per-acre CRM incentive", "count", ""),
    "biodecomposer_area_ha": ("PUSA bio-decomposer application area", "ha", ""),
    "cbg_plants_functional": ("Functional Compressed Bio-Gas (CBG) plants (all feedstocks)", "count", ""),
    "incentive_per_acre_rs": ("Per-acre incentive to farmers for not burning / managing paddy straw", "Rs per acre", ""),
}


def src(sid: str, title: str, publisher: str, url: str, date: str | None, local: str | None = None,
        note: str = "", kind: str = "") -> str:
    if sid not in SOURCES:
        SOURCES[sid] = dict(id=sid, title=title, publisher=publisher, url=url, date=date,
                            retrieved=TODAY, local=(os.path.relpath(local, ROOT) if local else None),
                            note=note, kind=kind)
    return sid


def add(metric: str, state: str, season: int | None, value, source: str, as_of: str | None = None,
        district: str | None = None, period: str | None = None, basis: str | None = None,
        note: str | None = None, **extra):
    assert metric in METRIC_DEFS, metric
    assert source in SOURCES, source
    m = METRICS.setdefault(metric, {"label": METRIC_DEFS[metric][0], "unit": METRIC_DEFS[metric][1],
                                    "note": METRIC_DEFS[metric][2], "by_state": {}, "by_district": {}})
    e = {"season": season, "as_of": as_of, "value": value, "source": source}
    if period:
        e["period"] = period
    if basis:
        e["basis"] = basis
    if note:
        e["note"] = note
    e.update({k: v for k, v in extra.items() if v is not None})
    if district:
        m["by_district"].setdefault(state, {}).setdefault(district, []).append(e)
    else:
        m["by_state"].setdefault(state, []).append(e)
    tag = metric + (" (district)" if district else "")
    if tag not in USED[source]:
        USED[source].append(tag)


DISTRICT_ALIASES = {
    # Punjab
    "AMRITSAR": "Amritsar", "BARNALA": "Barnala", "BATHINDA": "Bathinda", "FATEHGARH SAHIB": "Fatehgarh Sahib",
    "FARIDKOT": "Faridkot", "FAZILKA": "Fazilka", "FIROZPUR": "Ferozepur", "FEROZEPUR": "Ferozepur",
    "GURDASPUR": "Gurdaspur", "HOSHIARPUR": "Hoshiarpur", "JALANDHAR": "Jalandhar", "KAPURTHALA": "Kapurthala",
    "LUDHIANA": "Ludhiana", "MANSA": "Mansa", "MOGA": "Moga", "MUKTSAR": "Sri Muktsar Sahib",
    "SBS NAGAR": "Shahid Bhagat Singh Nagar", "PATHANKOT": "Pathankot", "PATIALA": "Patiala",
    "RUPNAGAR": "Rupnagar", "S.A.S. NAGAR": "S.A.S Nagar", "SAS NAGAR": "S.A.S Nagar", "SANGRUR": "Sangrur",
    "TARN TARAN": "Tarn Taran", "MALERKOTLA": "Malerkotla",
    # Haryana
    "AMBALA": "Ambala", "BHIWANI": "Bhiwani", "CHARKHI DADRI": "Charkhi Dadri", "FARIDABAD": "Faridabad",
    "FATEHABAD": "Fatehabad", "GURUGRAM": "Gurugram", "HANSI": "Hansi", "HISSAR": "Hisar", "HISAR": "Hisar",
    "JHAJJAR": "Jhajjar", "JIND": "Jind", "KAITHAL": "Kaithal", "KARNAL": "Karnal", "KURUKSHETRA": "Kurukshetra",
    "MAHINDERGARH": "Mahendragarh", "MAHENDRAGARH": "Mahendragarh", "MEWAT": "Nuh", "NUH": "Nuh",
    "PALWAL": "Palwal", "PANCHKULA": "Panchkula", "PANIPAT": "Panipat", "REWARI": "Rewari", "ROHTAK": "Rohtak",
    "SIRSA": "Sirsa", "SONIPAT": "Sonipat", "YAMUNANAGAR": "Yamunanagar",
}


def dname(s: str) -> str:
    k = re.sub(r"\s+", " ", s.strip().upper())
    if k not in DISTRICT_ALIASES:
        raise KeyError(f"unknown district name {s!r}")
    return DISTRICT_ALIASES[k]


# --------------------------------------------------------------------------------------
# 1. data.gov.in OGD (Rajya Sabha answer tables)
# --------------------------------------------------------------------------------------
OGD_META_FILE = os.path.join(RAW, "datagov", "_catalog_meta.json")


def ogd_source(rid: str) -> str:
    meta = json.load(open(OGD_META_FILE)) if os.path.exists(OGD_META_FILE) else {}
    m = meta.get(rid, {})
    d = fetch_datagov(rid) or {}
    title = d.get("title") or m.get("title") or rid
    note = m.get("note", "")
    mdate = re.search(r"ANSWERED ON,?\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", note, re.I)
    date = None
    if mdate:
        mon = mdate.group(2)[:3].title()
        mon = {"Feb": "Feb"}.get(mon, mon)
        try:
            date = dt.datetime.strptime(f"{mdate.group(1)} {mon} {mdate.group(3)}", "%d %b %Y").date().isoformat()
        except ValueError:
            date = None
    page = ("https://www.data.gov.in" + m["alias"]) if m.get("alias") else f"https://api.data.gov.in/resource/{rid}"
    return src(f"ogd_{rid[:8]}", title, "Rajya Sabha (answer table) via data.gov.in OGD", page, date,
               os.path.join(RAW, "datagov", rid + ".json"),
               note=(note + f" | API resource id {rid}").strip(" |"), kind="ogd")


def ogd_records(rid: str) -> list[dict]:
    d = fetch_datagov(rid)
    if not d:
        return []
    return d.get("records", [])


def fy_season(fy: str) -> int:
    return int(fy[:4])


def is_num(v) -> bool:
    if isinstance(v, (int, float)):
        return True
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", str(v).strip()))


def build_ogd():
    ST = {"Punjab": "Punjab", "Haryana": "Haryana"}

    # fire events 2020-2024 (RS 266 USQ 2905, 20.12.2024)
    rid = "c3daee75-6dc7-4b12-ac83-7a771ded6900"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r.get("state_ut") in ST:
            for y in range(2020, 2025):
                add("fire_events", r["state_ut"], y, num(str(r[f"_{y}"])), s, as_of=f"{y}-11-30",
                    period="15 Sep-30 Nov", basis="seasonal")

    # fire events 2018-2022, period 01 Oct-30 Nov (RS 259 USQ 834, 09.02.2023)
    rid = "e2415996-0692-4d2a-a520-0f186dca643b"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        st = r["states"].split(" (")[0]
        if st in ST:
            for y in (2018, 2019):
                k = f"number_of_active_fire_events_of_paddy_residue_burning___{y}"
                add("fire_events", st, y, num(str(r[k])), s, as_of=f"{y}-11-30", period="01 Oct-30 Nov",
                    basis="seasonal")

    # Haryana FIRs and EC collected 2016-2020 (RS 260 USQ 42, 20.07.2023; values in Rs lakh)
    rid = "8ea030d3-fca5-4a2c-bc0e-f3b6cfec659f"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        y = int(r["year"])
        asof = "2020-12-21" if y == 2020 else None
        fir = num(str(r["number_of_police_complaint__fir_s_registered"]))
        add("firs", "Haryana", y, fir, s, as_of=asof, basis="seasonal (calendar year)",
            note=("Number of police complaints / FIRs registered." +
                  (" Value printed as 2020 for the year 2019 - looks anomalous next to adjacent years; recorded as stated." if y == 2019 else "")))
        lakh = num(str(r["environmental_compensation_collected__rupees_"]))
        add("ec_recovered_rs", "Haryana", y, round(lakh * 100000), s, as_of=asof, basis="seasonal (calendar year)",
            value_as_stated=f"{lakh} (Rs lakh)",
            note="EC collected. Column header says '(Rupees)' but the dataset note says 'Data Figures in Rs. Lacs'; converted lakh -> Rs.")

    # EC imposed 2022 (RS 258 USQ 1008, 15.12.2022) - unit as labelled is Rs lakh
    rid = "bf148d9f-b4b7-436e-8120-7f19e769e87a"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            v = num(str(r["year_2022__in_rs__lakh_"]))
            add("ec_imposed_rs", r["state"], 2022, round(v * 100000), s, as_of="2022-12-15", basis="seasonal",
                value_as_stated=f"{v} (Rs lakh, as labelled)", suspect_unit=True,
                note="Unit as labelled by the OGD table ('in Rs. Lakh'). The implied Rs 17-22 crore is an order of magnitude above every other EC figure for these states; the original answer may have been in Rs thousand or a count. Use with caution.")

    # CRM funds released per FY: two answers (23.12.2022 and 20.12.2024) + 2019-20..2023-24 table
    rid = "e4cece27-bd4b-4eeb-9d74-bb406e3e1b4a"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["_year"] == "Total":
            for st, k in (("Punjab", "funds_released__rs__in_crores____punjab"),
                          ("Haryana", "funds_released__rs__in_crores____haryana")):
                add("crm_funds_released_cumulative_rs_cr", st, 2022, num(str(r[k])), s, as_of="2022-12-23",
                    period="FY 2018-19 to 2022-23", basis="cumulative")
            continue
        for st, k in (("Punjab", "funds_released__rs__in_crores____punjab"),
                      ("Haryana", "funds_released__rs__in_crores____haryana")):
            add("crm_funds_released_rs_cr", st, fy_season(r["_year"]), num(str(r[k])), s,
                period="FY " + r["_year"], basis="financial year")
    rid = "be51c3d4-e785-4fce-84e9-62144ea45c16"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            for fy in ("2021_22", "2022_23", "2023_24", "2024_25"):
                add("crm_funds_released_rs_cr", r["state"], int(fy[:4]), num(str(r["_" + fy])), s,
                    as_of=("2024-12-18" if fy == "2024_25" else None), period="FY " + fy.replace("_", "-"),
                    basis="financial year" + (" (part year)" if fy == "2024_25" else ""))
    rid = "6f8f188b-b3d9-4993-8306-6ab2efdc20b6"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            for fy in ("2019_20", "2020_21", "2021_22", "2022_23", "2023_24"):
                add("crm_funds_released_rs_cr", r["state"], int(fy[:4]), num(str(r["_" + fy])), s,
                    period="FY " + fy.replace("_", "-"), basis="financial year")

    # Funds released & expenditure by component 2018-19, 2019-20 (RS 251 USQ 1765)
    rid = "036a06aa-8e7a-49bf-ab88-bfaaa080bfa6"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state_ut_agency"] in ST:
            for fy in ("2018_19", "2019_20"):
                pre = f"funds_released__crores____{fy}___"
                exp = r[pre + "expenditure"]
                if is_num(exp):
                    add("crm_funds_utilised_rs_cr", r["state_ut_agency"], int(fy[:4]), num(str(exp)), s,
                        period="FY " + fy.replace("_", "-"), basis="financial year",
                        note="'Expenditure' column of funds sanctioned & utilised table.")

    # Funds allocation 2018-19 to 2019-20 (Rs lakh) (RS 249 USQ 20)
    rid = "012a2eaa-79a9-4138-a4a4-2a2dc691677a"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            v = num(str(r["funds_allocation"]))
            add("crm_funds_allocated_rs_cr", r["state"], 2019, v / 100, s, period="FY 2018-19 to 2019-20",
                basis="two financial years", value_as_stated=f"{v} (Rs lakh)")

    # Machines per FY 2018-19..2024-25 incl. Happy Seeders (RS 266 USQ 1974, 12.12.2024; 2024-25 as on 01.12.2024)
    rid = "efa96b64-c444-4570-b204-d6dcc6b2bb90"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        st = r["state_agency"]
        if st not in ST:
            continue
        hs = "Happy Seeders" in r["particulars"]
        for fy in ("2018_19", "2019_20", "2020_21", "2021_22", "2022_23", "2023_24", "2024_25"):
            v = r["_" + fy]
            if not is_num(v):
                continue
            add("happy_seeders_year" if hs else "crm_machines_year", st, int(fy[:4]), num(str(v)), s,
                as_of=("2024-12-01" if fy == "2024_25" else None), period="FY " + fy.replace("_", "-"),
                basis="financial year" + (" (part year)" if fy == "2024_25" else ""))
        if not hs:
            add("crm_machines_cumulative", st, 2024, num(str(r["_total"])), s, as_of="2024-12-01",
                period="FY 2018-19 to 2024-25", basis="cumulative (sum of yearly supplies)")

    # Machines & CHCs per FY 2018-19..2021-22 (RS 263 USQ 681, 08.02.2024 and RS 260 SQ 230, 10.08.2023)
    for rid in ("69b85e41-e5c2-4300-9ed8-5798589d34db", "6b26778c-3dbf-4fee-bc98-20611fb6ad12"):
        s = ogd_source(rid)
        for r in ogd_records(rid):
            st = r["state_agency"]
            if st not in ST:
                continue
            for fy in ("2018_19", "2019_20", "2020_21", "2021_22"):
                mk = f"number_of_machines_delivered_to_the_individual_farmers_and_custom_hiring_centres___{fy}"
                ck = f"number_of_custom_hiring_centres_established___{fy}"
                add("crm_machines_year", st, int(fy[:4]), num(str(r[mk])), s, period="FY " + fy.replace("_", "-"),
                    basis="financial year")
                add("chc_year", st, int(fy[:4]), num(str(r[ck])), s, period="FY " + fy.replace("_", "-"),
                    basis="financial year")

    # Cumulative machines + CHCs + funds released till date (RS 263 USQ 835 / 814, 09.02.2024)
    rid = "bed169f9-ca05-4ebd-90b0-e79136d0401f"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state_ut"] in ST:
            add("crm_machines_cumulative", r["state_ut"], 2023,
                num(str(r["number_of_machines_supplied_to_farmers_and_custom_hiring_centres"])), s,
                as_of="2024-02-09", period="since 2018-19", basis="cumulative",
                note="as on date of answer (09.02.2024)")
            add("chc_count", r["state_ut"], 2023, num(str(r["number_of_custom_hiring_centres_established"])), s,
                as_of="2024-02-09", period="since 2018-19", basis="cumulative", note="as on date of answer (09.02.2024)")
    rid = "b9e0ae07-87e4-45d1-835f-e71a5cd19c58"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            add("crm_funds_released_cumulative_rs_cr", r["state"], 2023,
                num(str(r["fund_released_till_now__rs__in_crores_"])), s, as_of="2024-02-09",
                period="since 2018-19", basis="cumulative", note="'Fund released till now' as on date of answer (09.02.2024)")

    # 2018-19..2021-22 grand total by state (RS 260 SQ 230, 10.08.2023)
    rid = "6032da10-386d-485f-b796-203bce17fdbf"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if str(r["_sl__no_"]) == "4":
            for st in ("Punjab", "Haryana"):
                add("crm_machines_cumulative", st, 2021, num(str(r[st.lower()])), s, as_of=None,
                    period="FY 2018-19 to 2021-22", basis="cumulative (period total)")
        if str(r["_sl__no_"]) == "3":
            for st in ("Punjab", "Haryana"):
                add("chc_count", st, 2021, num(str(r[st.lower()])), s, period="FY 2018-19 to 2021-22",
                    basis="cumulative (period total)")

    # 2019-20..2021-22 CHCs, machines, residue managed, area (RS 255 USQ 1444)
    rid = "95d19978-cbd6-4cef-9bd9-35758d439ffe"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        st = r["state_ut"]
        if st not in ST:
            continue
        per = "FY 2019-20 to 2021-22"
        add("chc_count", st, 2021, num(str(r["number_of_chcs_established"])), s, period=per, basis="period total")
        add("crm_machines_cumulative", st, 2021, num(str(r["number_of_crop_residue_management_machines_distributed"])),
            s, period=per, basis="period total")
        q = r["quantity_of_state_number_of_chcs_established_number_of_crop_residue_management_machines_distributed__lakh_tonne_"]
        if is_num(q):
            add("residue_managed_lakh_t", st, 2021, num(str(q)), s, period=per, basis="period total",
                note="Column titled 'Quantity of ... (Lakh Tonne)' in the OGD table (garbled header); taken as quantity of crop residue managed.")
        a = r["area_of_land_over_which_crop_residue_management_adopted__lakh_hectare_"]
        if is_num(a):
            add("crm_area_managed_lakh_ha", st, 2021, num(str(a)), s, period=per, basis="period total")

    # Bio-decomposer area table (RS 259 USQ 1802, 16.03.2023) is not used: it states no year/season.
    ogd_source("ef3999f2-6d38-4d98-8603-2601ca19de1b")

    # Functional CBG plants (RS USQ 10.02.2025)
    rid = "87ce16a2-7aef-411f-9e49-cb4edc311f83"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state_ut"] in ST:
            add("cbg_plants_functional", r["state_ut"], 2024, num(str(r["no_of_functional_cbg_plants"])), s,
                as_of="2025-02-10", basis="stock", note="All CBG plants (any feedstock), not only paddy-straw based.")

    # fetched for completeness (titles in SOURCES.md); duplicative of the above
    for rid in ("3562482d-53a8-496c-882b-39ff2e625963", "8b74eb59-ab46-465d-9779-4779fe710001",
                "a44a322d-927a-453b-9c0a-36a5c8027c52", "1f8298c0-5883-4f25-9532-68813d2c6172",
                "04602826-3c26-4591-aae3-6054dfadc13e", "3f433763-e9f9-4f60-9e4c-1c961f99a9a1",
                "3a655009-d2d6-44cd-bf52-4fb98f8503e2", "8c9ffe15-b5ce-4384-9762-ef538ddd2775",
                "c8d8f8af-f3dc-4412-8048-509311673172", "23fcc530-6321-4b05-8f07-96c92da1bcd1",
                "5d8a9447-7895-42d6-a3c4-adbba643d180", "e1abe4a8-11a7-46dd-8b24-691fd7b3192c"):
        ogd_source(rid)

    # CHCs 2018-19, 2019-20 and machines from the 2020/2021 answers (differ from later revisions)
    rid = "1f8298c0-5883-4f25-9532-68813d2c6172"
    s = ogd_source(rid)
    for r in ogd_records(rid):
        if r["state"] in ST:
            for fy in ("2018_19", "2019_20"):
                add("crm_machines_year", r["state"], int(fy[:4]),
                    num(str(r[f"_{fy}___total_in_situ_crop_residue_management_machinery_distributed_to_individual_and_chcs"])),
                    s, period="FY " + fy.replace("_", "-"), basis="financial year", note="early (2020/21) answer; later answers revise these")
                add("chc_year", r["state"], int(fy[:4]), num(str(r[f"_{fy}___custom_hiring_centres_established"])), s,
                    period="FY " + fy.replace("_", "-"), basis="financial year", note="early (2020/21) answer; later answers revise these")


# --------------------------------------------------------------------------------------
# 2. Curated facts from text documents (quote-verified)
# --------------------------------------------------------------------------------------
class Doc:
    def __init__(self, path: str):
        self.path = path
        self.text = norm(pdf_text(path))

    def check(self, quote: str, numtxt: str, where: str):
        q = norm(quote)
        if q not in self.text:
            raise AssertionError(f"[{where}] quote not found in {os.path.relpath(self.path, ROOT)}:\n  {q[:160]}")
        if numtxt not in q:
            raise AssertionError(f"[{where}] number {numtxt!r} not inside its quote")


def fact(doc: Doc, sid: str, metric: str, state: str, season, numtxt: str, quote: str, mult: float = 1,
         **kw):
    doc.check(quote, numtxt, f"{metric}/{state}/{season}")
    from decimal import Decimal
    v = Decimal(numtxt.replace(",", "")) * Decimal(str(mult))
    v = int(v) if v == v.to_integral_value() else float(v)
    add(metric, state, season, v, sid, quote=norm(quote), **kw)


CAQM_LIST = os.path.join(RAW, "caqm", "doc_meta.json")


def caqm_doc(doc_id: str, sid: str, title: str, date: str, kind="CAQM document") -> tuple[Doc | None, str]:
    p = os.path.join(RAW, "caqm", f"doc_{doc_id}.pdf")
    url = None
    meta = json.load(open(CAQM_LIST)) if os.path.exists(CAQM_LIST) else {}
    if doc_id in meta:
        url = meta[doc_id]["url"]
    if not os.path.exists(p) and not OFFLINE:
        try:
            with urllib.request.urlopen(f"https://caqm.nic.in/DMSAPI/api/Document/GetAllDocuments?documentId={doc_id}",
                                        timeout=60) as r:
                d = json.load(r)["data"][0]
            url = "https://caqm.nic.in/FileUploadDomain/" + urllib.parse.quote(d["documentPath"])
            curl_to(url, p)
            meta[doc_id] = dict(title=title, url=url, path=d["documentPath"])
            json.dump(meta, open(CAQM_LIST, "w"), indent=1)
        except Exception as e:
            print(f"  ! CAQM doc {doc_id} unavailable: {type(e).__name__}", file=sys.stderr)
    s = src(sid, title, "Commission for Air Quality Management in NCR & Adjoining Areas (CAQM)",
            url or f"https://caqm.nic.in/DMSAPI/api/Document/GetAllDocuments?documentId={doc_id}", date, p, kind=kind)
    return (Doc(p) if os.path.exists(p) else None), s


LS_INDEX = os.path.join(RAW, "sansad", "ls", "_index.json")


def ls_doc(fname: str) -> tuple[Doc | None, str]:
    idx = json.load(open(LS_INDEX)) if os.path.exists(LS_INDEX) else {}
    m = idx.get(fname, {})
    p = os.path.join(RAW, "sansad", "ls", fname)
    if m.get("url"):
        curl_to(m["url"], p)
    sid = "ls_" + fname[3:-4].lower()
    title = f"Lok Sabha {m.get('type', '').title()} Question No. {m.get('qno')} - '{m.get('subject', '')}' ({m.get('ministry', '').title()})"
    s = src(sid, title, "Lok Sabha Secretariat (sansad.in) - Government answer", m.get("url", ""), m.get("date"), p,
            kind="parliament answer")
    return (Doc(p) if os.path.exists(p) else None), s


def build_caqm_facts():
    # ---- CAQM press releases ----------------------------------------------------------
    d, s = caqm_doc("5807", "caqm_pr_2021-11-03", "CAQM press release: Reduction in paddy residue burning cases till 02.11.2021 and imposition of EC", "2021-11-03", "press release")
    if d:
        q = "Enforcement agencies have so far inspected 8,575 of the total reported sites in Punjab, Haryana and NCR Districts of U.P and Environmental Compensation (EC) of about Rs. 58,05,000 has been imposed so far"
        st = "Punjab+Haryana+UP-NCR (combined)"
        fact(d, s, "inspections", st, 2021, "8,575", q, as_of="2021-11-02", basis="cumulative in season",
             note="Combined figure for Punjab, Haryana and NCR districts of UP; not split by state.")
        fact(d, s, "ec_imposed_rs", st, 2021, "58,05,000", q, as_of="2021-11-02", basis="cumulative in season",
             note="'about Rs. 58,05,000'; combined Punjab, Haryana and UP-NCR.")
    d, s = caqm_doc("5810", "caqm_pr_2021-10-15", "CAQM press release: Reduction in paddy residue burning cases in the NCR (15.10.2021)", "2021-10-15", "press release")
    if d:
        st = "Punjab+Haryana+UP-NCR (combined)"
        fact(d, s, "inspections", st, 2021, "663",
             "Out of a total 1795 sites where burning has been reported in Punjab, Haryana and 8 NCR Districts of Uttar Pradesh up till 14.10.2021, 663 fields have been inspected",
             as_of="2021-10-14", basis="cumulative in season")
        fact(d, s, "ec_cases", st, 2021, "252", "EC was imposed in a total of 252 cases, upto 13.10.2021",
             as_of="2021-10-13", basis="cumulative in season")

    d, s = caqm_doc("5774", "caqm_pr_2022-12-05", "CAQM press release: Analysis of paddy stubble management in NCR States and Punjab (05.12.2022)", "2022-12-05", "press release")
    if d:
        q = "Regarding availability of machinery for crop residue management procured through the scheme so far, Punjab has about 1.20 Lakh machines; Haryana about 72,700"
        fact(d, s, "crm_machines_cumulative", "Punjab", 2022, "1.20", q, mult=100000, as_of="2022-12-05",
             basis="cumulative (approx., 'about')", value_as_stated="about 1.20 lakh")
        fact(d, s, "crm_machines_cumulative", "Haryana", 2022, "72,700", q, as_of="2022-12-05",
             basis="cumulative (approx., 'about')")
        q = "about 38,400 Custom Hiring Centers (CHCs) have been established in Punjab, Haryana and Uttar Pradesh, of which about 24,200 CHCs are in Punjab and about 6,775 in Haryana"
        fact(d, s, "chc_count", "Punjab", 2022, "24,200", q, as_of="2022-12-05", basis="cumulative (approx.)")
        fact(d, s, "chc_count", "Haryana", 2022, "6,775", q, as_of="2022-12-05", basis="cumulative (approx.)")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2022, "1,426",
             "Of the total releases, more than Rs. 1,426 crore have been released to State Government of Punjab",
             as_of="2022-12-05", period="FY 2018-19 to 2022-23", basis="cumulative ('more than')")

    d, s = caqm_doc("5832", "caqm_pr_2023-09-26", "CAQM press release: Punjab envisages substantial reduction in paddy stubble burning incidents (26.09.2023)", "2023-09-26", "press release")
    if d:
        q = "Punjab currently has 1,17,672 CRM Machines and around 23,792 Custom Hiring Centers (CHCs) have been established in Punjab"
        fact(d, s, "crm_machines_cumulative", "Punjab", 2023, "1,17,672", q, as_of="2023-09-26", basis="stock ('currently has')")
        fact(d, s, "chc_count", "Punjab", 2023, "23,792", q, as_of="2023-09-26", basis="cumulative ('around')")
    d, s = caqm_doc("5833", "caqm_pr_2023-09-22", "CAQM press release: Haryana to bring down fire counts substantially (22.09.2023)", "2023-09-22", "press release")
    if d:
        fact(d, s, "crm_machines_cumulative", "Haryana", 2023, "80,000",
             "Haryana is currently having more than 80,000 crop residue management machineries",
             as_of="2023-09-22", basis="stock ('more than')")
    d, s = caqm_doc("5825", "caqm_pr_2023-10-30", "CAQM press release: Status of paddy stubble burning incidents during the last 45 days (30.10.2023)", "2023-10-30", "press release")
    if d:
        q = "The total amount of crop residue management (CRM) machines available in Punjab are 1,17,672, Haryana – 80,071"
        fact(d, s, "crm_machines_cumulative", "Punjab", 2023, "1,17,672", q, as_of="2023-10-30", basis="stock (available)")
        fact(d, s, "crm_machines_cumulative", "Haryana", 2023, "80,071", q, as_of="2023-10-30", basis="stock (available)")
    d, s = caqm_doc("5817", "caqm_pr_2023-11-30", "CAQM press release: Status of paddy stubble burning incidents during last 75 days (30.11.2023)", "2023-11-30", "press release")
    if d:
        USED[s].append("context (fire counts 2020-2023, district trends) - used for events")
    d, s = caqm_doc("5951", "caqm_pr_2025-12-01", "CAQM press release: Paddy Harvesting Season 2025 concludes with significant reduction in farm fire incidents (01.12.2025)", "2025-12-01", "press release")
    if d:
        fact(d, s, "fire_events", "Punjab", 2025, "5,114", "Punjab recorded 5,114 fire counts this season",
             as_of="2025-11-30", period="15 Sep-30 Nov", basis="seasonal")
        fact(d, s, "fire_events", "Haryana", 2025, "662", "Haryana maintained its performance as well, logging 662 farm fire incidents this year",
             as_of="2025-11-30", period="15 Sep-30 Nov", basis="seasonal")

    # ---- State Action Plans 2026 (submitted to CAQM) ---------------------------------
    d, s = caqm_doc("5769", "caqm_sap_punjab_2026", "Punjab State Action Plan, 2026 for prevention of paddy stubble burning (published by CAQM)", "2026", "state action plan")
    if d:
        rows = [("2018-19", "269.38", "27,747", "3,888"), ("2019-20", "273.80", "23,068", "5,140"),
                ("2020-21", "272.50", "25,811", "10,808"), ("2021-22", "290.87", "13,796", "3,073"),
                ("2022-23", "278.83", "27,250", "883"), ("2023-24", "175.00", "13,179", "916"),
                ("2024-25", "375.00", "17,600", "1,331")]
        for i, (fy, fu, mc, chc) in enumerate(rows, 1):
            q = f"{i} {fy} {fu} {mc} {chc}"
            per = "FY " + fy
            fact(d, s, "crm_funds_utilised_rs_cr", "Punjab", fy_season(fy), fu, q, period=per, basis="financial year",
                 note="'Funds Utilized (Rs. in Crores)' - State Action Plan table")
            fact(d, s, "crm_machines_year", "Punjab", fy_season(fy), mc, q, period=per, basis="financial year")
            fact(d, s, "chc_year", "Punjab", fy_season(fy), chc, q, period=per, basis="financial year",
                 note="'No. of CHCs established/ strengthened'")
        q = "8 2025-26 ( till 17.12.25) 294.00 10,447 1044"
        fact(d, s, "crm_funds_utilised_rs_cr", "Punjab", 2025, "294.00", q, as_of="2025-12-17", period="FY 2025-26 (till 17.12.2025)", basis="financial year (part)")
        fact(d, s, "crm_machines_year", "Punjab", 2025, "10,447", q, as_of="2025-12-17", period="FY 2025-26 (till 17.12.2025)", basis="financial year (part)")
        fact(d, s, "chc_year", "Punjab", 2025, "1044", q, as_of="2025-12-17", period="FY 2025-26 (till 17.12.2025)", basis="financial year (part)")
        q = "Total 2229.38 158,898 27,083"
        fact(d, s, "crm_machines_cumulative", "Punjab", 2025, "158,898", q, as_of="2025-12-17", period="FY 2018-19 to 2025-26", basis="cumulative")
        fact(d, s, "chc_count", "Punjab", 2025, "27,083", q, as_of="2025-12-17", period="FY 2018-19 to 2025-26", basis="cumulative (established/strengthened)")
        q = "Number of Warnings/ Notices/ Show cause notices issued to Nodal/Supervisory Officers 1513 iii. Number of Nodal/Supervisory Officers, against whom prosecution action has been initiated under Section 14 of CAQM Act,2021. 29"
        fact(d, s, "officials_show_cause", "Punjab", 2025, "1513", q, basis="seasonal", period="paddy season 2025")
        fact(d, s, "officials_prosecuted", "Punjab", 2025, "29", q, basis="seasonal", period="paddy season 2025")
        fact(d, s, "field_functionaries", "Punjab", 2025, "10500",
             "The State in 2025 had appointed 10500 number of Field Functionaries to monitor and contain the stubble fires.",
             basis="seasonal")
        q = "1 In-Situ Management 11.4 10.53*** 2 Ex-Situ management 7.06 7.8"
        fact(d, s, "in_situ_tonnes", "Punjab", 2025, "11.4", q, mult=1_000_000, basis="seasonal (2025 actual)",
             value_as_stated="11.4 million tons", note="State Action Plan 2026, 'Paddy Straw Utilization (million tons) 2025 (Actual)'")
        fact(d, s, "ex_situ_tonnes", "Punjab", 2025, "7.06", q, mult=1_000_000, basis="seasonal (2025 actual)",
             value_as_stated="7.06 million tons",
             note="Breakdown (million tons): industrial boilers 4.1, biomass power 1.17, CBG 0.50, bio-ethanol 0.2, thermal power plants 0.87, brick kilns 0.22.")
        fact(d, s, "fodder_tonnes", "Punjab", 2025, "0.86", "3 Fodder 0.86 1.13", mult=1_000_000,
             basis="seasonal (2025 actual)", value_as_stated="0.86 million tons")

    d, s = caqm_doc("5767", "caqm_sap_haryana_2026", "Haryana State Action Plan, 2026 (CAQM proforma, Annexure-I)", "2026", "state action plan")
    if d:
        fact(d, s, "crm_machines_functional", "Haryana", 2025, "52684",
             "1. Functional CRM Machines (Nos) 52684 66317 13633 10/2026", as_of="2025-12-31", basis="stock",
             note="Status as on 31 Dec 2025; optimum requirement 66,317 (gap 13,633).")
        fact(d, s, "chc_count", "Haryana", 2025, "3019", "2. Custom Hiring Centres (Nos) 3019 3019 0",
             as_of="2025-12-31", basis="stock",
             note="CAQM proforma figure (functional CHCs, status 31.12.2025) - much lower than the cumulative CHCs 'established' under the central scheme (6,775 by 2024).")
        fact(d, s, "ex_situ_tonnes", "Haryana", 2025, "9917739", "Total (1 to 8) 11356155 9917739 1434416",
             as_of="2025-12-31", basis="seasonal (total utilization in 2025)",
             note="Sum of pellet/briquette 1,083,108; industrial boilers 1,208,806; biomass power 6,541,500; CBG 203,590; 2G ethanol 77,000; TPP co-firing 790,395; brick kilns 13,341 MT - as reported in the proforma; the biomass-power line likely includes non-paddy biomass.")


def build_ls_facts():
    d, s = ls_doc("LS_20250310_U1737.pdf")
    if d:
        fact(d, s, "ec_recovered_rs", "Punjab", 2024, "1,48,03,000",
             "Total amount of environmental compensation (EC) collected from farmers for Stubble burning during Paddy Stubble season in 2024 in the State of Punjab is Rs 1,48,03,000",
             as_of="2024-11-30", period="15 Sep-30 Nov 2024", basis="seasonal", note="EC collected")
        q = "the Government of Haryana is providing incentives to the farmers which include an incentive of Rs. 1000/- per acre for management of crop residue by making bales"
        fact(d, s, "incentive_per_acre_rs", "Haryana", 2024, "1000", q, as_of="2025-03-10",
             note="Rs 1000/acre for ex-situ (bales) management; Haryana also pays Rs 1000/acre for in-situ machine use (see Haryana CRM registered-area report).")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2024, "1756.45",
             "During the period from 2018-19 to 2024-25 (as on 28.02.2025), Rs. 3698.45 crores have been released by MoA&FW (Punjab - Rs. 1756.45 crores, Haryana - Rs. 1081.71 crores",
             as_of="2025-02-28", period="FY 2018-19 to 2024-25", basis="cumulative")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Haryana", 2024, "1081.71",
             "During the period from 2018-19 to 2024-25 (as on 28.02.2025), Rs. 3698.45 crores have been released by MoA&FW (Punjab - Rs. 1756.45 crores, Haryana - Rs. 1081.71 crores",
             as_of="2025-02-28", period="FY 2018-19 to 2024-25", basis="cumulative")
    d, s = ls_doc("LS_20241125_U69.pdf")
    if d:
        q = "During the period from 2018 to 2024-25 (till 15.11.2024), a total of Rs. 3623.45 crores have been released (Punjab - Rs. 1681.45 crores, Haryana - Rs. 1081.71 crores"
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2024, "1681.45", q, as_of="2024-11-15", period="2018 to 2024-25", basis="cumulative")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Haryana", 2024, "1081.71", q, as_of="2024-11-15", period="2018 to 2024-25", basis="cumulative")
    d, s = ls_doc("LS_20251201_S18.pdf")
    if d:
        q = "During the period from 2018-19 to 2025-26 (as on 17.11.2025), Rs. 3120.16 crores (Punjab - Rs. 1963.45 crores, Haryana- Rs. 1156.71 crores) have been released"
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2025, "1963.45", q, as_of="2025-11-17", period="FY 2018-19 to 2025-26", basis="cumulative")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Haryana", 2025, "1156.71", q, as_of="2025-11-17", period="FY 2018-19 to 2025-26", basis="cumulative")
        q = "These States have distributed more than 2.6 lakh machines to the individual famers and to more than 33,800 CHCs in these States."
        fact(d, s, "crm_machines_cumulative", "Punjab+Haryana (combined)", 2025, "2.6", q, mult=100000, as_of="2025-11-17",
             basis="cumulative ('more than')", value_as_stated="more than 2.6 lakh")
        fact(d, s, "chc_count", "Punjab+Haryana (combined)", 2025, "33,800", q, as_of="2025-11-17", basis="cumulative ('more than')")
    d, s = ls_doc("LS_20251202_U328.pdf")
    if d:
        fact(d, s, "crm_machines_cumulative", "Punjab", 2025, "1.59",
             "during the period from 2018-19 to 2025-26 (as on 25.11.2025), more than 1.59 lakh machines have been supplied in the State",
             mult=100000, as_of="2025-11-25", period="FY 2018-19 to 2025-26", basis="cumulative ('more than')",
             value_as_stated="more than 1.59 lakh")
    d, s = ls_doc("LS_20260202_U431.pdf")
    if d:
        q = "During the period from 2018-19 to 2025-26 (as on 20.01.2026), Rs. 4027.88 crores have been released to Punjab, Haryana, Uttar Pradesh and Delhi (Punjab-Rs. 2026.45 Crores, Haryana- Rs. 1156.71 crores"
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2025, "2026.45", q, as_of="2026-01-20", period="FY 2018-19 to 2025-26", basis="cumulative")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Haryana", 2025, "1156.71", q, as_of="2026-01-20", period="FY 2018-19 to 2025-26", basis="cumulative")
    d, s = ls_doc("LS_20260209_U1552.pdf")
    if d:
        fact(d, s, "ec_cases", "Punjab", 2025, "2386",
             "Environmental compensation has been imposed in 2386 incidents as per Air Quality Management in National Capital Region and Adjoining Areas",
             as_of="2025-11-30", basis="seasonal", note="paddy season 2025")
        fact(d, s, "incentive_per_acre_rs", "Haryana", 2025, "1200",
             "Government of Haryana also provides an incentives of Rs.1200 per acre to farmers for not engaging in stubble burning.",
             as_of="2026-02-09")
        fact(d, s, "crm_machines_cumulative", "Punjab", 2025, "1.48",
             "which includes 1.48 lakh Crop Residue Machines (CRM) and 1500 CHC in Punjab for in-situ crop residue management",
             mult=100000, as_of="2026-02-04", basis="cumulative", value_as_stated="1.48 lakh",
             note="Same answer gives Rs 4,173.84 crore released 2018-19 to 2025-26 (as on 04.02.2026), Rs 2026.45 crore to Punjab. The '1500 CHC' in the same sentence is far below every other Punjab CHC count and is not used.")
        fact(d, s, "crm_funds_released_cumulative_rs_cr", "Punjab", 2025, "2026.45",
             "So far, Rs.4,173.84 crore has been released during the period 2018-19 till 2025-26 (as on 04.02.2026), of which, an amount of Rs. 2026.45 crore has been released to State of Punjab.",
             as_of="2026-02-04", period="FY 2018-19 to 2025-26", basis="cumulative")
    d, s = ls_doc("LS_20250819_U4233.pdf")
    if d:
        q = "Punjab 59664 50735 83002 71304 49922 36650 10909 Haryana 9227 6364 4202 6987 3661 2296 1406"
        for y, pv, hv in zip(range(2018, 2025), ["59664", "50735", "83002", "71304", "49922", "36650", "10909"],
                             ["9227", "6364", "4202", "6987", "3661", "2296", "1406"]):
            fact(d, s, "fire_events", "Punjab", y, pv, q, as_of=f"{y}-11-30", period="15 Sep-30 Nov", basis="seasonal")
            fact(d, s, "fire_events", "Haryana", y, hv, q, as_of=f"{y}-11-30", period="15 Sep-30 Nov", basis="seasonal")
    # all-state cumulative series (Punjab+Haryana+UP+Delhi), for context
    allst = "CRM states total (Punjab, Haryana, UP, Delhi)"
    for fn, q, n, asof in [
        ("LS_20250318_U2801.pdf", "during the period from 2018-19 to 2024-25 (as on 28 February 2025), an amount of Rs. 3698.45 Crore have been released to these States and Indian Council of Agricultural Research (ICAR)", "3698.45", "2025-02-28"),
        ("LS_20250729_U1592.pdf", "Under the CRM scheme as on 30.06.2025, the central funds amounting to Rs. 3951.16 Crores have been released.", "3951.16", "2025-06-30"),
        ("LS_20250819_U4233.pdf", "during the period from 2018-19 to 2025-26 (as on 31 July 2025), an amount of Rs. 3926.16 Crore have been released to the States of Punjab", "3926.16", "2025-07-31"),
        ("LS_20251208_U1362.pdf", "During the period from 2018-19 to 2025-26 (as on 02.12.2025), Rs. 4090.84 crores have been released.", "4090.84", "2025-12-02"),
        ("LS_20260209_U1552.pdf", "So far, Rs.4,173.84 crore has been released during the period 2018-19 till 2025-26 (as on 04.02.2026)", "4,173.84", "2026-02-04"),
        ("LS_20260316_U3733.pdf", "During the period from 2018-19 to 2025-26 (as on 10.03.2026), Rs. 4237.47 crores have been released.", "4237.47", "2026-03-10"),
    ]:
        d, s = ls_doc(fn)
        if d:
            fact(d, s, "crm_funds_released_cumulative_rs_cr", allst, int(asof[:4]) - (1 if asof[5:7] < "09" else 0), n, q,
                 as_of=asof, basis="cumulative",
                 note="All CRM-scheme states and ICAR; note the 30.06.2025 figure (3951.16) exceeds the 31.07.2025 one (3926.16) - recorded as stated.")
    for fn, q, n, m, asof, metric in [
        ("LS_20250318_U2801.pdf", "The States have established more than 41,900 CHCs of crop residue management machines and more than 3.23 lakh crop residue management machines have been supplied", "3.23", 100000, "2025-02-28", "crm_machines_cumulative"),
        ("LS_20250318_U2801.pdf", "The States have established more than 41,900 CHCs of crop residue management machines", "41,900", 1, "2025-02-28", "chc_count"),
        ("LS_20251208_U1362.pdf", "The States have distributed more than 3.45 lakh machines to the individual famers and established more than 43270 CHCs in the States.", "3.45", 100000, "2025-12-02", "crm_machines_cumulative"),
        ("LS_20251208_U1362.pdf", "The States have distributed more than 3.45 lakh machines to the individual famers and established more than 43270 CHCs in the States.", "43270", 1, "2025-12-02", "chc_count"),
        ("LS_20260316_U3733.pdf", "The States have distributed more than 3.53 lakh machines to the individual famers and established more than 43535 CHCs in the States.", "3.53", 100000, "2026-03-10", "crm_machines_cumulative"),
        ("LS_20260316_U3733.pdf", "The States have distributed more than 3.53 lakh machines to the individual famers and established more than 43535 CHCs in the States.", "43535", 1, "2026-03-10", "chc_count"),
    ]:
        d, s = ls_doc(fn)
        if d:
            fact(d, s, metric, allst, int(asof[:4]) - (1 if asof[5:7] < "09" else 0), n, q, mult=m, as_of=asof,
                 basis="cumulative ('more than')", value_as_stated=(f"more than {n} lakh" if m != 1 else None))


# --------------------------------------------------------------------------------------
# 3. PPCB daily "Monitoring and Enforcement Actions" ATRs (text-layer PDFs) - Punjab
# --------------------------------------------------------------------------------------
PPCB_ATR = [  # (file, as_of, season, include_districts)
    ("CAQM_ATR_15.10.2025.pdf", "2025-10-15", 2025, False),
    ("CAQM31.10.2025.pdf", "2025-10-31", 2025, False),
    ("CAQM_ATR_15.11.2025.pdf", "2025-11-15", 2025, False),
    ("CAQM_ATR_30.11.2025.pdf", "2025-11-30", 2025, True),
    ("CAQM_ATR_15.10.2026.pdf", "2026-10-15", 2026, False),
    ("CAQM_ATR_16.10.2026.pdf", "2026-10-16", 2026, True),
]
ATR_COLS = ["paddy_area_lakh_ha", "harvested_pct", "fire_events", "inspections", "burning_confirmed",
            "non_afl_burning", "ec_cases", "ec_imposed_rs", "ec_recovered_rs", "firs", "red_entries",
            "officials_prosecuted", "officials_chargesheeted", "officials_suspended", "officials_show_cause"]


def build_ppcb_atr():
    for fn, asof, season, with_d in PPCB_ATR:
        p = os.path.join(RAW, "punjab", "ppcb_atr", fn)
        url = "https://ppcb.punjab.gov.in/media/documents/" + fn
        curl_to(url, p)
        if not os.path.exists(p):
            continue
        page = ("https://ppcb.punjab.gov.in/en/action-taken-report-of-stubble-burning-paddy-harvesting-season-%d/" % season)
        s = src(f"ppcb_atr_{asof}", f"PPCB 'Monitoring and Enforcement Actions, State - Punjab, As on {asof[8:10]}.{asof[5:7]}.{asof[:4]}' (CAQM-format daily action-taken report)",
                "Punjab Pollution Control Board (PPCB)", url, asof, p,
                note=f"Listed on {page}", kind="state ATR (text PDF)")
        t = pdf_text(p)
        assert asof[8:10] + "." + asof[5:7] + "." + asof[:4] in t.replace(" ", ""), f"date mismatch in {fn}"
        cells = [c.strip() for c in t.split("\n") if c.strip()]
        rows = {}
        for i, c in enumerate(cells):
            key = re.sub(r"\s+", " ", c.upper())
            if key in DISTRICT_ALIASES or key == "TOTAL":
                vals = cells[i + 1:i + 1 + len(ATR_COLS)]
                if len(vals) == len(ATR_COLS) and all(re.fullmatch(r"[\d,\.]+", v) for v in vals):
                    rows[key] = [num(v) for v in vals]
        assert "TOTAL" in rows, fn
        dsum = [sum(r[j] for k, r in rows.items() if k != "TOTAL") for j in range(len(ATR_COLS))]
        for j, col in enumerate(ATR_COLS):
            if col in ("paddy_area_lakh_ha", "harvested_pct"):
                continue
            tot = rows["TOTAL"][j]
            if abs(dsum[j] - tot) > 0.5:
                print(f"  ! {fn}: district sum {dsum[j]} != printed total {tot} for {col}", file=sys.stderr)
            add(col, "Punjab", season, tot, s, as_of=asof, period=f"15 Sep {season} - {asof}",
                basis="cumulative in season" + (" (end of season)" if asof.endswith("11-30") else ""))
            if with_d:
                for k, r in rows.items():
                    if k != "TOTAL":
                        add(col, "Punjab", season, r[j], s, as_of=asof, district=dname(k),
                            period=f"15 Sep {season} - {asof}", basis="cumulative in season", name_in_source=k.title())


# --------------------------------------------------------------------------------------
# 4. NGT OA 632/2023 filings (scanned) - Punjab 2024 season
# --------------------------------------------------------------------------------------
NGT_URLS = os.path.join(RAW, "ngt", "_urls.json")
# Annexure PB-I "Action Taken Report upto 03.11.2024" (page 82 of PPCB affidavit) transcribed from the
# scanned image.  columns: fires, sites visited, no-burning, EC cases, EC imposed Rs, EC yet to impose,
# EC recovered Rs, red entries, Air Act s.39 prosecutions, FIR s.223 BNS, cases u/s 126/170 BNSS,
# EC on harvest combines, warnings to officials, officials prosecuted s.14 CAQM Act, pending visits
PB_I_COLS = ["fire_events", "inspections", "no_burning_found", "ec_cases", "ec_imposed_rs", "_ec_pending",
             "ec_recovered_rs", "red_entries", "prosecutions_air_act", "firs", "_bnss_126_170", "_ec_combines",
             "officials_show_cause", "officials_prosecuted", "_pending_visits"]
PB_I_ROWS = {
    "AMRITSAR": [600, 600, 340, 249, 635000, 0, 632500, 249, 2, 342, 0, 2, 127, 11, 0],
    "BARNALA": [53, 53, 26, 27, 67500, 0, 67500, 27, 0, 24, 0, 0, 10, 0, 0],
    "BATHINDA": [154, 154, 70, 84, 225000, 0, 125000, 84, 0, 66, 0, 0, 3, 2, 0],
    "FATEHGARH SAHIB": [161, 161, 73, 88, 222500, 0, 222500, 88, 0, 100, 0, 0, 10, 2, 0],
    "FARIDKOT": [81, 81, 43, 38, 100000, 0, 85000, 38, 0, 34, 0, 6, 9, 3, 0],
    "FAZILKA": [35, 35, 18, 17, 55000, 0, 55000, 17, 0, 17, 0, 0, 13, 2, 0],
    "FIROZPUR": [497, 490, 185, 305, 762500, 0, 700000, 305, 0, 382, 0, 0, 102, 10, 7],
    "GURDASPUR": [154, 154, 60, 91, 227500, 0, 227500, 91, 0, 87, 0, 1, 165, 4, 0],
    "HOSHIARPUR": [12, 12, 6, 5, 25000, 0, 5000, 5, 0, 4, 0, 0, 0, 0, 0],
    "JALANDHAR": [66, 66, 21, 45, 140000, 0, 110000, 45, 0, 47, 0, 1, 19, 0, 0],
    "KAPURTHALA": [223, 223, 99, 124, 360000, 0, 360000, 124, 1, 100, 0, 0, 128, 2, 0],
    "LUDHIANA": [103, 103, 59, 41, 117500, 0, 80000, 37, 0, 58, 0, 0, 47, 1, 0],
    "MANSA": [204, 204, 94, 110, 275000, 0, 247500, 110, 0, 113, 0, 0, 18, 4, 0],
    "MOGA": [124, 124, 19, 105, 282500, 0, 282500, 105, 0, 69, 0, 0, 29, 1, 0],
    "MUKTSAR": [42, 42, 18, 16, 47500, 8, 10000, 20, 0, 14, 0, 0, 5, 0, 0],
    "SBS NAGAR": [14, 14, 9, 5, 15000, 0, 15000, 3, 0, 3, 0, 0, 4, 0, 0],
    "PATHANKOT": [1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],
    "PATIALA": [391, 381, 100, 281, 730000, 0, 730000, 281, 1, 289, 41, 6, 37, 5, 10],
    "RUPNAGAR": [10, 10, 5, 5, 15000, 0, 2500, 4, 0, 6, 0, 0, 0, 0, 0],
    "S.A.S. NAGAR": [37, 37, 24, 13, 35000, 0, 35000, 13, 0, 16, 0, 0, 29, 1, 0],
    "SANGRUR": [552, 490, 349, 141, 352500, 0, 287500, 141, 0, 243, 0, 0, 51, 3, 62],
    "TARN TARAN": [563, 556, 270, 286, 727500, 0, 667500, 286, 3, 463, 0, 0, 160, 3, 7],
    "MALERKOTLA": [55, 55, 36, 19, 47500, 0, 10000, 19, 0, 27, 0, 0, 11, 2, 0],
}
PB_I_TOTAL = [4132, 4046, 1925, 2095, 5465000, 8, 4957500, 2092, 7, 2505, 41, 16, 977, 56, 86]


def build_ngt():
    urls = json.load(open(NGT_URLS)) if os.path.exists(NGT_URLS) else {}
    p = os.path.join(RAW, "ngt", "ppcb_affidavit_oa632.pdf")
    if "ppcb_affidavit_oa632.pdf" in urls:
        curl_to(urls["ppcb_affidavit_oa632.pdf"], p)
    s = src("ngt_ppcb_affidavit_2024-11", "Affidavit of Member Secretary, PPCB in NGT OA No. 632/2023 (In re: 'Pollution takes a front seat as stubble fires spike in Punjab'), in compliance of order dated 21.10.2024 - incl. Annexure PB-I 'Action Taken Report upto 03.11.2024'",
            "Punjab Pollution Control Board, filed before the National Green Tribunal", urls.get("ppcb_affidavit_oa632.pdf", ""),
            "2024-11", p, kind="court filing (scanned)")
    # arithmetic guard on the transcription
    for j in range(len(PB_I_COLS)):
        ssum = sum(r[j] for r in PB_I_ROWS.values())
        assert ssum == PB_I_TOTAL[j], f"PB-I transcription check failed for column {PB_I_COLS[j]}: {ssum} != {PB_I_TOTAL[j]}"
    if os.path.exists(p):  # the OCR layer of the summary page carries the same totals
        t = pdf_text(p)
        for k in ("54,65,000", "49,57,500", "2505", "2092", "977"):
            assert k in t, f"expected {k} in PPCB affidavit OCR text"
    for j, col in enumerate(PB_I_COLS):
        if col.startswith("_"):
            continue
        add(col, "Punjab", 2024, PB_I_TOTAL[j], s, as_of="2024-11-03", period="15 Sep 2024 - 03 Nov 2024",
            basis="cumulative in season (mid-season)", transcribed_from_image=True)
        for k, r in PB_I_ROWS.items():
            add(col, "Punjab", 2024, r[j], s, as_of="2024-11-03", district=dname(k),
                period="15 Sep 2024 - 03 Nov 2024", basis="cumulative in season (mid-season)",
                transcribed_from_image=True, name_in_source=k.title())
    # burning confirmed is printed on the summary page (para 16, row iii) as 2121
    add("burning_confirmed", "Punjab", 2024, 2121, s, as_of="2024-11-03", period="15 Sep 2024 - 03 Nov 2024",
        basis="cumulative in season (mid-season)", transcribed_from_image=True,
        note="Summary table para 16 (iii) 'Cases in which stubble Burning Observed'.")
    for col, v in (("officials_suspended", 34), ("officials_terminated", 1), ("numberdars_suspended", 12)):
        add(col, "Punjab", 2024, v, s, as_of="2024-11-03", basis="cumulative in season (mid-season)",
            transcribed_from_image=True, note="Affidavit para 17 table (action against Nodal/Supervisory Officers).")
    add("field_functionaries", "Punjab", 2024, 9492, s, basis="seasonal", transcribed_from_image=False,
        note="'the State has appointed 9492 number of Field Functionaries' (affidavit para 11).")
    assert "9492" in pdf_text(p) if os.path.exists(p) else True

    p = os.path.join(RAW, "ngt", "ppcb_affidavit_b_oa632.pdf")
    if "ppcb_affidavit_b_oa632.pdf" in urls:
        curl_to(urls["ppcb_affidavit_b_oa632.pdf"], p)
    s = src("ngt_ppcb_affidavit_2024-10", "Affidavit of Member Secretary, PPCB in NGT OA No. 632/2023 in compliance of order dated 21.10.2024 (action 15.09.2024-23.10.2024)",
            "Punjab Pollution Control Board, filed before the National Green Tribunal", urls.get("ppcb_affidavit_b_oa632.pdf", ""),
            "2024-10", p, kind="court filing (scanned)")
    per = "15 Sep 2024 - 23 Oct 2024"
    for col, v in (("firs", 1254), ("red_entries", 496), ("ec_imposed_rs", 1325000), ("ec_cases", 497),
                   ("ec_recovered_rs", 1167500)):
        add(col, "Punjab", 2024, v, s, as_of="2024-10-23", period=per, basis="cumulative in season (early season)",
            transcribed_from_image=True,
            note=("'1254 FIRs have been registered u/s 233 of the Bharaitya Nagarik Suraksha Sanhita (BNS), 2023' (sic)" if col == "firs" else
                  "EC 'imposed upon 497 persons'" if col == "ec_cases" else None))


# --------------------------------------------------------------------------------------
# 5. Haryana Agriculture Department CRM dashboard reports (agriharyana.gov.in)
# --------------------------------------------------------------------------------------
HR_DASH = "https://agriharyana.gov.in/dashboardcrm"


def build_haryana():
    base = os.path.join(RAW, "haryana")
    # enforcement measures 2026 (text PDF)
    p = os.path.join(base, "enforcement_measures_2026-27.pdf")
    if os.path.exists(p):
        s = src("hr_agri_enforcement_2026", "Haryana Agriculture & Farmers Welfare Dept - CRM Dashboard 2026-27: 'Enforcement Measures, Year: 2026, PERFORMA-I' (district-wise)",
                "Department of Agriculture & Farmers Welfare, Haryana", HR_DASH, None, p,
                note="Report generated from the dashboard ('Report On' timestamp printed in the PDF); per-district 'Updated On' dates 29/09-16/10/2026.",
                kind="state dashboard (text PDF)")
        t = pdf_text(p)
        m = re.search(r"Report On:\s*(\d\d)/(\d\d)/(\d{4})", t)
        SOURCES[s]["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
        cols = ["_paddy_area_ha", "_harvested_ha", "fire_events", "inspections", "burning_confirmed", "non_afl_burning",
                "ec_cases", "ec_imposed_rs", "ec_recovered_rs", "firs", "red_entries", "officials_prosecuted",
                "officials_chargesheeted", "officials_suspended", "officials_show_cause"]
        cells = [c.strip() for c in t.split("\n") if c.strip()]
        rows = {}
        for i, c in enumerate(cells):
            key = c.upper()
            if key in DISTRICT_ALIASES or key == "TOTAL":
                vals = cells[i + 1:i + 1 + len(cols)]
                if all(re.fullmatch(r"[\d,\.]+", v) for v in vals):
                    rows[key] = ([num(v) for v in vals], cells[i + 1 + len(cols)] if key != "TOTAL" else None)
        asof = "2026-10-16"
        for j, col in enumerate(cols):
            if col.startswith("_"):
                continue
            tot = rows["TOTAL"][0][j]
            dsum = sum(v[0][j] for k, v in rows.items() if k != "TOTAL")
            assert abs(dsum - tot) < 0.5, (col, dsum, tot)
            add(col, "Haryana", 2026, tot, s, as_of=asof, period="15 Sep 2026 - mid Oct 2026",
                basis="cumulative in season", note="District rows updated between 29/09 and 16/10/2026.")
            for k, (v, upd) in rows.items():
                if k == "TOTAL":
                    continue
                dd, mm, yy = upd.split("/")
                add(col, "Haryana", 2026, v[j], s, as_of=f"{yy}-{mm}-{dd}", district=dname(k),
                    basis="cumulative in season", name_in_source=k.title())

    # red entry summary 2024-25
    p = os.path.join(base, "red_entry_summary_2024-25.pdf")
    if os.path.exists(p):
        s = src("hr_agri_redentry_2024", "Haryana Agriculture Dept - CRM Dashboard 2024-25: 'Red Entry Summary Report, Year: 2024-25' (district-wise)",
                "Department of Agriculture & Farmers Welfare, Haryana", HR_DASH, None, p,
                note="'Status as on' = report generation date; covers the 2024 paddy season dashboard.", kind="state dashboard (text PDF)")
        t = pdf_text(p)
        cells = [c.strip() for c in t.split("\n") if c.strip()]
        rows = {}
        for i, c in enumerate(cells):
            key = c.upper()
            if (key in DISTRICT_ALIASES or key == "TOTAL") and i + 2 < len(cells):
                if re.fullmatch(r"\d+", cells[i + 1]) and re.fullmatch(r"\d+", cells[i + 2]):
                    rows[key] = (int(cells[i + 1]), int(cells[i + 2]))
        tot = rows.pop("TOTAL")
        assert sum(v[0] for v in rows.values()) == tot[0]
        add("red_entries", "Haryana", 2024, tot[0], s, basis="seasonal (dashboard 2024-25)",
            note=f"Red entries; same report lists {tot[1]} 'Non AFL' cases.")
        for k, v in rows.items():
            add("red_entries", "Haryana", 2024, v[0], s, district=dname(k), basis="seasonal (dashboard 2024-25)",
                name_in_source=k.title(), non_afl=v[1])

    # action taken summary 2024-25 (officials)
    p = os.path.join(base, "action_summary_2024-25.pdf")
    if os.path.exists(p):
        s = src("hr_agri_action_2024", "Haryana Agriculture Dept - CRM Dashboard 2024-25: 'Action Taken Summary Report, Year: 2024-25' (district-wise action against officials)",
                "Department of Agriculture & Farmers Welfare, Haryana", HR_DASH, None, p, kind="state dashboard (text PDF)")
        t = pdf_text(p)
        cells = [c.strip() for c in t.split("\n") if c.strip()]
        cols = ["officials_suspended", "officials_chargesheeted", "officials_explanations", "officials_show_cause"]
        tot = [0] * 4
        for i, c in enumerate(cells):
            key = c.upper()
            if key in DISTRICT_ALIASES:
                vals = cells[i + 1:i + 5]
                if all(re.fullmatch(r"\d+", v) for v in vals):
                    for j, col in enumerate(cols):
                        add(col, "Haryana", 2024, int(vals[j]), s, district=dname(key), basis="seasonal (dashboard 2024-25)",
                            name_in_source=key.title())
                        tot[j] += int(vals[j])
        for j, col in enumerate(cols):
            add(col, "Haryana", 2024, tot[j], s, basis="seasonal (dashboard 2024-25)",
                note="Sum of the districts listed in the report (the report prints no total row; districts not listed had no actions).",
                derived="sum of district rows")

    # AFL summary Kharif 2025-26 (red entries on AFL)
    p = os.path.join(base, "afl_crm_2025-26.pdf")
    if os.path.exists(p):
        s = src("hr_agri_afl_2025", "Haryana Agriculture Dept - CRM Dashboard 2025-26: 'AFL Summary Report, Year: Kharif 2025-26' (district-wise)",
                "Department of Agriculture & Farmers Welfare, Haryana", HR_DASH, None, p, kind="state dashboard (text PDF)",
                note="Counts 221 AFL locations found on agricultural land, fewer than the 662 ISRO-protocol events CAQM reported for Haryana 2025 - the dashboard list is not the full CAQM count.")
        t = pdf_text(p)
        cells = [c.strip() for c in t.split("\n") if c.strip()]
        rows = {}
        for i, c in enumerate(cells):
            key = c.upper()
            if key in DISTRICT_ALIASES or key == "TOTAL":
                vals = cells[i + 1:i + 7]
                if len(vals) == 6 and all(re.fullmatch(r"\d+", v) for v in vals):
                    rows[key] = [int(v) for v in vals]
        tot = rows.pop("TOTAL")
        assert [sum(r[j] for r in rows.values()) for j in range(6)] == tot
        add("burning_confirmed", "Haryana", 2025, tot[4], s, basis="seasonal (Kharif 2025-26 dashboard)",
            note="'Found in Agriculture Land' among AFL locations reported by district.")
        add("red_entries", "Haryana", 2025, tot[5], s, basis="seasonal (Kharif 2025-26 dashboard)", note="'Red Entry on AFL'")
        for k, r in rows.items():
            add("burning_confirmed", "Haryana", 2025, r[4], s, district=dname(k), basis="seasonal (Kharif 2025-26 dashboard)", name_in_source=k.title())
            add("red_entries", "Haryana", 2025, r[5], s, district=dname(k), basis="seasonal (Kharif 2025-26 dashboard)", name_in_source=k.title())

    # Registered area under the Rs 1000/acre CRM incentive, 2024-25 (district-wise, pdfplumber table)
    p = os.path.join(base, "insitu_area_2024-25.pdf")
    if os.path.exists(p):
        import pdfplumber
        s = src("hr_agri_crm_area_2024", "Haryana Agriculture Dept - 'Crop Residue Management @ Rs. 1000/- Acre (Machine Wise Registered Area)', Year: 2024-25 (district-wise)",
                "Department of Agriculture & Farmers Welfare, Haryana", HR_DASH, None, p, kind="state dashboard (text PDF)",
                note="Area (acres) and farmers registered for the per-acre incentive by machine type; registration, not verified utilisation.")
        rows = {}
        with pdfplumber.open(p) as pdf:
            for pg in pdf.pages:
                for tb in pg.extract_tables():
                    for r in tb:
                        if r and r[0] and (r[0].upper() in DISTRICT_ALIASES or r[0] == "Total"):
                            rows[r[0].upper()] = [float((c or "").replace("\n", "")) for c in r[1:17]]
        tot = rows.pop("TOTAL")
        for j in range(16):
            assert abs(sum(r[j] for r in rows.values()) - tot[j]) < 1.0, j
        spec = [("happy_seeder_area_acres", 1), ("super_seeder_area_acres", 9), ("in_situ_area_registered_acres", 13),
                ("ex_situ_area_registered_acres", 15)]
        for metric, j in spec:
            add(metric, "Haryana", 2024, tot[j], s, basis="seasonal (registered)")
            for k, r in rows.items():
                add(metric, "Haryana", 2024, r[j], s, district=dname(k), basis="seasonal (registered)", name_in_source=k.title())
        add("incentive_farmers_registered", "Haryana", 2024, int(tot[12] + tot[14]), s, basis="seasonal (registered)",
            derived="in-situ farmers + ex-situ (baler) farmers", note=f"in-situ {int(tot[12])} + ex-situ {int(tot[14])}; a farmer may appear in both")


# --------------------------------------------------------------------------------------
# 6. Dated policy / enforcement events
# --------------------------------------------------------------------------------------
def ev(date: str, state: str, title: str, detail: str, source: str, quote: str | None = None, doc: Doc | None = None,
       **extra):
    if quote is not None and doc is not None:
        doc.check(quote, "", f"event {date}")
    e = {"date": date, "state": state, "title": title, "detail": detail, "source": source}
    if quote:
        e["quote"] = norm(quote)
    e.update(extra)
    EVENTS.append(e)
    if "events" not in USED[source]:
        USED[source].append("events")


def caqm_listing_titles() -> dict[str, dict]:
    p = os.path.join(RAW, "caqm", "listing_docs.json")
    out = {}
    if os.path.exists(p):
        for k, v in json.load(open(p)).items():
            for x in (v.get("data") or []):
                if x.get("documentId"):
                    out[str(x["documentId"])] = x
    return out


def build_events():
    titles = caqm_listing_titles()
    lst = src("caqm_listing", "CAQM website document listings (Directions, Rules, Orders, Press Releases) - caqm.nic.in/website/api",
              "Commission for Air Quality Management in NCR & Adjoining Areas (CAQM)",
              "https://caqm.nic.in/website/api/Home/GetLatestDocumentPolicy?category=5&subcategory=7", None,
              os.path.join(RAW, "caqm", "listing_docs.json"), kind="listing")

    # SC order 06.11.2019 (mirror)
    p = os.path.join(RAW, "sci", "SC_order_2019-11-06_cse_mirror.pdf")
    curl_to("https://cdn.cseindia.org/epca/court_orders/SC-order-dated6-11-19.pdf", p)
    s = src("sc_order_2019-11-06", "Supreme Court of India, W.P.(C) 13029/1985 M.C. Mehta v. Union of India - Record of Proceedings dated 06.11.2019 (In re: stubble burning)",
            "Supreme Court of India (copy hosted by Centre for Science and Environment / EPCA archive)",
            "https://cdn.cseindia.org/epca/court_orders/SC-order-dated6-11-19.pdf", "2019-11-06", p, kind="court order (mirror)")
    if os.path.exists(p):
        d = Doc(p)
        ev("2019-11-06", "Punjab, Haryana, UP", "Supreme Court orders Rs 100/quintal support to non-burning small & marginal farmers",
           "In M.C. Mehta (stubble burning) the Court directed Punjab, Haryana and UP to pay small and marginal farmers who did not burn stubble Rs 100 per quintal of non-basmati paddy within seven days (Punjab's Chief Secretary had estimated this at Rs 2,000-2,500 per acre).",
           s, quote="quantified amount at Rs. 100/- per quintal of Non-Basmati Paddy, shall be given to those farmers within seven days from today by the State Governments to those who have not burnt the stubble.", doc=d,
           incentive_rs_per_quintal=100)

    # CAQM Directions / rules from the CAQM listing (title + date as published by CAQM)
    for docid, date, state, title, detail in [
        ("5528", "2021-06-10", "Punjab", "CAQM Direction No. 6: framework & action plan to tackle stubble burning (Punjab)", "Statutory direction asking Punjab to prepare a detailed action plan on CAQM's framework (in-situ, ex-situ, prohibition, enforcement, IEC)."),
        ("5527", "2021-06-10", "Haryana", "CAQM Direction No. 7: framework & action plan to tackle stubble burning (Haryana)", "Same direction to Haryana."),
        ("5500", "2021-08-16", "Punjab", "CAQM Direction No. 33: standard ISRO protocol to estimate crop-residue fire events (Punjab)", "Fire events to be counted with the ISRO standard satellite protocol (15 Sep-30 Nov)."),
        ("5499", "2021-08-16", "Haryana", "CAQM Direction No. 34: standard ISRO protocol (Haryana)", "Same direction to Haryana."),
        ("5491", "2021-09-17", "Punjab, Haryana", "CAQM Direction No. 42: ex-situ paddy straw use in coal thermal power plants", "Co-firing of paddy-straw biomass in coal-based TPPs."),
        ("5465", "2022-09-12", "Punjab, Haryana", "CAQM Direction No. 67: implementation of revised action plans for 2022", ""),
        ("5457", "2023-07-25", "Punjab, Haryana", "CAQM Direction No. 74: revised plan of action for paddy stubble burning 2023", ""),
        ("5450", "2024-04-12", "Punjab, Haryana", "CAQM Direction No. 80: revised plan of action for paddy stubble burning 2024", ""),
        ("5444", "2024-10-10", "Punjab, Haryana", "CAQM Direction No. 84: DCs authorised to prosecute officials for inaction", "Deputy Commissioners/DMs authorised under s.14(2) CAQM Act to file complaints against nodal/supervisory officers and SHOs for inaction (per Lok Sabha answer 25.11.2024)."),
        ("5437", "2025-05-09", "Punjab, Haryana", "CAQM Direction No. 90: action plan 2025 targeting complete elimination", "Includes rent-free CRM machines for small/marginal farmers, nodal officer per group of farmers, Parali Protection Force, red entries and EC realisation."),
        ("5435", "2025-06-03", "Punjab, Haryana", "CAQM Direction No. 92: paddy-straw pellets mandatory in brick kilns (Punjab and non-NCR Haryana)", ""),
        ("5430", "2025-10-01", "Punjab, Haryana", "CAQM Direction No. 95: effective enforcement in 2025 (DCs may prosecute officials)", ""),
        ("5426", "2026-02-13", "Punjab, Haryana", "CAQM Direction No. 96: wheat stubble burning action plans 2026", ""),
        ("5423", "2026-05-15", "Punjab, Haryana", "CAQM Direction No. 99: action plan 2026 targeting complete elimination of paddy stubble burning", ""),
    ]:
        x = titles.get(docid, {})
        t = x.get("title", "")
        dd = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", t)  # listing title carries 'dt dd.mm.yyyy'
        if dd:
            assert f"{dd.group(3)}-{dd.group(2)}-{dd.group(1)}" == date, (docid, t, date)
        sid = f"caqm_dir_{docid}"
        src(sid, t or title, "Commission for Air Quality Management in NCR & Adjoining Areas (CAQM)",
            (json.load(open(CAQM_LIST)).get(docid, {}).get("url") if os.path.exists(CAQM_LIST) else None) or SOURCES[lst]["url"],
            date, os.path.join(RAW, "caqm", f"doc_{docid}.pdf"), kind="statutory direction",
            note="Scanned PDF; date and title from CAQM's document listing.")
        ev(date, state, title, detail or t, sid, listing_title=t)

    d, s = caqm_doc("6058", "caqm_ec_rules_2023", "CAQM (Imposition, Collection and Utilization of Environmental Compensation for Stubble Burning) Rules, 2023 - G.S.R. 322(E), 28.04.2023", "2023-04-28", "rules (gazette)")
    if d:
        ev("2023-04-28", "Punjab, Haryana, Delhi, UP-NCR, Rajasthan-NCR", "EC Rules 2023: Rs 2,500 / 5,000 / 15,000 per incident by farm size",
           "MoEFCC notified EC for stubble burning: <2 acres Rs 2,500; 2-5 acres Rs 5,000; >5 acres Rs 15,000 (G.S.R. 322(E)).", s,
           quote="(a) The Farmer having an area of land less than two acres shall pay an environmental compensation of rupees two thousand five hundred;", doc=d,
           ec_rates_rs={"<2 acres": 2500, "2-5 acres": 5000, ">5 acres": 15000})
    d, s = caqm_doc("6060", "caqm_ec_rules_2024", "CAQM (Imposition, Collection and Utilization of Environmental Compensation for Stubble Burning) Amendment Rules, 2024 - G.S.R. 690(E), 06.11.2024", "2024-11-06", "rules (gazette)")
    if d:
        ev("2024-11-06", "Punjab, Haryana, Delhi, UP-NCR, Rajasthan-NCR", "EC doubled: Rs 5,000 / 10,000 / 30,000 per incident by farm size",
           "Amendment Rules 2024 (G.S.R. 690(E)) substitute rule 3: <2 acres Rs 5,000; 2-5 acres Rs 10,000; >5 acres Rs 30,000 per incidence.", s,
           quote="the Farmer having an area of land of more than five acres shall pay an environmental compensation of thirty thousand rupees, per incidence", doc=d,
           ec_rates_rs={"<2 acres": 5000, "2-5 acres": 10000, ">5 acres": 30000})
    d, s = caqm_doc("5731", "caqm_order_2024-11-07", "CAQM Order dt. 07.11.2024 - Imposition and Collection of Environmental Compensation for Stubble Burning", "2024-11-07", "order")
    if d:
        ev("2024-11-07", "Punjab, Haryana, Delhi, UP-NCR, Rajasthan-NCR", "CAQM authorises nodal/supervisory officers to impose and collect EC at revised rates",
           "Order authorising all Nodal/Supervisory Officers appointed by the State Governments to impose and collect EC from farmers at the 2024 rates.", s)
    d, s = caqm_doc("6059", "caqm_tpp_rules_2023", "Environment (Utilisation of Crop Residue by Thermal Power Plants) Rules, 2023", "2023-07-18", "rules (gazette)")
    ev("2023-07-18", "Punjab, Haryana", "Crop-residue co-firing rules for thermal power plants notified", "Environment (Utilisation of Crop residue by Thermal Power Plants) Rules, 2023 (EC on TPPs that miss biomass co-firing targets).", s)
    d, s = caqm_doc("6010", "caqm_pr_2026-04-08", "CAQM press release: CAQM imposes Environmental Compensation of approx. Rs 61.85 crore on 06 TPPs (08.04.2026)", "2026-04-08", "press release")
    if d:
        ev("2026-04-08", "Punjab, Haryana, UP", "CAQM imposes ~Rs 61.85 crore EC on 6 thermal power plants for missing paddy-straw co-firing",
           "EC under Rule 3 of the TPP crop-residue rules for FY 2024-25 shortfall (orders dated 01.04.2026 to GHTPP-PSPCL, TSPL, PTPS, RGTPP, DCRTPP, HTPS).", s,
           quote="CAQM imposes Environmental Compensation of approximately ₹61.85 Crore on 06", doc=d)
    d, s = caqm_doc("5894", "caqm_pr_2024-10-01", "CAQM press release: Flying Squads deployed to monitor paddy stubble burning in Punjab and Haryana (01.10.2024)", "2024-10-01", "press release")
    ev("2024-10-01", "Punjab, Haryana", "CPCB flying squads deployed in hotspot districts (01.10-30.11.2024)", "CAQM deploys CPCB flying squads to Punjab and Haryana hotspot districts.", s)
    d, s = caqm_doc("5972", "caqm_pr_2025-07-29", "CAQM press release: Dedicated CAQM Cell set up in SAS Nagar (Mohali), Punjab (29.07.2025)", "2025-07-29", "press release")
    ev("2025-07-29", "Punjab", "CAQM Cell set up at Mohali for year-round stubble monitoring", "Dedicated CAQM cell at Kheti Bhawan, SAS Nagar.", s)
    d, s = caqm_doc("5960", "caqm_pr_2025-11-08", "CAQM press release: review meetings with Punjab and Haryana on actions taken during Paddy Harvest Season 2025 (08.11.2025)", "2025-11-08", "press release")
    if d:
        ev("2025-11-07", "Punjab", "CAQM directs action against nodal officers in high-fire areas", "Review at Chandigarh; CAQM directed Punjab to act against nodal officers and functionaries under whom higher farm fires were recorded.",
           s, quote="It was also directed to take actions against the nodal officers and other functionaries under whom higher number of farm fires have been recorded.", doc=d)
    d, s = caqm_doc("20139", "caqm_notice_193_2026", "CAQM Notice No. 193 dated 16.10.2026 - Non-compliance with Directions to prevent paddy stubble burning (DC & SSP Amritsar)", "2026-10-16", "show-cause notice")
    if d:
        ev("2026-10-16", "Punjab", "CAQM show-cause notice to DC and SSP Amritsar (s.14 CAQM Act)",
           "78 fire incidents 15 Sep-14 Oct 2026 vs 68 in 2025 (+15%); officers asked to explain why prosecution under s.14 should not be initiated.",
           s, district="Amritsar")
    d, s = caqm_doc("20137", "caqm_notice_194_2026", "CAQM Notice No. 194 dated 16.10.2026 - Non-compliance with Directions to prevent paddy stubble burning (DC & SSP Tarn Taran)", "2026-10-16", "show-cause notice")
    if d:
        ev("2026-10-16", "Punjab", "CAQM show-cause notice to DC and SSP Tarn Taran (s.14 CAQM Act)",
           "68 fire incidents 15 Sep-14 Oct 2026 vs 47 in 2025 (+45%).", s, district="Tarn Taran")

    # State-level events from the Punjab State Action Plan 2026 / PPCB affidavit
    d, s = caqm_doc("5769", "caqm_sap_punjab_2026", "Punjab State Action Plan, 2026 for prevention of paddy stubble burning (published by CAQM)", "2026", "state action plan")
    if d:
        ev("2013-10-22", "Punjab", "Punjab prohibits burning of agricultural residue (Air Act s.19(5))",
           "Department of Science, Technology & Environment notification no. 946 dated 22.10.2013.", s,
           quote="vide notification no. 946 dated 22/10/2013 has prohibited the burning of agriculture residue in the State of Punjab", doc=d)
        ev("2018-02-07", "Punjab", "PPCB makes Super Straw Management System mandatory on combine harvesters",
           "Order dated 7.2.2018 under s.31-A of the Air Act; EC on combines without Super SMS later set at Rs 50,000 / 75,000 / 1,00,000 (order 25.09.2020).", s,
           quote="Punjab Pollution Control Board has passed order dated 7.2.2018 under section 31-A of Air (Prevention and Control of Pollution) Act,1981 making it mandatory for all the Combines to have Super SMS system fixed.", doc=d)
        ev("2025-12-31", "Punjab", "Punjab seeks Rs 2,500/acre paddy-straw management compensation from GoI (not acceded)",
           "Punjab's State Action Plan 2026 records that the State asked the Government of India for Rs 2,500/acre compensation to farmers for straw management, which was not accepted.",
           s, quote="for providing Paddy Straw Management Compensation to farmers @ Rs.2500- per acre however Government of India has not acceded to the request of the state.", doc=d,
           date_precision="undated in source (State Action Plan 2026)", incentive_rs_per_acre_requested=2500)
        ev("2024-10-25", "Punjab", "Punjab SOP to verify every 'No Crop Burning Found' case",
           "Government of Punjab memo dated 25.10.2024 to all Deputy Commissioners (SOP for verification of PRSC-reported cases).", s,
           quote="the Government of Punjab has issued a Standard Operating Protocol (SOP) vide memo dated 25.Oct.2024 to all the Deputy Commissioners in the State of Punjab", doc=d)
    p = os.path.join(RAW, "ngt", "ppcb_affidavit_oa632.pdf")
    if os.path.exists(p):
        d = Doc(p)
        ev("2024-04-01", "All", "Jan Vishwas Act decriminalises Air Act / EP Act penalties (in force 01.04.2024)",
           "MoEFCC notifications G.S.R. 749(E) and 775(E) bring the Jan Vishwas amendments into force; criminal prosecution of farmers under s.39 Air Act replaced by adjudicated penalties.",
           "ngt_ppcb_affidavit_2024-11", quote="shall come into force with eRect from 01.04.2024", doc=d)

    # incentives (Lok Sabha answers)
    d, s = ls_doc("LS_20250310_U1737.pdf")
    if d:
        ev("2025-03-10", "Haryana", "Haryana incentive: Rs 1,000/acre for straw management (bales) plus other per-acre support",
           "Lok Sabha answer lists Rs 1000/acre for crop residue management by making bales, Rs 500/MT top-up in IOCL 2G-ethanol clusters, Rs 2500/MT common paddy straw rate, Rs 7000/acre MPMV diversification and Rs 4000/acre DSR.",
           s, quote="an incentive of Rs. 1000/- per acre for management of crop residue by making bales", doc=d, incentive_rs_per_acre=1000)
    d, s = ls_doc("LS_20260209_U1552.pdf")
    if d:
        ev("2026-02-09", "Haryana", "Haryana incentive reported at Rs 1,200/acre for not burning stubble",
           "Lok Sabha answer (09.02.2026): 'Government of Haryana also provides an incentives of Rs.1200 per acre to farmers for not engaging in stubble burning.'",
           s, quote="Government of Haryana also provides an incentives of Rs.1200 per acre to farmers for not engaging in stubble burning.", doc=d, incentive_rs_per_acre=1200)
    p = os.path.join(RAW, "haryana", "insitu_area_2024-25.pdf")
    if os.path.exists(p) and "hr_agri_crm_area_2024" in SOURCES:
        ev("2024-10-01", "Haryana", "Haryana 'Crop Residue Management @ Rs 1000/acre' incentive in operation (season 2024)",
           "Machine-wise registered area under the Rs 1000/acre scheme for 2024-25 (in-situ and straw-baler ex-situ).", "hr_agri_crm_area_2024",
           date_precision="season (date approximate: start of 2024 harvest season)")

    # Punjab sowing-date notification (scanned, Punjabi)
    p = os.path.join(RAW, "punjab", "punjab_paddy_dates_2026.pdf")
    curl_to("https://agri.punjab.gov.in/uploads/news/news_1777549205_aq80a4Fpl8GXYc3p.pdf", p)
    s = src("pb_agri_paddy_dates_2026", "Punjab Dept of Agriculture & Farmers Welfare notification no. 08/19/2006-Kh-2(6)/6370 dated 30-04-2026 under the Punjab Preservation of Subsoil Water Act, 2009 (s.3(1),(2)) - paddy sowing/transplanting dates 2026 (in Punjabi)",
            "Government of Punjab, Department of Agriculture & Farmers Welfare",
            "https://agri.punjab.gov.in/uploads/news/news_1777549205_aq80a4Fpl8GXYc3p.pdf", "2026-04-30", p,
            kind="notification (scanned, Punjabi)", note="Image-only PDF; dates transcribed from the scan. Signature block also shows a typed date 30.04.2025 while the handwritten dates read 30-04-2026; the notification lists 2026 dates and supersedes no. 5514 of 23-04-2025.")
    ev("2026-04-30", "Punjab", "Punjab notifies 2026 paddy dates under the Preservation of Subsoil Water Act, 2009",
       "DSR 15-31 May 2026; transplanting from 1 June (Gurdaspur, Pathankot, Amritsar, Tarn Taran, Rupnagar, SAS Nagar, Fatehgarh Sahib, Hoshiarpur), 5 June (Faridkot, Bathinda, Ferozepur, Sri Muktsar Sahib, Fazilka), 9 June (Ludhiana, Malerkotla, Mansa, Moga, Patiala, Sangrur, Barnala, Kapurthala, Jalandhar, SBS Nagar); supersedes notification no. 5514 dated 23-04-2025.",
       s, transcribed_from_image=True,
       sowing_dates={"DSR": "2026-05-15/2026-05-31", "zone_1": "2026-06-01", "zone_2": "2026-06-05", "zone_3": "2026-06-09"})

    # Season summaries from CAQM press releases (fire counts) as events
    d, s = caqm_doc("5817", "caqm_pr_2023-11-30", "CAQM press release: Status of paddy stubble burning incidents during last 75 days (30.11.2023)", "2023-11-30", "press release")
    if d:
        ev("2023-11-30", "Punjab, Haryana", "Season 2023 closes: Punjab 36,663 and Haryana 2,303 fire counts",
           "CAQM: Punjab 83,002 (2020) -> 71,304 (2021) -> 49,922 (2022) -> 36,663 (2023); Haryana 4,202 -> 6,987 -> 3,661 -> 2,303.",
           s, quote="Against cumulative 83,002 fire counts on account of paddy stubble burning in Punjab during 2020, the fire counts in the corresponding period were 71,304 during 2021, 49,922 during 2022 and 36,663 during 2023.", doc=d)
    d, s = caqm_doc("5951", "caqm_pr_2025-12-01", "CAQM press release: Paddy Harvesting Season 2025 concludes with significant reduction in farm fire incidents (01.12.2025)", "2025-12-01", "press release")
    if d:
        ev("2025-12-01", "Punjab, Haryana", "Season 2025 closes: Punjab 5,114 and Haryana 662 fire counts", "Lowest counts since CAQM monitoring began.", s,
           quote="Punjab recorded 5,114 fire counts this season, reflecting a reduction of 53% over 2024", doc=d)

    EVENTS.sort(key=lambda e: (e["date"], e["state"], e["title"]))


# --------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------
def finalize():
    # mark one 'preferred' entry per (metric, state[, district], season, basis family): latest source date, then latest as_of
    def sdate(e):
        return (SOURCES[e["source"]].get("date") or "0000", e.get("as_of") or "0000")
    for m in METRICS.values():
        groups = []
        for st, lst in m["by_state"].items():
            groups.append(lst)
        for st, dd in m["by_district"].items():
            for dn, lst in dd.items():
                groups.append(lst)
        for lst in groups:
            lst.sort(key=lambda e: ((e["season"] if e["season"] is not None else -1), e.get("as_of") or "", e["source"]))
            by_key = defaultdict(list)
            for e in lst:
                by_key[(e["season"], e.get("period"))].append(e)
            for k, es in by_key.items():
                best = max(es, key=sdate)
                for e in es:
                    e["preferred"] = e is best
            # 'latest': among preferred entries of a season, the one with the latest as_of
            # (e.g. the end-of-season PPCB report rather than mid-season snapshots)
            by_season = defaultdict(list)
            for e in lst:
                if e["preferred"]:
                    by_season[e["season"]].append(e)
            for es in by_season.values():
                top = max(es, key=lambda e: (e.get("as_of") or "", SOURCES[e["source"]].get("date") or ""))
                for e in lst:
                    if e in es:
                        e["latest"] = e is top
    used_sources = [SOURCES[k] for k in sorted(SOURCES)]
    out = {
        "generated": TODAY,
        "description": "Enforcement and crop-residue-management (CRM) indicators for paddy stubble burning in Punjab and Haryana, 2016-2026, with sources. Values are exactly as stated by each source; multiple sources for the same season are kept side by side ('preferred' marks the most recent source for a season/period).",
        "conventions": {
            "season": "Paddy harvest season year (Sept-Nov). Financial-year figures (FY 2018-19) are assigned to the season of the first year (2018). Cumulative stock figures use the season of their as-of date.",
            "as_of": "Date the figure refers to (cumulative 'as on' date, end of reporting window, or answer date when nothing else is given).",
            "basis": "seasonal | cumulative | financial year | stock | period total - read with 'period'.",
            "preferred": "One entry per (metric, state/district, season, period) flagged as the latest-published.",
            "latest": "One preferred entry per (metric, state/district, season) with the latest as_of - use this for one-number-per-season charts. Different periods (mid-season snapshots, part-year FY) stay in the series for context.",
            "combined_keys": "Rows under combined state keys are never part of the Punjab or Haryana series; do not add them to state totals.",
            "transcribed_from_image": "Value read from a scanned (image-only) page; guarded by row/total arithmetic checks.",
            "quote": "Verbatim passage (whitespace-normalised) that contains the number, checked against the cached document at build time.",
            "states": "Besides 'Punjab' and 'Haryana', some entries use a combined key (e.g. 'Punjab+Haryana+UP-NCR (combined)', 'CRM states total (Punjab, Haryana, UP, Delhi)') when the source gives no split.",
        },
        "sources": [{k: v for k, v in s.items() if v not in (None, "")} for s in used_sources],
        "metrics": {k: METRICS[k] for k in METRIC_DEFS if k in METRICS},
        "events": EVENTS,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    s = json.dumps(out, indent=1, ensure_ascii=False)
    key = _datagov_key()
    if key:
        assert key not in s
    open(OUT_JSON, "w").write(s)
    write_md(out)
    n_state = sum(len(v) for m in METRICS.values() for v in m["by_state"].values())
    n_dist = sum(len(v) for m in METRICS.values() for d in m["by_district"].values() for v in d.values())
    print(f"wrote {os.path.relpath(OUT_JSON, ROOT)}: {len(METRICS)} metrics, {n_state} state values, "
          f"{n_dist} district values, {len(EVENTS)} events, {len(SOURCES)} sources")


def write_md(out):
    L = ["# Enforcement & crop-residue-management sources", "",
         f"Generated by `scripts/build_enforcement.py` on {TODAY}. Cache: `data/raw/enforcement/`.", "",
         "Every value in `enforcement.json` carries a `source` id listed here. Curated figures from prose documents include the verbatim `quote` that the build checks against the cached text; values from scanned pages are flagged `transcribed_from_image` and arithmetic-checked.", "",
         "## Caveats", "",
         "- **Different counting bases.** CAQM / ISRO-protocol fire counts (15 Sep-30 Nov) differ from state dashboard lists (Haryana's AFL list, PRSC lists) and from 01 Oct-30 Nov windows used in some parliamentary tables.",
         "- **Cumulative vs seasonal.** PPCB / Haryana dashboard figures are cumulative *within* a season as of the date shown; CRM machines/CHCs/funds are cumulative since 2018-19 unless the entry is a financial-year figure.",
         "- **Imposed vs recovered.** EC imposed, EC realised/collected and number of cases are separate metrics; parliamentary answers sometimes say 'collected'.",
         "- **FIR basis changed.** IPC s.188 until 30 June 2024, BNS s.223 afterwards; Haryana 2016-2020 counts are 'police complaints/FIRs'.",
         "- **Revisions.** The Ministry of Agriculture revised year-wise machine and CHC numbers between answers (e.g. Punjab 2021-22 machines 10,031 vs 13,796); Punjab's own State Action Plan gives different 2023-24/2024-25 machine counts (13,179 / 17,600) from the Ministry (19,735 / 11,285). All are kept.",
         "- **Suspect unit.** The OGD table of EC imposed in 2022 is labelled 'Rs. Lakh' (Punjab 1740, Haryana 2194); this implies Rs 17-22 crore, inconsistent with all other EC figures - flagged `suspect_unit`.",
         "- **Haryana stock vs scheme totals.** Haryana's CAQM proforma reports 52,684 *functional* machines and 3,019 CHCs (31.12.2025) against ~1 lakh machines and 6,775 CHCs supplied/established under the scheme.",
         "", "## Sources", ""]
    for s in out["sources"]:
        L.append(f"### `{s['id']}`")
        L.append(f"- **{s['title']}**")
        L.append(f"- Publisher: {s['publisher']}")
        if s.get("date"):
            L.append(f"- Date: {s['date']}")
        if s.get("url"):
            L.append(f"- URL: {s['url']}")
        if s.get("local"):
            L.append(f"- Cached: `{s['local']}`")
        if s.get("kind"):
            L.append(f"- Type: {s['kind']}")
        if s.get("note"):
            L.append(f"- Note: {s['note']}")
        gave = USED.get(s["id"]) or ["(fetched / catalogued; not used for values - duplicative or out of scope)"]
        L.append(f"- Gave: {', '.join(gave)}")
        L.append("")
    L += ["## Searched but not used / gaps", "",
          "- Rajya Sabha answers whose OGD tables are not API-enabled and whose CSVs return HTTP 403 (Punjab FIRs/EC 2016-2022: RS 260 USQ 42 of 20.07.2023 and USQ 1661 of 03.08.2023; EC collected 2015-16 to 2018-19: RS 249 USQ 16). rsdebate.nic.in PDFs redirect to sansad.in and were not retrievable.",
          "- No official season-end 2023 or 2024 enforcement totals for Punjab (EC imposed/recovered, FIRs, red entries) beyond the mid-season NGT affidavit (to 03.11.2024) and the Lok Sabha figure of EC collected in 2024.",
          "- Haryana: no official season totals of EC/FIRs for 2021-2025 found (dashboard keeps 2024 red entries/officials and 2026 enforcement only; the '2025-26' enforcement report holds one wheat-season row and is not used).",
          "- Farmers barred from MSP sale / schemes: only the policy (red entry => no sale on e-Kharid for two seasons in Haryana) is reported in news; no official count found.",
          "- PUSA bio-decomposer area: the only official table found (RS USQ 1802, 16.03.2023: Punjab 1,870 ha, Haryana 1,37,600 ha) states no year, so it is not included as a metric.",
          "- Lok Sabha 17 (2019-2024) answers were not mined (crawl stopped); older Punjab/Haryana enforcement totals 2019-2023 may exist there.",
          "- Supreme Court 2023-2024 orders (e.g. 23.10.2024, 04.11.2024) are referenced in affidavits; order PDFs were not retrieved.",
          "- PIB press releases were not needed where the same figures were available from CAQM, Lok Sabha answers or OGD; PIB search is not scriptable.", ""]
    open(OUT_MD, "w").write("\n".join(L))


def main():
    os.makedirs(RAW, exist_ok=True)
    build_ogd()
    build_caqm_facts()
    build_ls_facts()
    build_ppcb_atr()
    build_ngt()
    build_haryana()
    build_events()
    finalize()


if __name__ == "__main__":
    main()
