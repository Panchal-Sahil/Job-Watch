#!/usr/bin/env python3
"""Quick live test for one ATS type or one named board.

Usage:
  python3 test-ats.py --ats avature
  python3 test-ats.py --ats avature --board "Siemens"
  python3 test-ats.py --ats avature --raw        # skip filtering
  python3 test-ats.py --ats avature --n 10       # show up to N sample jobs
"""

import argparse
import sys
from pathlib import Path

from jobwatch import fetch_board, load_json, matches

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def main():
    ap = argparse.ArgumentParser(description="Live-test one ATS adapter against real boards.")
    ap.add_argument("--ats", required=True, metavar="TYPE",
                    help="adapter type to test (e.g. avature, greenhouse, workday)")
    ap.add_argument("--board", metavar="NAME",
                    help="limit to this board name (default: all boards of that type)")
    ap.add_argument("--raw", action="store_true",
                    help="skip filtering — show all jobs returned by the adapter")
    ap.add_argument("--n", type=int, default=5, metavar="N",
                    help="number of sample jobs to print per board (default: 5)")
    args = ap.parse_args()

    config = load_json(CONFIG_PATH, {})
    filters = config.get("filters", {})
    boards = [b for b in config.get("boards", []) if b.get("type") == args.ats]

    if not boards:
        sys.exit(f"No boards of type '{args.ats}' found in config.json.")

    if args.board:
        boards = [b for b in boards if b.get("name", "").lower() == args.board.lower()]
        if not boards:
            sys.exit(f"No board named '{args.board}' with type '{args.ats}' found.")

    for board in boards:
        name = board.get("name", board.get("url", "?"))
        print(f"\n=== {name} ===")
        _, jobs, err = fetch_board(board)
        if err:
            print(f"  ERROR: {err}")
            continue

        if args.raw:
            sample = jobs
            label = f"{len(jobs)} jobs (raw)"
        else:
            sample = [j for j in jobs if matches(j, filters)]
            label = f"{len(jobs)} raw  →  {len(sample)} after filter"

        print(f"  {label}")
        for j in sample[:args.n]:
            posted = f"  posted={j['posted']}" if j["posted"] else ""
            print(f"  • {j['title']}")
            print(f"    {j['location']}{posted}")
            print(f"    {j['url']}")


if __name__ == "__main__":
    main()
