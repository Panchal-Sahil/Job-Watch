#!/usr/bin/env python3
"""discover — find career boards for companies you don't yet track.

Given a list of company names, generates plausible board slugs and checks
them against the Greenhouse, Lever, and Ashby public APIs.  Skips names
already in config.json.  Output is probe.py-compatible so hits can be
piped straight into --add.

    python3 discover.py companies.txt            # scan and report
    python3 discover.py companies.txt --add      # also add hits to config
    python3 discover.py "Stripe" "Figma"         # inline names
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from adapters.common import BROWSER_UA

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"


# --------------------------------------------------------------------------- #
# Slug generation
# --------------------------------------------------------------------------- #

_CORP_SUFFIXES = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|llc|ltd|limited|"
    r"group|holdings|technologies|technology|tech|labs|software|solutions|"
    r"systems|services|platforms|partners|ventures|networks?|digital|global|"
    r"international|enterprise|industries|pharma|therapeutics|robotics|"
    r"biosciences|biotech|healthcare|financial|capital|consulting|"
    r"analytics|entertainment|ai|io)\b\.?",
    re.I,
)


def _slugify(name):
    """Plausible board slugs from a company name, most-likely first.

    >>> _slugify("Palo Alto Networks")
    ['paloaltonetworks', 'palo-alto-networks', 'paloalto', 'palo-alto', 'palo']
    """
    clean = name.strip()
    clean = re.sub(r"\s*\(.*?\)\s*", " ", clean)
    clean = clean.lower()
    clean = clean.replace("&", "and")
    clean = re.sub(r"[^a-z0-9\s-]", "", clean)
    words = clean.split()
    if not words:
        return []

    seen, out = set(), []

    def _add(s):
        s = s.strip("-")
        if s and len(s) >= 2 and s not in seen:
            seen.add(s)
            out.append(s)

    _add("".join(words))
    _add("-".join(words))

    stripped = _CORP_SUFFIXES.sub("", " ".join(words)).split()
    if stripped and stripped != words:
        _add("".join(stripped))
        _add("-".join(stripped))

    if words[0] == "the" and len(words) >= 2:
        no_the = words[1:]
        _add("".join(no_the))
        _add("-".join(no_the))
        no_the_stripped = _CORP_SUFFIXES.sub("", " ".join(no_the)).split()
        if no_the_stripped and no_the_stripped != no_the:
            _add("".join(no_the_stripped))
            _add("-".join(no_the_stripped))

    if len(words) >= 2 and len(words[0]) >= 4:
        _add(words[0])

    if len(words) >= 3:
        _add("".join(words[:2]))
        _add("-".join(words[:2]))

    return out


# --------------------------------------------------------------------------- #
# ATS checkers — each returns (board_display_name, job_count, url) or None
# --------------------------------------------------------------------------- #

def _greenhouse(sess, slug):
    try:
        r = sess.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=15,
        )
        if not r.ok:
            return None
        n = len(r.json().get("jobs", []))
        if n == 0:
            return None
        r2 = sess.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}",
            timeout=15,
        )
        board_name = r2.json().get("name", slug) if r2.ok else slug
        return board_name, n, f"https://boards.greenhouse.io/{slug}"
    except Exception:
        return None


def _lever(sess, slug):
    try:
        r = sess.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            timeout=15,
        )
        if not r.ok:
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        return slug, len(data), f"https://jobs.lever.co/{slug}"
    except Exception:
        return None


def _ashby(sess, slug):
    try:
        r = sess.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=15,
        )
        if not r.ok:
            return None
        jobs = r.json().get("jobs", [])
        if len(jobs) == 0:
            return None
        return slug, len(jobs), f"https://jobs.ashbyhq.com/{slug}"
    except Exception:
        return None


_CHECKERS = [
    ("greenhouse", _greenhouse),
    ("lever", _lever),
    ("ashby", _ashby),
]


# --------------------------------------------------------------------------- #
# Name verification
# --------------------------------------------------------------------------- #

def _names_match(board_name, input_name):
    """True if the board's display name plausibly belongs to the input company.

    Guards against slug collisions (e.g. slug 'pan' → PAN Foundation when the
    input was 'Palo Alto Networks').
    """
    a = re.sub(r"[^a-z0-9]", "", board_name.lower())
    b = re.sub(r"[^a-z0-9]", "", input_name.lower())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    prefix = min(len(a), len(b), 6)
    return prefix >= 4 and a[:prefix] == b[:prefix]


# --------------------------------------------------------------------------- #
# Config awareness
# --------------------------------------------------------------------------- #

def _load_existing():
    """Return (set-of-lowercase-names, set-of-known-slugs) from config.json."""
    if not CONFIG_PATH.exists():
        return set(), set()
    boards = json.loads(CONFIG_PATH.read_text()).get("boards", [])
    names, slugs = set(), set()
    for b in boards:
        n = b.get("name", "").strip()
        if n:
            names.add(n.lower())
            names.add(re.sub(r"[^a-z0-9]", "", n.lower()))
        url = b.get("url", "")
        for pat in (r"boards\.greenhouse\.io/([a-z0-9_-]+)",
                    r"job-boards\.greenhouse\.io/([a-z0-9_-]+)",
                    r"jobs\.lever\.co/([a-z0-9_-]+)",
                    r"jobs\.ashbyhq\.com/([a-z0-9_-]+)"):
            m = re.search(pat, url, re.I)
            if m:
                slugs.add(m.group(1).lower())
        for field in ("token", "company", "board"):
            v = b.get(field)
            if isinstance(v, str):
                slugs.add(v.lower())
    return names, slugs


# --------------------------------------------------------------------------- #
# Per-company discovery
# --------------------------------------------------------------------------- #

def _discover_one(name, known_slugs):
    """Try slug variants against GH/Lever/Ashby.  Returns a list with at most
    one (ats, board_display_name, job_count, url) hit, or []."""
    sess = requests.Session()
    sess.headers["User-Agent"] = BROWSER_UA

    for slug in _slugify(name):
        if slug in known_slugs:
            continue
        for ats, checker in _CHECKERS:
            result = checker(sess, slug)
            if result is None:
                continue
            board_name, n, url = result
            if not _names_match(board_name, name):
                continue
            return [(ats, board_name, n, url)]
    return []


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _read_names(args):
    """Collect company names from positional args and/or --file."""
    names = list(args.names)
    if args.file:
        text = (sys.stdin.read() if args.file == "-"
                else Path(args.file).read_text())
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[.)]\s+", "", line)
            if line:
                names.append(line)
    return names


def main():
    ap = argparse.ArgumentParser(
        description="Discover career boards for a list of company names.",
    )
    ap.add_argument("names", nargs="*", help="Company names to look up")
    ap.add_argument(
        "--file", "-f",
        help="File of company names, one per line ('-' for stdin)",
    )
    ap.add_argument(
        "--add", action="store_true",
        help="Add discovered boards to config.json via probe",
    )
    ap.add_argument(
        "--workers", "-w", type=int, default=8,
        help="Concurrent lookups (default 8)",
    )
    args = ap.parse_args()

    names = _read_names(args)
    if not names:
        ap.error("give at least one company name (positional or --file)")

    known_names, known_slugs = _load_existing()

    todo = []
    for name in names:
        norm = re.sub(r"[^a-z0-9]", "", name.lower())
        if name.lower().strip() in known_names or norm in known_names:
            print(f"  skip  {name}  (already in config)")
        else:
            todo.append(name)

    if not todo:
        print("\nAll companies are already in config.")
        return

    print(f"\nChecking {len(todo)} name(s) against "
          f"Greenhouse / Lever / Ashby APIs...\n")

    all_hits = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(_discover_one, n, known_slugs): n for n in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            name = futs[fut]
            try:
                hits = fut.result()
            except Exception as e:
                print(f"  [{i:3}/{len(todo)}]  x {name:32}  error: {e}")
                continue
            if hits:
                for ats, bname, n, url in hits:
                    print(f"  [{i:3}/{len(todo)}]  + {name:32}  "
                          f"{ats:12}  {n:4} jobs  ({bname})")
                    all_hits.append((name, ats, bname, n, url))
            else:
                print(f"  [{i:3}/{len(todo)}]  . {name}")

    if not all_hits:
        print("\nNo new boards found.")
        return

    print(f"\n{'=' * 70}")
    print(f"  {len(all_hits)} board(s) found:\n")
    for name, ats, bname, n, url in sorted(all_hits):
        print(f"    {name:30}  {ats:12}  {n:4} jobs  {url}")

    if not args.add:
        print(f"\n  To add, re-run with --add, or pass to probe.py --add:\n")
        for name, ats, bname, n, url in sorted(all_hits):
            print(f"    {bname}, {url}")
        return

    print()
    from probe.cli import _handle_one
    for name, ats, bname, n, url in all_hits:
        try:
            _handle_one(url, bname, do_add=True)
        except Exception as e:
            print(f"  x {name}: {e}")


if __name__ == "__main__":
    main()
