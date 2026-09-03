#!/usr/bin/env python3
"""probe.py — point it at a careers URL, find out what ATS the site runs.

    python3 probe.py <url> [--name "Display Name"] [--add]
    python3 probe.py "Display Name, <url>" [--add]   # pin the name inline
    python3 probe.py --file urls.txt [--add]         # batch (Markdown/prose ok)
"""

from probe.cli import main

if __name__ == "__main__":
    main()
