#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print newly-posted jobs that match your filters.

Run it whenever you like:  python3 jobwatch.py
It remembers which jobs it has already shown you (seen.json), so each run only
prints what's NEW since last time.

Currently supports Workday boards. Other ATS types (Greenhouse, Lever, Ashby)
plug in as small adapters following the same shape — see ADAPTERS at the bottom.
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

from adapters.ashby import fetch_ashby
from adapters.common import BROWSER_UA
from adapters.greenhouse import fetch_greenhouse
from adapters.lever import fetch_lever
from adapters.oracle import fetch_oracle
from adapters.phenom import fetch_phenom
from adapters.radancy import fetch_radancy
from adapters.smartrecruiters import fetch_smartrecruiters
from adapters.successfactors import fetch_successfactors
from adapters.workday import fetch_workday

MAX_WORKERS = 8  # how many boards to fetch at once

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_PATH = HERE / "seen.json"


# --------------------------------------------------------------------------- #
# Normalized job shape every adapter returns:
#   { "id", "title", "location", "posted", "url", "company" }
# --------------------------------------------------------------------------- #


def fetch_bamboohr(board):
    """BambooHR public careers API.
    URL like https://<sub>.bamboohr.com/careers -> uses that host."""
    host = urlparse(board["url"]).netloc
    sub = host.split(".")[0]
    company = board.get("name", sub)
    r = requests.get(f"https://{host}/careers/list",
                     headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json().get("result", []):
        loc = p.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        jid = p.get("id")
        jobs.append({
            "id": f"bamboo:{sub}:{jid}",
            "title": (p.get("jobOpeningName") or "").strip(),
            "location": location or ("Remote" if p.get("isRemote") else ""),
            "posted": "",
            "url": f"https://{host}/careers/{jid}",
            "company": company,
        })
    return jobs


def fetch_rippling(board):
    """Rippling ATS public board API.
    URL like https://ats.rippling.com/<slug>/jobs -> slug."""
    segs = [s for s in urlparse(board["url"]).path.split("/") if s]
    slug = board.get("board") or (segs[0] if segs else None)
    company = board.get("name", slug)
    api = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    r = requests.get(api, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json():
        jobs.append({
            "id": f"rippling:{slug}:{p.get('uuid')}",
            "title": (p.get("name") or "").strip(),
            "location": (p.get("workLocation") or {}).get("label", ""),
            "posted": "",
            "url": p.get("url", board["url"]),
            "company": company,
        })
    return jobs


def fetch_ukg(board):
    """UKG Pro Recruiting (UltiPro) public job board API.
    URL like https://recruiting.ultipro.ca/<TENANT>/JobBoard/<guid>/ -> both."""
    parsed = urlparse(board["url"])
    host = parsed.netloc
    segs = [s for s in parsed.path.split("/") if s]
    tenant = segs[0]
    guid = segs[segs.index("JobBoard") + 1] if "JobBoard" in segs else segs[2]
    company = board.get("name", tenant)
    api = (f"https://{host}/{tenant}/JobBoard/{guid}/JobBoardView/LoadSearchResults")
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json",
               "Content-Type": "application/json"}

    jobs, skip, total = [], 0, None
    while True:
        body = {"opportunitySearch": {"Top": 100, "Skip": skip, "QueryString": "",
                                      "OrderBy": [], "Filters": []}}
        r = requests.post(api, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        opps = data.get("opportunities", [])
        for p in opps:
            locs = "; ".join(l.get("LocalizedDescription", "") for l in p.get("Locations", []))
            jid = p.get("Id")
            jobs.append({
                "id": f"ukg:{tenant}:{jid}",
                "title": (p.get("Title") or "").strip(),
                "location": locs,
                "posted": (p.get("PostedDate") or "")[:10],
                "url": f"https://{host}/{tenant}/JobBoard/{guid}/OpportunityDetail?opportunityId={jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalCount", 0)
        skip += 100
        if not opps or skip >= total:
            break
        time.sleep(0.3)
    return jobs


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def _word_match(keyword, text):
    """True if keyword appears as a whole word in text. Allows the plural/`-ship`
    forms (intern -> interns/internship) but NOT longer unrelated words, so
    "intern" matches "Internship" but not "Internal"/"International"."""
    pat = r"\b" + re.escape(keyword) + r"(s|ship|ships)?\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def _keyword_match(keyword, text):
    """Match one filter keyword against a title. A keyword starting with `re:` is
    treated as a raw, case-sensitive regex (used for things like a trailing
    entry-level roman numeral, `re:\\bI{1,2}\\b`); otherwise it's a whole-word,
    case-insensitive match."""
    if keyword.startswith("re:"):
        return re.search(keyword[3:], text) is not None
    return _word_match(keyword, text)


def matches(job, filters):
    title = job["title"]
    loc = job["location"].lower()

    # title_groups: a list of keyword-lists. The title must match at least one
    # keyword in EVERY group (AND across groups, OR within a group). Use this to
    # require e.g. (early-career) AND (tech domain). Falls back to the simpler
    # title_any (a single OR group) if title_groups isn't set.
    groups = filters.get("title_groups")
    if groups:
        for group in groups:
            if not any(_keyword_match(k, title) for k in group):
                return False
    else:
        title_any = filters.get("title_any", [])
        if title_any and not any(_keyword_match(k, title) for k in title_any):
            return False

    title_none = filters.get("title_none", [])
    if any(_keyword_match(k, title) for k in title_none):
        return False

    loc_any = [k.lower() for k in filters.get("location_any", [])]
    if loc_any and not any(k in loc for k in loc_any):
        return False

    return True


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #

ADAPTERS = {
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "phenom": fetch_phenom,
    "successfactors": fetch_successfactors,
    "oracle": fetch_oracle,
    "radancy": fetch_radancy,
    "smartrecruiters": fetch_smartrecruiters,
    "bamboohr": fetch_bamboohr,
    "rippling": fetch_rippling,
    "ukg": fetch_ukg,
}


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def fetch_board(board):
    """Fetch one board. Returns (name, jobs, error) — never raises, so one bad
    board can't sink the whole run. Safe to call from worker threads."""
    name = board.get("name", board.get("url", "?"))
    kind = board.get("type", "workday")
    adapter = ADAPTERS.get(kind)
    if not adapter:
        return name, [], f"no adapter for type '{kind}'"
    try:
        return name, adapter(board), None
    except Exception as e:
        return name, [], str(e)


def main():
    if not CONFIG_PATH.exists():
        sys.exit(
            f"No config found. Copy config.example.json to {CONFIG_PATH.name} and add your boards."
        )

    config = load_json(CONFIG_PATH, {})
    filters = config.get("filters", {})
    boards = config.get("boards", [])
    workers = config.get("max_workers", MAX_WORKERS)
    seen = set(load_json(SEEN_PATH, []))

    # Fetch every board concurrently; total time ~= the slowest single board.
    total = len(boards)
    print(f"Fetching {total} boards (up to {workers} at a time)...", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_board, b) for b in boards]
        for done, fut in enumerate(as_completed(futures), 1):
            name, jobs, error = fut.result()
            results.append((name, jobs, error))
            status = f"{len(jobs)} jobs" if error is None else f"ERROR: {error[:50]}"
            print(f"  [{done:2}/{total}] {name:32} {status}", flush=True)

    # Diff + filter sequentially (fast, and keeps `seen` mutation single-threaded).
    new_jobs = []
    for name, jobs, error in results:
        if error:
            print(f"  ! {name}: {error}", file=sys.stderr)
            continue
        for job in jobs:
            if job["id"] in seen:
                continue
            seen.add(job["id"])  # mark seen even if filtered out, so it stays quiet next time
            if matches(job, filters):
                new_jobs.append(job)

    if new_jobs:
        print(f"\n  {len(new_jobs)} new matching job(s):\n")
        for job in sorted(new_jobs, key=lambda j: (j["company"], j["title"])):
            print(f"  • {job['title']}")
            print(f"      {job['company']} — {job['location']}  ({job['posted']})")
            print(f"      {job['url']}\n")
    else:
        print("  No new matching jobs.")

    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
