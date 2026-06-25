"""SmartRecruiters ATS adapter."""

import time

import requests

from adapters.common import BROWSER_UA, _slug_from_url


def fetch_smartrecruiters(board):
    """SmartRecruiters public Posting API.
    URL like https://careers.smartrecruiters.com/<company>/ -> company."""
    company_id = _slug_from_url(board["url"], "company", board)
    company = board.get("name", company_id)
    api = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    jobs = []
    offset, limit, total = 0, 100, None
    while True:
        r = requests.get(api, headers={"User-Agent": BROWSER_UA},
                         params={"limit": limit, "offset": offset}, timeout=30)
        r.raise_for_status()
        data = r.json()
        postings = data.get("content", [])
        for p in postings:
            loc = p.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                             loc.get("country")) if x)
            jid = p.get("id")
            jobs.append({
                "id": f"smartrecruiters:{company_id}:{jid}",
                "title": (p.get("name") or "").strip(),
                "location": location,
                "posted": (p.get("releasedDate") or "")[:10],
                "url": f"https://jobs.smartrecruiters.com/{company_id}/{jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalFound", 0)
        offset += limit
        if not postings or offset >= total:
            break
        time.sleep(0.3)
    return jobs
