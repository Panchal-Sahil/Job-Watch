"""Oracle Recruiting Cloud (Candidate Experience) ATS adapter."""

import time
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA


def fetch_oracle(board):
    """Oracle Recruiting Cloud (Candidate Experience) public REST API.
    URL like https://HOST/hcmUI/CandidateExperience/en/sites/SITE/jobs -> HOST + SITE.
    Note: vanity domains (e.g. jobs.nokia.com) proxy the UI but not the API, so
    they need the real `*.oraclecloud.com` host passed via "host" in config."""
    parsed = urlparse(board["url"])
    host = board.get("host") or parsed.netloc
    company = board.get("name", host)
    segs = [s for s in parsed.path.split("/") if s]
    site = board.get("site")
    if not site and "sites" in segs:
        site = segs[segs.index("sites") + 1]
    if not site:
        raise ValueError(f"Could not find Oracle site in URL: {board['url']}")

    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    jobs = []
    offset, limit, total = 0, 200, None
    while True:
        finder = (f"findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,"
                  f"limit={limit},offset={offset}")
        params = {"onlyData": "true",
                  "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                  "finder": finder}
        r = requests.get(api, headers={"User-Agent": BROWSER_UA}, params=params, timeout=30)
        r.raise_for_status()
        item = (r.json().get("items") or [{}])[0]
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
        time.sleep(0.3)
    return jobs
