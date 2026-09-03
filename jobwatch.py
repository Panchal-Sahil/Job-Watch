#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print new matching jobs."""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
from adapters.yello import fetch_yello
from adapters.zohorecruit import fetch_zohorecruit

# I/O-bound threads; past ~32 the run is bound by the slowest board.
MAX_WORKERS = 32

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_PATH = HERE / "seen.json"
OUTPUT_DIR = HERE / "output" / "jobwatch"


def _word_match(keyword, text):
    """Whole-word match allowing plural/-ship (intern -> interns/internship)."""
    pat = r"\b" + re.escape(keyword) + r"(s|ship|ships)?\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def _location_match(keyword, text):
    # Boundary on alphanumeric ends only — ", pe" matched "Peoria", ", ab" matched "Abu Dhabi".
    pat = re.escape(keyword)
    if keyword[:1].isalnum():
        pat = r"\b" + pat
    if keyword[-1:].isalnum():
        pat = pat + r"\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def _keyword_match(keyword, text):
    # re: prefix = raw case-sensitive regex; otherwise whole-word.
    if keyword.startswith("re:"):
        return re.search(keyword[3:], text) is not None
    return _word_match(keyword, text)


def _title_matches(title, filters):
    """Title half of matches(). Safe for adapters to skip location resolution
    when this returns False — matches() will reject regardless."""
    # title_groups: AND across groups, OR within each group.
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

    # location_none with location_rescue: drop foreign postings but rescue
    # ones also naming Canada ("Remote (United States | Canada)").
    loc_none = [k.lower() for k in filters.get("location_none", [])]
    if loc_none and any(_location_match(k, loc) for k in loc_none):
        loc_rescue = [k.lower() for k in filters.get("location_rescue", [])]
        if not any(_location_match(c, loc) for c in loc_rescue):
            return False

    loc_any = [k.lower() for k in filters.get("location_any", [])]
    if loc_any and not any(_location_match(k, loc) for k in loc_any):
        return False

    return True

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
    "yello": fetch_yello,
    "zohorecruit": fetch_zohorecruit,
}


def load_json(path, default):
    if path.exists():
        text = path.read_text().strip()
        if not text:  # empty file from interrupted write
            return default
        return json.loads(text)
    return default


_DEFAULT_QUERY = None


def _default_query_terms():
    """Early-careers search terms from config, cached. Also used by probe --add."""
    global _DEFAULT_QUERY
    if _DEFAULT_QUERY is None:
        _DEFAULT_QUERY = load_json(CONFIG_PATH, {}).get("query_terms", [])
    return _DEFAULT_QUERY


_RESOLVE_MULTI_LOC = None


def _resolve_multi_location():
    """Whether to resolve placeholder locations ("2 Locations") via detail endpoint."""
    global _RESOLVE_MULTI_LOC
    if _RESOLVE_MULTI_LOC is None:
        _RESOLVE_MULTI_LOC = bool(load_json(CONFIG_PATH, {}).get("resolve_multi_location", False))
    return _RESOLVE_MULTI_LOC


_FILTERS = None


def _filters():
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = load_json(CONFIG_PATH, {}).get("filters", {})
    return _FILTERS


def fetch_board(board):
    """Fetch one board. Returns (name, jobs, error) — never raises."""
    name = board.get("name", board.get("url", "?"))
    kind = board.get("type", "workday")
    adapter = ADAPTERS.get(kind)
    if not adapter:
        return name, [], f"no adapter for type '{kind}'"
    if "query" not in board and _default_query_terms():
        board = {**board, "query": _default_query_terms()}
    if "resolve_multi_location" not in board and _resolve_multi_location():
        board = {**board, "resolve_multi_location": True}
    # Advisory hint: ignoring it is always correct — matches() still runs in
    # full, so this can only skip work, never surface a wrong job.
    if "title_ok" not in board:
        board = {**board, "title_ok": lambda t: _title_matches(t, _filters())}
    try:
        return name, adapter(board), None
    except Exception as e:
        return name, [], str(e)


def _short_error(error, width=140):
    # Head+tail: urllib3 puts host at front, cause at back.
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
    common.DELAY_SCALE = config.get("request_delay_scale", common.DELAY_SCALE)
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

    # Diff + filter sequentially — keeps `seen` mutation single-threaded.
    new_jobs = []
    failed = []
    for name, jobs, error in results:
        if error:
            failed.append((name, error))
            print(f"  ! {name}: {error}", file=sys.stderr)
            continue
        for job in jobs:
            if job["id"] in seen:
                continue
            if args.raw or matches(job, filters):
                seen.add(job["id"])
                new_jobs.append(job)

    # Restate failures on stdout — stderr goes elsewhere in a redirected run.
    if failed:
        print(f"\n  {len(failed)} board(s) failed: {', '.join(sorted(n for n, _ in failed))}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = OUTPUT_DIR / f"jobwatch-{stamp}.out"

    lines = []

    flag_parts = []
    if args.board:
        flag_parts.append(f"--board {args.board}")
    if args.type:
        flag_parts.append(f"--type {args.type}")
    if args.raw:
        flag_parts.append("--raw")
    lines.append(f"Run: {stamp}  |  Boards: {total}  |  Flags: {', '.join(flag_parts) or 'none'}")
    if failed:
        lines.append(f"\n{len(failed)} board(s) failed:")
        for name, error in sorted(failed):
            lines.append(f"  ! {name}: {error}")
    lines.append("")

    if new_jobs:
        print(f"\n  {len(new_jobs)} new matching job(s):\n")
        lines.append(f"{len(new_jobs)} new matching job(s):\n")
        for job in sorted(new_jobs, key=lambda j: (j["company"], j["title"])):
            print(f"  • {job['title']}")
            print(f"      {job['company']} — {job['location']}  ({job['posted']})")
            print(f"      {job['url']}\n")
            lines.append(f"• {job['title']}")
            lines.append(f"    {job['company']} — {job['location']}  ({job['posted']})")
            lines.append(f"    {job['url']}\n")
    else:
        print("  No new matching jobs.")
        lines.append("No new matching jobs.")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"  Saved to {out_path.relative_to(HERE)}")

    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
