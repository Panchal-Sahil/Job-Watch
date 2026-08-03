"""Oracle Recruiting Cloud (Candidate Experience) ATS adapter."""

import re
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA, polite_sleep

# Vanity domains (careers.oracle.com, jobs.akamai.com, jobs.nokia.com, ...) proxy
# the CE *UI* but not the /hcmRestApi endpoint — hitting the API on the vanity host
# 302-redirects to an HTML error page, so `.json()` blows up. The real API lives on
# the tenant's `*.oraclecloud.com` pod, whose host is embedded in the page HTML.
_ORACLE_HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]*\.oraclecloud\.com", re.I)


def extract_oracle_host(html):
    """The real `*.oraclecloud.com` API host embedded in a vanity CE page, or None.
    Shared with probe so a probed vanity board gets its `host` pinned at add-time."""
    m = _ORACLE_HOST_RE.search(html or "")
    return m.group(0).lower() if m else None


def _resolve_vanity_host(url):
    """Fetch the vanity page and pull the real `*.oraclecloud.com` API host from it."""
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    return extract_oracle_host(r.text)


def fetch_oracle(board):
    """Oracle Recruiting Cloud (Candidate Experience) public REST API.
    URL like https://HOST/hcmUI/CandidateExperience/en/sites/SITE/jobs -> HOST + SITE.
    Vanity domains proxy the UI but not the API, so the real `*.oraclecloud.com` host
    is resolved from the page (or pinned via "host" in config). If a pinned pod is
    later re-pointed, a non-JSON response triggers a one-shot re-resolve from the page."""
    parsed = urlparse(board["url"])
    host = board.get("host") or parsed.netloc
    company = board.get("name", host)
    segs = [s for s in parsed.path.split("/") if s]
    site = board.get("site")
    if not site and "sites" in segs:
        site = segs[segs.index("sites") + 1]
    if not site:
        raise ValueError(f"Could not find Oracle site in URL: {board['url']}")

    # Proactively resolve the API host for a vanity domain: a non-oraclecloud host
    # definitionally can't serve /hcmRestApi, so don't wait for the API to fail
    # (that path is hostage to the vanity error page happening to return HTTP 200).
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
        r = requests.get(_api(host), headers={"User-Agent": BROWSER_UA},
                         params=params, timeout=30)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            # The host served HTML, not JSON — a re-pointed/stale pod. Re-resolve the
            # real host from the vanity page once and retry the same offset.
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
            jobs.append({
                "id": f"oracle:{host}:{jid}",
                "title": (p.get("Title") or "").strip(),
                "location": (p.get("PrimaryLocation") or "").strip(),
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
