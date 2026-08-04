"""Ashby ATS adapter."""

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url


def fetch_ashby(board):
    """Ashby public job-board API.
    URL like https://jobs.ashbyhq.com/<board>  ->  board."""
    slug = _slug_from_url(board["url"], "board", board)
    company = board.get("name", slug)
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = HTTP.get(api, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs = []
    for p in resp.json().get("jobs", []):
        jobs.append(
            {
                "id": f"ashby:{slug}:{p.get('id')}",
                "title": (p.get("title") or "").strip(),
                "location": (p.get("location") or "").strip(),
                "posted": (p.get("publishedDate") or p.get("updatedDate") or "")[:10],
                "url": p.get("jobUrl", board["url"]),
                "company": company,
            }
        )
    return jobs
