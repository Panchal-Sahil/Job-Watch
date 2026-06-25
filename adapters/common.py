"""Shared constants and helpers used across ATS adapters.

Things move here as each adapter is extracted from jobwatch.py. For now it
holds the JSON request HEADERS used by the Workday/Greenhouse/Lever/Ashby
adapters.
"""

HEADERS = {
    # A real-ish User-Agent + JSON accept. Workday's API is picky about these.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobwatch/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
