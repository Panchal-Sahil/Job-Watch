#!/usr/bin/env python3
"""probe.py — point it at a careers URL, find out what ATS the site runs.

    python3 probe.py <url> [--name "Display Name"] [--add]
    python3 probe.py "Display Name, <url>" [--add]   # pin the name inline
    python3 probe.py --file urls.txt [--add]         # batch (Markdown/prose ok)

This is a thin entry point. The implementation lives in the `probe/` package:

    probe.signatures   — the ATS detection signature tables (data, not code)
    probe.confirm      — active Greenhouse/Lever/Ashby API confirmation
    probe.detect       — the probe() pipeline and slug/name/config helpers
    probe.config_io    — reading/writing config.json (and verifying a board)
    probe.input_parse  — pulling (name, url) pairs out of args/files
    probe.cli          — argparse and per-URL orchestration

See probe/__init__.py for the full detection strategy.
"""

from probe.cli import main

if __name__ == "__main__":
    main()
