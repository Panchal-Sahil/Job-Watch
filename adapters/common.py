"""Shared constants and helpers used across ATS adapters.

Things move here as each adapter is extracted from jobwatch.py. For now it
holds the JSON request HEADERS and the `_slug_from_url` helper used by the
Workday/Greenhouse/Lever/Ashby/SmartRecruiters adapters.
"""

import time
from http import cookiejar
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# (connect, read). A bare `timeout=N` applies N to *both* phases, so a host that
# accepts the TCP connection and then stalls burns the whole budget before raising.
# Connecting either happens fast or not at all; reading a big paged board is
# legitimately slow, so the two deserve different limits. See docs/performance.md §5a.
TIMEOUT = (5, 20)

# Connection-level retries, mounted on every session below. What is deliberately
# NOT retried matters as much as what is:
#   * `status=0` with no forcelist — urllib3 retries nothing on an HTTP status.
#     Workday's 429 backoff in `workday._post_page` stays the sole owner of
#     throttling (retrying underneath it would double up and hit the pod harder),
#     and a board that 404s because the company closed it still fails at once
#     instead of being asked three times.
#   * `other=0` — no retry for errors urllib3 can't classify.
# What IS retried is the transport: connect and read failures, which is the class
# that cost three boards in a single run while each of them fetched fine alone.
# `allowed_methods=None` extends that to POST, normally unsafe but correct here —
# every POST in these adapters is a search/paging query that creates nothing, so
# replaying one is harmless. Two retries at backoff_factor 0.5 wait 0s then 1s,
# cheap against the 25-60s a whole re-fetch of one of these boards would cost.
_RETRY = Retry(total=2, connect=2, read=2, status=0, other=0,
               allowed_methods=None, backoff_factor=0.5, raise_on_status=False)


def new_session():
    """A `requests.Session` with the retry policy above mounted. Use this for a
    board that needs its own cookie jar — SuccessFactors, Dayforce, iCIMS and
    Eightfold each warm a session up on the careers page before paging its API."""
    sess = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


class _BlockCookies(cookiejar.DefaultCookiePolicy):
    """Refuse every cookie — see HTTP below."""

    def set_ok(self, cookie, request):
        return False


# The shared session for adapters that just issue one-off requests. Those used bare
# `requests.get(...)`, which builds a throwaway session per call, so no cookie ever
# outlived a single request. Pooling connections across the run is worth keeping, but
# a shared jar would leak cookies between unrelated companies' boards — so this one
# refuses them and stays effectively stateless, which is also what makes it safe to
# share across the run's worker threads (urllib3's pool manager is thread-safe; the
# session state that isn't is the cookie jar, and there now isn't one).
HTTP = new_session()
HTTP.cookies.set_policy(_BlockCookies())

# Adapters pause between pages to stay polite. The pauses are expressed at their
# original full-second weights and scaled by this one factor, which jobwatch sets
# from config.json's `request_delay_scale` — so backing off if a vendor ever starts
# throttling is a config edit, not a code change. 1.0 restores the original
# timings; 0 disables sleeping entirely.
DELAY_SCALE = 0.2

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


def polite_sleep(seconds):
    """Sleep `seconds`, scaled by DELAY_SCALE (see above). Call sites keep their
    original values so the intended politeness stays legible at the call."""
    if DELAY_SCALE > 0:
        time.sleep(seconds * DELAY_SCALE)


def _slug_from_url(url, fallback_field, board):
    """Last non-empty path segment of the board URL (drops query/fragment)."""
    if board.get(fallback_field):
        return board[fallback_field]
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[-1] if segs else None
