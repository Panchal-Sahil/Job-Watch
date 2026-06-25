"""Greenhouse ATS adapter."""

import requests

from adapters.common import HEADERS, _slug_from_url


def fetch_greenhouse(board):
    """Greenhouse public board API.
    URL like https://job-boards.greenhouse.io/<token>  ->  token."""
    token = _slug_from_url(board["url"], "token", board)
    company = board.get("name", token)
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    resp = requests.get(api, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    jobs = []
    for p in resp.json().get("jobs", []):
        jobs.append(
            {
                "id": f"gh:{token}:{p.get('id')}",
                "title": (p.get("title") or "").strip(),
                "location": (p.get("location") or {}).get("name", "").strip(),
                "posted": (p.get("updated_at") or "")[:10],
                "url": p.get("absolute_url", board["url"]),
                "company": company,
            }
        )
    return jobs
