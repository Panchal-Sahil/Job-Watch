"""Lever ATS adapter."""

import time

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url


def fetch_lever(board):
    """Lever public postings API.
    URL like https://jobs.lever.co/<company>  ->  company."""
    slug = _slug_from_url(board["url"], "company", board)
    company = board.get("name", slug)
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = HTTP.get(api, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for p in resp.json():
        cats = p.get("categories") or {}
        all_locs = cats.get("allLocations") or []
        if len(all_locs) > 1:
            location = " ; ".join(all_locs)
        else:
            location = (cats.get("location") or "").strip()
        created = p.get("createdAt")
        posted = (
            time.strftime("%Y-%m-%d", time.gmtime(created / 1000)) if created else ""
        )
        jobs.append(
            {
                "id": f"lever:{slug}:{p.get('id')}",
                "title": (p.get("text") or "").strip(),
                "location": location,
                "posted": posted,
                "url": p.get("hostedUrl", board["url"]),
                "company": company,
            }
        )
    return jobs
