"""Shared constants and helpers used across ATS adapters."""

import time
from http import cookiejar
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = (5, 20)  # (connect, read) — split so reads stay generous

# status=0: Workday's own 429 backoff stays the sole throttle owner.
# allowed_methods=None: every POST here is idempotent (search/paging).
_RETRY = Retry(total=2, connect=2, read=2, status=0, other=0,
               allowed_methods=None, backoff_factor=0.5, raise_on_status=False)


def new_session():
    """Session with retry policy mounted; use when the board needs its own cookie jar."""
    sess = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


class _BlockCookies(cookiejar.DefaultCookiePolicy):

    def set_ok(self, cookie, request):
        return False


# Cookies blocked so boards can't leak state between companies — stateless, thread-safe.
HTTP = new_session()
HTTP.cookies.set_policy(_BlockCookies())

DELAY_SCALE = 0.2  # multiplier for inter-page pauses; set from config

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobwatch/1.0",  # Workday is picky
    "Accept": "application/json",
    "Content-Type": "application/json",
}

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def polite_sleep(seconds):
    """Sleep `seconds` scaled by DELAY_SCALE."""
    if DELAY_SCALE > 0:
        time.sleep(seconds * DELAY_SCALE)


def _slug_from_url(url, fallback_field, board):
    """Last non-empty path segment, or board[fallback_field] if set."""
    if board.get(fallback_field):
        return board[fallback_field]
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[-1] if segs else None
