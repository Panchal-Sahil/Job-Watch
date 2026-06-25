"""Lever ATS adapter."""

import time

import requests

from adapters.common import HEADERS, _slug_from_url


def fetch_lever(board):
    """Lever public postings API.
    URL like https://jobs.lever.co/<company>  ->  company."""
    slug = _slug_from_url(board["url"], "company", board)
    company = board.get("name", slug)
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = requests.get(api, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    jobs = []
    for p in resp.json():
        cats = p.get("categories") or {}
        created = p.get("createdAt")
        posted = (
            time.strftime("%Y-%m-%d", time.gmtime(created / 1000)) if created else ""
        )
        jobs.append(
            {
                "id": f"lever:{slug}:{p.get('id')}",
                "title": (p.get("text") or "").strip(),
                "location": (cats.get("location") or "").strip(),
                "posted": posted,
                "url": p.get("hostedUrl", board["url"]),
                "company": company,
            }
        )
    return jobs
