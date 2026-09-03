"""Oracle Recruiting Cloud (Candidate Experience) ATS adapter."""

import re
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

    def _api(h):
        return f"https://{h}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    jobs = []
    offset, limit, total = 0, 200, None
    while True:
        finder = (f"findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,"
                  f"limit={limit},offset={offset}")
        params = {"onlyData": "true",
                  "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                  "finder": finder}
        r = HTTP.get(_api(host), headers={"User-Agent": BROWSER_UA},
                         params=params, timeout=TIMEOUT)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            # HTML instead of JSON = stale pod; re-resolve once and retry.
            if tried_resolve:
                raise
            tried_resolve = True
            real = _resolve_vanity_host(board["url"])
            if not real or real == host:
                raise
            host = real
            continue
        item = (data.get("items") or [{}])[0]
        reqs = item.get("requisitionList", [])
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
        if total is None:
            total = item.get("TotalJobsCount", 0)
        offset += limit
        if not reqs or offset >= total:
            break
        polite_sleep(0.3)
    return jobs
