"""Rippling ATS adapter."""

from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA


def fetch_rippling(board):
    """Rippling ATS public board API.
    URL like https://ats.rippling.com/<slug>/jobs -> slug."""
    segs = [s for s in urlparse(board["url"]).path.split("/") if s]
    slug = board.get("board") or (segs[0] if segs else None)
    company = board.get("name", slug)
    api = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    r = requests.get(api, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json():
        jobs.append({
            "id": f"rippling:{slug}:{p.get('uuid')}",
            "title": (p.get("name") or "").strip(),
            "location": (p.get("workLocation") or {}).get("label", ""),
            "posted": "",
            "url": p.get("url", board["url"]),
            "company": company,
        })
    return jobs
