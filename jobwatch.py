#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print new matching jobs."""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlparse

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


def make_fetcher(config):
    """Build fetch_board(board) -> (name, jobs, error) from a loaded config.

    Also applies the config's process-wide adapter knobs (`request_delay_scale`,
    `workday_pod_limit`, both required). Call it once, before any fetch: Workday's
    per-pod semaphores capture POD_LIMIT the first time each pod is used.

    The returned fetch_board never raises, and fills config-level defaults into the
    board dict: `query` (from `query_terms`), `resolve_multi_location`, `title_ok`.
    """
    common.DELAY_SCALE = config["request_delay_scale"]
    workday_mod.POD_LIMIT = config["workday_pod_limit"]

    defaults = {}
    if config.get("query_terms"):
        defaults["query"] = config["query_terms"]
    if config.get("resolve_multi_location"):
        defaults["resolve_multi_location"] = True
    filters = config.get("filters", {})
    # Advisory hint: ignoring it is always correct — matches() still runs in
    # full, so this can only skip work, never surface a wrong job.
    defaults["title_ok"] = lambda t: _title_matches(t, filters)

    def fetch_board(board):
        name = board.get("name", board.get("url", "?"))
        kind = board.get("type", "workday")
        adapter = ADAPTERS.get(kind)
        if not adapter:
            return name, [], f"no adapter for type '{kind}'"
        try:
            return name, adapter({**defaults, **board}), None
        except Exception as e:
            return name, [], str(e)

    return fetch_board


# Rough seconds per board by type, measured on a full run. Only the ordering matters.
_TYPE_WEIGHT = {"avature": 40, "yello": 35, "workday": 30, "radancy": 17, "oracle": 15,
                "successfactors": 15, "eightfold": 8, "smartrecruiters": 7, "icims": 5,
                "dayforce": 3, "phenom": 2}


def _lane(board):
    """Workday boards queue per pod (one rate limit each); everything else per type."""
    kind = board.get("type", "workday")
    if kind == "workday":
        return workday_mod._pod(urlparse(board.get("url", "")).netloc)
    return kind


def _schedule(boards):
    """Submission order: round-robin across lanes, heaviest lane first. Every pod and
    every slow type starts in the first round instead of waiting behind its config
    group, and no pod gets a run of workers stuck on its semaphore."""
    lanes = {}
    for b in boards:
        lanes.setdefault(_lane(b), []).append(b)
    order = sorted(lanes.values(), reverse=True,
                   key=lambda ls: sum(_TYPE_WEIGHT.get(b.get("type", "workday"), 1)
                                      for b in ls))
    return [b for rnd in zip_longest(*order) for b in rnd if b is not None]


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


@dataclass
class RunResult:
    new_jobs: list   # unseen jobs to show, sorted by (company, title)
    failed: list     # [(board name, error)], sorted
    seen: set        # seen ids to persist after this run


def run(boards, fetch, seen, filters, *, workers, raw=False, on_board=None):
    """Fetch every board concurrently, then diff against `seen` and filter.

    A job is new if its id is in neither `seen` nor earlier in this run. A normal
    run shows new jobs that pass `filters` and marks only those seen — so loosening
    a filter later still surfaces what it used to hide. `raw` shows and marks seen
    every new job. `seen` itself is not mutated.

    `on_board(done, total, name, elapsed, jobs, error)` is called per board as it
    completes, from the calling thread.
    """
    def timed(board):
        start = time.monotonic()
        return fetch(board), time.monotonic() - start

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(timed, b) for b in _schedule(boards)]
        for done, fut in enumerate(as_completed(futures), 1):
            (name, jobs, error), elapsed = fut.result()
            results.append((name, jobs, error))
            if on_board:
                on_board(done, len(futures), name, elapsed, jobs, error)

    # Diff + filter sequentially — keeps `seen` mutation single-threaded.
    seen_after, shown, new_jobs, failed = set(seen), set(), [], []
    for name, jobs, error in results:
        if error:
            failed.append((name, error))
            continue
        for job in jobs:
            if job["id"] in seen or job["id"] in shown:
                continue
            if raw or matches(job, filters):
                shown.add(job["id"])
                new_jobs.append(job)
                seen_after.add(job["id"])

    new_jobs.sort(key=lambda j: (j["company"], j["title"]))
    return RunResult(new_jobs=new_jobs, failed=sorted(failed), seen=seen_after)


def report_lines(result):
    """The new-jobs block, unindented. Shared by stdout and the saved log."""
    if not result.new_jobs:
        return ["No new matching jobs."]
    lines = [f"{len(result.new_jobs)} new matching job(s):", ""]
    for job in result.new_jobs:
        lines += [f"• {job['title']}",
                  f"    {job['company']} — {job['location']}  ({job['posted']})",
                  f"    {job['url']}",
                  ""]
    return lines


def main():
    if not CONFIG_PATH.exists():
        sys.exit(
            f"No config found. Copy config.example.json to {CONFIG_PATH.name} and add your boards."
        )

    args = _parse_args()
    config = load_json(CONFIG_PATH, {})
    boards = config.get("boards", [])
    workers = config["max_workers"]
    fetch = make_fetcher(config)
    seen = set(load_json(SEEN_PATH, []))

    if args.board:
        needle = args.board.lower()
        boards = [b for b in boards if needle in b.get("name", "").lower()
                  or needle in b.get("url", "").lower()]
    if args.type:
        boards = [b for b in boards if b.get("type", "").lower() == args.type.lower()]

    if not boards:
        sys.exit("No boards matched the filter.")

    def progress(done, total, name, elapsed, jobs, error):
        status = f"{len(jobs)} jobs" if error is None else f"ERROR: {_short_error(error)}"
        print(f"  [{done:2}/{total}] {name:32} {elapsed:5.1f}s  {status}", flush=True)

    total = len(boards)
    print(f"Fetching {total} board(s) (up to {workers} at a time)...", flush=True)
    result = run(boards, fetch, seen, config.get("filters", {}),
                 workers=workers, raw=args.raw, on_board=progress)

    for name, error in result.failed:
        print(f"  ! {name}: {error}", file=sys.stderr)
    # Restate failures on stdout — stderr goes elsewhere in a redirected run.
    if result.failed:
        print(f"\n  {len(result.failed)} board(s) failed: "
              f"{', '.join(n for n, _ in result.failed)}")

    body = report_lines(result)
    print()
    for line in body:
        print(f"  {line}" if line else "")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = OUTPUT_DIR / f"jobwatch-{stamp}.out"

    flag_parts = []
    if args.board:
        flag_parts.append(f"--board {args.board}")
    if args.type:
        flag_parts.append(f"--type {args.type}")
    if args.raw:
        flag_parts.append("--raw")
    lines = [f"Run: {stamp}  |  Boards: {total}  |  Flags: {', '.join(flag_parts) or 'none'}"]
    if result.failed:
        lines.append(f"\n{len(result.failed)} board(s) failed:")
        lines += [f"  ! {name}: {error}" for name, error in result.failed]
    lines += [""] + body

    out_path.write_text("\n".join(lines) + "\n")
    print(f"  Saved to {out_path.relative_to(HERE)}")

    SEEN_PATH.write_text(json.dumps(sorted(result.seen), indent=0))


if __name__ == "__main__":
    main()
