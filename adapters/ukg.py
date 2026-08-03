"""UKG Pro Recruiting (UltiPro) ATS adapter."""

from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA, polite_sleep


def fetch_ukg(board):
    """UKG Pro Recruiting (UltiPro) public job board API.
    URL like https://recruiting.ultipro.ca/<TENANT>/JobBoard/<guid>/ -> both."""
    parsed = urlparse(board["url"])
    host = parsed.netloc
    segs = [s for s in parsed.path.split("/") if s]
    tenant = segs[0]
    guid = segs[segs.index("JobBoard") + 1] if "JobBoard" in segs else segs[2]
    company = board.get("name", tenant)
    api = (f"https://{host}/{tenant}/JobBoard/{guid}/JobBoardView/LoadSearchResults")
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json",
               "Content-Type": "application/json"}

    jobs, skip, total = [], 0, None
    while True:
        body = {"opportunitySearch": {"Top": 100, "Skip": skip, "QueryString": "",
                                      "OrderBy": [], "Filters": []}}
        r = requests.post(api, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        opps = data.get("opportunities", [])
        for p in opps:
            locs = "; ".join(l.get("LocalizedDescription", "") for l in p.get("Locations", []))
            jid = p.get("Id")
            jobs.append({
                "id": f"ukg:{tenant}:{jid}",
                "title": (p.get("Title") or "").strip(),
                "location": locs,
                "posted": (p.get("PostedDate") or "")[:10],
                "url": f"https://{host}/{tenant}/JobBoard/{guid}/OpportunityDetail?opportunityId={jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalCount", 0)
        skip += 100
        if not opps or skip >= total:
            break
        polite_sleep(0.3)
    return jobs
