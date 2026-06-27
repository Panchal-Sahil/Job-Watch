"""Command-line entry point: argument parsing and per-URL orchestration.

main() collects (name, url) pairs, runs each through probe(), prints a report, and
— with --add — verifies the board fetches before inserting it into config.json. One
bad URL never sinks the batch.
"""

import argparse
import sys

from probe.config_io import CONFIG_PATH, _append_to_config, _format_entry, _verify
from probe.detect import probe
from probe.input_parse import _collect_entries
from probe.signatures import SUPPORTED_TYPES


def _handle_one(url, name, do_add):
    """Probe one URL, print its report, optionally add it. Returns a one-line
    status string for the batch summary."""
    res = probe(url)
    if name and res.config:
        res.config["name"] = name
        res.name = name

    print(f"\n  URL:      {url}  {res.note}")
    if not res.type:
        if res.other:
            print(f"  ATS:      {res.other}  [NOT SUPPORTED — no adapter]")
            print(f"  Evidence: {res.evidence}")
            return f"{res.other} (no adapter)"
        print("  ATS:      unknown — no known ATS signature found.")
        print("  Try the actual job-board/iframe URL, or it may be a bespoke/JS-injected site.")
        return "unknown"

    supported = res.type in SUPPORTED_TYPES
    tag = "supported" if supported else "NO ADAPTER"
    print(f"  ATS:      {res.type}  [{tag}]   (confidence: {res.confidence})")
    print(f"  Evidence: {res.evidence}")
    if res.slug:
        print(f"  Slug:     {res.slug}")
    print("\n  config.json board entry:\n")
    print(_format_entry(res.config))

    if not do_add:
        print("\n  (re-run with --add to append it to config.json — it's added"
              " only if the board actually fetches jobs)")
        return f"{res.type} (not added)"

    if not supported:
        print(f"\n  Not added: '{res.type}' has no adapter yet.")
        return f"{res.type} (no adapter, skipped)"

    # Verify it actually works in jobwatch before touching config.json. The
    # validity test is whether the fetch *succeeds* — a reachable board with 0
    # current postings is still valid (a company can just have nothing open now),
    # so only an error blocks the add. 0 jobs is surfaced as a heads-up.
    print("\n  Verifying (fetching the board the same way jobwatch will)...")
    n, error = _verify(res.config)
    if error:
        print(f"  Not added: board returned an error — {error}.")
        return f"{res.type} (NOT added: error — {error})"

    if n == 0:
        note = "fetched 0 jobs — board is reachable but has no postings right now"
    else:
        note = f"fetched {n} job{'s' if n != 1 else ''}"
    group, count = _append_to_config(res.config)
    print(f"  ✓ Added to {CONFIG_PATH.name} in the '{group}' group "
          f"({count} of this type) — {note}")
    return f"{res.type} ✓ added — {note}"


def main():
    ap = argparse.ArgumentParser(description="Detect which ATS one or more careers URLs run on.")
    ap.add_argument("urls", nargs="*",
                    help="Careers URLs, or 'Name, url' / 'Name<TAB>url' to pin the name")
    ap.add_argument("--file", help="Read URLs (or 'Name, url' lines) from a file ('-' for stdin)")
    ap.add_argument("--name", help="Display name (only applied when given a single URL)")
    ap.add_argument("--add", action="store_true",
                    help="Add to config.json — only boards that fetch without error")
    args = ap.parse_args()

    entries = _collect_entries(args)
    if not entries:
        ap.error("give at least one URL (positional or 'Name, url') or --file")
    if args.name and len(entries) > 1:
        ap.error("--name only works with a single URL")

    summary = []
    for name, url in entries:
        try:
            # Inline 'Name, url' wins; --name covers the single-URL no-inline case.
            status = _handle_one(url, name or args.name, args.add)
        except Exception as e:  # never let one bad URL sink the batch
            print(f"\n  URL:      {url}\n  ERROR: {e}")
            status = f"ERROR: {e}"
        summary.append((url, status))

    if len(entries) > 1:
        print("\n" + "=" * 70 + "\n  Summary:\n")
        for url, status in summary:
            print(f"  {status:42}  {url}")
    print()
