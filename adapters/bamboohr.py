"""BambooHR ATS adapter."""

from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA


def fetch_bamboohr(board):
    """BambooHR public careers API.
    URL like https://<sub>.bamboohr.com/careers -> uses that host."""
    host = urlparse(board["url"]).netloc
    sub = host.split(".")[0]
    company = board.get("name", sub)
    r = requests.get(f"https://{host}/careers/list",
                     headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json().get("result", []):
        loc = p.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        jid = p.get("id")
        jobs.append({
            "id": f"bamboo:{sub}:{jid}",
            "title": (p.get("jobOpeningName") or "").strip(),
            "location": location or ("Remote" if p.get("isRemote") else ""),
            "posted": "",
            "url": f"https://{host}/careers/{jid}",
            "company": company,
        })
    return jobs
