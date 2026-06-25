"""Shared constants and helpers used across ATS adapters.

Things move here as each adapter is extracted from jobwatch.py. For now it
holds the JSON request HEADERS and the `_slug_from_url` helper used by the
Workday/Greenhouse/Lever/Ashby/SmartRecruiters adapters.
"""

from urllib.parse import urlparse

HEADERS = {
    # A real-ish User-Agent + JSON accept. Workday's API is picky about these.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobwatch/1.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _slug_from_url(url, fallback_field, board):
    """Last non-empty path segment of the board URL (drops query/fragment)."""
    if board.get(fallback_field):
        return board[fallback_field]
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[-1] if segs else None
