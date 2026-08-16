#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print newly-posted jobs that match your filters.

Run it whenever you like:  python3 jobwatch.py
It remembers which jobs it has already shown you (seen.json), so each run only
prints what's NEW since last time.

Supports 18 ATS platforms. Each one is a small adapter in the `adapters/`
package (`adapters/<platform>.py`) that returns the normalized job shape; the
adapters are wired into the `ADAPTERS` registry below.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from adapters import common
from adapters.ashby import fetch_ashby
from adapters.avature import fetch_avature
from adapters.bamboohr import fetch_bamboohr
from adapters.dayforce import fetch_dayforce
from adapters.eightfold import fetch_eightfold
from adapters.greenhouse import fetch_greenhouse
from adapters.icims import fetch_icims
from adapters.jazzhr import fetch_jazzhr
from adapters.jobvite import fetch_jobvite
from adapters.lever import fetch_lever
from adapters.oracle import fetch_oracle
from adapters.phenom import fetch_phenom
from adapters.radancy import fetch_radancy
from adapters.ripplematch import fetch_ripplematch
from adapters.rippling import fetch_rippling
from adapters.smartrecruiters import fetch_smartrecruiters
from adapters.successfactors import fetch_successfactors
from adapters.ukg import fetch_ukg
from adapters import workday as workday_mod
from adapters.workday import fetch_workday
from adapters.gem import fetch_gem
from adapters.workable import fetch_workable

# How many boards to fetch at once. The threads are almost entirely idle waiting on
# network, so this can run well above the core count; past ~32 the run is bound by the
# slowest single board rather than by throughput. Override with `max_workers` in config.
# (The Workday adapter fans its own paging out over a second, smaller shared pool —
# see adapters/workday._PAGE_POOL — so peak threads are this plus that pool.)
MAX_WORKERS = 32

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


def _location_match(keyword, text):
    """Match one location keyword against a location string.

    Locations are free-form ("Toronto, ON, Canada", "Abu Dhabi, Abu Dhabi, ae"),
    so this stays a substring test rather than a whole-word one — but the match
    must not run *into* a longer word. Without that, the two-letter province
    codes swallow city names: ", pe" (Prince Edward Island) matched "East
    Peoria, Illinois" and rescued US-only Caterpillar postings that
    location_none had correctly caught, and ", ab" (Alberta) matched "Abu
    Dhabi". A boundary is only required on an end that is alphanumeric, so
    punctuation keywords like "u.s." (often title-final) still match."""
    pat = re.escape(keyword)
    if keyword[:1].isalnum():
        pat = r"\b" + pat
    if keyword[-1:].isalnum():
        pat = pat + r"\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def _keyword_match(keyword, text):
    """Match one filter keyword against a title. A keyword starting with `re:` is
    treated as a raw, case-sensitive regex (used for things like a trailing
    entry-level roman numeral, `re:\\bI{1,2}\\b`); otherwise it's a whole-word,
    case-insensitive match."""
    if keyword.startswith("re:"):
        return re.search(keyword[3:], text) is not None
    return _word_match(keyword, text)


def _title_matches(title, filters):
    """The title half of `matches()`, on its own so adapters can consult it before
    doing expensive per-job work. A job whose title fails here is dropped by
    `matches()` no matter what its location turns out to be — which is what makes
    it safe for an adapter to skip resolving that location (see fetch_board)."""
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

    return True


def matches(job, filters):
    loc = job["location"].lower()

    if not _title_matches(job["title"], filters):
        return False

    # location_none: drop foreign postings (e.g. "Richmond, VA, United States"),
    # but rescue ones that ALSO name Canada — remote roles often read
    # "Remote (United States | Canada)" and should be kept. The Canada signals
    # live in config.json under "location_rescue".
    loc_none = [k.lower() for k in filters.get("location_none", [])]
    if loc_none and any(_location_match(k, loc) for k in loc_none):
        loc_rescue = [k.lower() for k in filters.get("location_rescue", [])]
        if not any(_location_match(c, loc) for c in loc_rescue):
            return False

    loc_any = [k.lower() for k in filters.get("location_any", [])]
    if loc_any and not any(_location_match(k, loc) for k in loc_any):
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
    "jazzhr": fetch_jazzhr,
    "jobvite": fetch_jobvite,
    "eightfold": fetch_eightfold,
    "avature": fetch_avature,
    "gem": fetch_gem,
    "workable": fetch_workable,
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


