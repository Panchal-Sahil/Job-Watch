"""Oracle Recruiting Cloud (Candidate Experience) ATS adapter."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, polite_sleep, TIMEOUT

# Vanity domains proxy the UI but not /hcmRestApi; the real API host is on
# *.oraclecloud.com, embedded in the page HTML.
_ORACLE_HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]*\.oraclecloud\.com", re.I)


def extract_oracle_host(html):
    """Real *.oraclecloud.com API host from a vanity page, or None. Shared with probe."""
    m = _ORACLE_HOST_RE.search(html or "")
    return m.group(0).lower() if m else None


def _resolve_vanity_host(url):
    """Fetch the vanity page and pull the real `*.oraclecloud.com` API host from it."""
    r = HTTP.get(url, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
    return extract_oracle_host(r.text)


_PAGE_LIMIT = 200

# Every board has its own *.oraclecloud.com host, so one shared pool is enough —
# no per-host throttling to respect, unlike Workday's pods.
_PAGE_WORKERS = 8
_POOL = None
_POOL_LOCK = threading.Lock()


def _pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=_PAGE_WORKERS, thread_name_prefix="oracle")
        return _POOL


def _get_page(host, site, offset):
    finder = (f"findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,"
              f"limit={_PAGE_LIMIT},offset={offset}")
    params = {"onlyData": "true",
              "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
              "finder": finder}
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    r = HTTP.get(url, headers={"User-Agent": BROWSER_UA}, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _fetch_page(host, site, offset):
    """One later page from the pool — host is already settled by page 0."""
    polite_sleep(0.3)
    item = (_get_page(host, site, offset).json().get("items") or [{}])[0]
    return item.get("requisitionList", [])


def _parse_reqs(reqs, host, site, company):
    jobs = []
    for p in reqs:
        jid = p.get("Id")
        locs = [p.get("PrimaryLocation") or ""]
        for sl in p.get("secondaryLocations") or []:
            if sl.get("Name"):
                locs.append(sl["Name"])
        jobs.append({
            "id": f"oracle:{host}:{jid}",
            "title": (p.get("Title") or "").strip(),
            "location": " ; ".join(locs).strip(),
            "posted": (p.get("PostedDate") or "")[:10],
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
            "company": company,
        })
    return jobs


def fetch_oracle(board):
    """Oracle Recruiting Cloud REST API. Vanity domains need host resolution."""
    parsed = urlparse(board["url"])
    host = board.get("host") or parsed.netloc
    company = board.get("name", host)
    segs = [s for s in parsed.path.split("/") if s]
    site = board.get("site")
    if not site and "sites" in segs:
        site = segs[segs.index("sites") + 1]
    if not site:
        raise ValueError(f"Could not find Oracle site in URL: {board['url']}")

    # Non-oraclecloud hosts can't serve /hcmRestApi; resolve upfront.
    tried_resolve = False
    if not host.endswith(".oraclecloud.com"):
        real = _resolve_vanity_host(board["url"])
        tried_resolve = True
        if real:
            host = real

    # Page 0 must be sequential — it settles the host and reports the total.
    while True:
        r = _get_page(host, site, 0)
        try:
            data = r.json()
            break
        except ValueError:
            # HTML instead of JSON = stale pod; re-resolve once and retry.
            if tried_resolve:
                raise
            tried_resolve = True
            real = _resolve_vanity_host(board["url"])
            if not real or real == host:
                raise
            host = real
    item = (data.get("items") or [{}])[0]
    pages = [item.get("requisitionList", [])]
    total = item.get("TotalJobsCount", 0)

    # Total is known — fan remaining pages out in parallel, kept in offset order.
    if pages[0]:
        pages.extend(_pool().map(lambda off: _fetch_page(host, site, off),
                                 range(_PAGE_LIMIT, total, _PAGE_LIMIT)))

    return [job for reqs in pages for job in _parse_reqs(reqs, host, site, company)]
