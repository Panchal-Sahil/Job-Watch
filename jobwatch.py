#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print newly-posted jobs that match your filters.

Run it whenever you like:  python3 jobwatch.py
It remembers which jobs it has already shown you (seen.json), so each run only
prints what's NEW since last time.

Supports 15 ATS platforms. Each one is a small adapter in the `adapters/`
package (`adapters/<platform>.py`) that returns the normalized job shape; the
adapters are wired into the `ADAPTERS` registry below.
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from adapters.ashby import fetch_ashby
from adapters.bamboohr import fetch_bamboohr
from adapters.dayforce import fetch_dayforce
from adapters.eightfold import fetch_eightfold
from adapters.greenhouse import fetch_greenhouse
from adapters.icims import fetch_icims
from adapters.lever import fetch_lever
from adapters.oracle import fetch_oracle
from adapters.phenom import fetch_phenom
from adapters.radancy import fetch_radancy
from adapters.ripplematch import fetch_ripplematch
from adapters.rippling import fetch_rippling
from adapters.smartrecruiters import fetch_smartrecruiters
from adapters.successfactors import fetch_successfactors
from adapters.ukg import fetch_ukg
from adapters.workday import fetch_workday

MAX_WORKERS = 8  # how many boards to fetch at once

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_PATH = HERE / "seen.json"


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

    # location_none: drop foreign postings (e.g. "Richmond, VA, United States"),
    # but rescue ones that ALSO name Canada — remote roles often read
    # "Remote (United States | Canada)" and should be kept. The Canada signals
    # live in config.json under "location_rescue".
    loc_none = [k.lower() for k in filters.get("location_none", [])]
    if loc_none and any(k in loc for k in loc_none):
        loc_rescue = [k.lower() for k in filters.get("location_rescue", [])]
        if not any(c in loc for c in loc_rescue):
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
    "ripplematch": fetch_ripplematch,
    "ukg": fetch_ukg,
    "dayforce": fetch_dayforce,
    "icims": fetch_icims,
    "eightfold": fetch_eightfold,
}


def load_json(path, default):
    if path.exists():
        # An empty/whitespace file (e.g. a run interrupted mid-write of seen.json)
        # would crash json.loads — treat it as "nothing yet" and fall back. Genuinely
        # malformed JSON still raises, so a corrupt config.json fails loudly.
        text = path.read_text().strip()
        if not text:
            return default
        return json.loads(text)
    return default


_DEFAULT_QUERY = None


def _default_query_terms():
    """Keyword-driven adapters (Phenom, Eightfold) narrow a big company board to
    early-careers roles using these search terms. They live in one place —
    config.json's top-level `query_terms` — so they're not duplicated per adapter.
    Cached, and read here (not just in main()) so probe.py's --add verify uses
    the same terms a real run would."""
    global _DEFAULT_QUERY
    if _DEFAULT_QUERY is None:
        _DEFAULT_QUERY = load_json(CONFIG_PATH, {}).get("query_terms", [])
    return _DEFAULT_QUERY


_RESOLVE_MULTI_LOC = None


def _resolve_multi_location():
    """Top-level config flag. When true, adapters that get a placeholder location
    like Workday's "2 Locations" resolve it to the real city names via the per-job
    detail endpoint. Centralized in config.json so the behavior is one switch."""
    global _RESOLVE_MULTI_LOC
    if _RESOLVE_MULTI_LOC is None:
        _RESOLVE_MULTI_LOC = bool(load_json(CONFIG_PATH, {}).get("resolve_multi_location", False))
    return _RESOLVE_MULTI_LOC


def fetch_board(board):
    """Fetch one board. Returns (name, jobs, error) — never raises, so one bad
    board can't sink the whole run. Safe to call from worker threads."""
    name = board.get("name", board.get("url", "?"))
    kind = board.get("type", "workday")
    adapter = ADAPTERS.get(kind)
    if not adapter:
        return name, [], f"no adapter for type '{kind}'"
    # Supply the shared early-careers search terms unless the board pins its own.
    if "query" not in board and _default_query_terms():
        board = {**board, "query": _default_query_terms()}
    # Supply the global multi-location resolution flag unless the board pins its own,
    # so the policy lives in config.json (top-level) but stays per-board overridable.
    if "resolve_multi_location" not in board and _resolve_multi_location():
        board = {**board, "resolve_multi_location": True}
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
            # Only remember jobs we actually surface. Filtered-out jobs are left
            # unseen so that loosening filters later can still catch them (they
            # stay silent until they match, so this adds no output noise).
            if matches(job, filters):
                seen.add(job["id"])
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
