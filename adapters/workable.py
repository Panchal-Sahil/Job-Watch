"""Workable ATS adapter.

Uses the v1 widget API which returns all jobs in a single response (no paging).
URL pattern: https://apply.workable.com/<slug>
API endpoint: https://apply.workable.com/api/v1/widget/accounts/<slug>
"""

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url


def _format_location(job):
    """Build 'City, State, Country' from the job's location fields."""
    parts = [job.get("city", ""), job.get("state", ""), job.get("country", "")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def fetch_workable(board):
    slug = _slug_from_url(board["url"], "slug", board)
    api = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    resp = HTTP.get(api, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    company = board.get("name") or data.get("name") or slug
    jobs = []
    for p in data.get("jobs", []):
        jobs.append({
            "id": f"workable:{slug}:{p.get('shortcode')}",
            "title": (p.get("title") or "").strip(),
            "location": _format_location(p),
            "posted": (p.get("published_on") or "")[:10],
            "url": p.get("url") or p.get("shortlink") or board["url"],
            "company": company,
        })
    return jobs