_FILTERS = None


def _filters():
    """The `filters` object from config. Read here (not just in main()) so that
    fetch_board can hand adapters the title predicate — see `title_ok` below."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = load_json(CONFIG_PATH, {}).get("filters", {})
    return _FILTERS


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
    # An advisory hint, not part of the adapter contract: an adapter that is about
    # to spend a request enriching one job can ask whether its title stands any
    # chance of surviving the filter. Ignoring it is always correct — matches()
    # still runs in full below — so this can only skip work, never surface a job
    # that wouldn't otherwise appear.
    if "title_ok" not in board:
        board = {**board, "title_ok": lambda t: _title_matches(t, _filters())}
    try:
        return name, adapter(board), None
    except Exception as e:
        return name, [], str(e)


def _short_error(error, width=140):
    """One-line, bounded rendering of a board error for the progress log.

    The untruncated text also goes to stderr, but a run is normally redirected
    (`python3 jobwatch.py > jobwatch.out`), which sends stderr somewhere else — so
    in practice this line is the only record. Keeping the head *and* the tail is
    the point: urllib3 puts the host at the front and the actual cause ("Read timed
    out", "Connection reset by peer") at the back, so a head-only cut rendered a
    transient timeout and a permanently closed board as the same useless prefix.
    """
    text = " ".join(str(error).split())
    if len(text) <= width:
        return text
    keep = (width - 3) // 2
    return f"{text[:keep]}...{text[-keep:]}"


def _parse_args():
    p = argparse.ArgumentParser(description="Poll ATS boards and print new matching jobs.")
    p.add_argument("--board", "-b", help="Only fetch boards whose name contains this string (case-insensitive)")
    p.add_argument("--type", "-t", help="Only fetch boards of this ATS type (e.g. greenhouse, workday)")
    p.add_argument("--raw", action="store_true", help="Skip filtering — show all jobs, not just matches")
    return p.parse_args()


def main():
    if not CONFIG_PATH.exists():
        sys.exit(
            f"No config found. Copy config.example.json to {CONFIG_PATH.name} and add your boards."
        )

    args = _parse_args()
    config = load_json(CONFIG_PATH, {})
    filters = config.get("filters", {})
    boards = config.get("boards", [])
    workers = config.get("max_workers", MAX_WORKERS)
    # Scales every adapter's inter-page pause; see adapters/common.polite_sleep.
    common.DELAY_SCALE = config.get("request_delay_scale", common.DELAY_SCALE)
    # Per-pod board concurrency for Workday; see adapters/workday.POD_LIMIT.
    workday_mod.POD_LIMIT = config.get("workday_pod_limit", workday_mod.POD_LIMIT)
    seen = set(load_json(SEEN_PATH, []))

    if args.board:
        needle = args.board.lower()
        boards = [b for b in boards if needle in b.get("name", "").lower()
                  or needle in b.get("url", "").lower()]
    if args.type:
        boards = [b for b in boards if b.get("type", "").lower() == args.type.lower()]

    if not boards:
        sys.exit("No boards matched the filter.")

    # Fetch every board concurrently; total time ~= the slowest single board.
    total = len(boards)
    print(f"Fetching {total} board(s) (up to {workers} at a time)...", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_board, b) for b in boards]
        for done, fut in enumerate(as_completed(futures), 1):
            name, jobs, error = fut.result()
            results.append((name, jobs, error))
            status = (f"{len(jobs)} jobs" if error is None
                      else f"ERROR: {_short_error(error)}")
            print(f"  [{done:2}/{total}] {name:32} {status}", flush=True)

    # Diff + filter sequentially (fast, and keeps `seen` mutation single-threaded).
    new_jobs = []
    failed = []
    for name, jobs, error in results:
        if error:
            failed.append(name)
            print(f"  ! {name}: {error}", file=sys.stderr)
            continue
        for job in jobs:
            if job["id"] in seen:
                continue
            if args.raw or matches(job, filters):
                seen.add(job["id"])
                new_jobs.append(job)

    # Restate the failures on stdout. They already scrolled past in the progress
    # list, and the detail went to stderr, so a redirected run had no one place
    # that answered "did more boards than usual fail this time?".
    if failed:
        print(f"\n  {len(failed)} board(s) failed: {', '.join(sorted(failed))}")

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
