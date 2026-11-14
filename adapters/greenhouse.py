"""Greenhouse ATS adapter."""

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url, pin_slug

# Config field a white-labelled board pins its real slug in.
SLUG_FIELD = "token"


def fetch_greenhouse(board):
    """Greenhouse public board API.
    URL like https://job-boards.greenhouse.io/<token>  ->  token."""
    token = _slug_from_url(board["url"], SLUG_FIELD, board)
    company = board.get("name", token)
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    resp = HTTP.get(api, headers=HEADERS, timeout=TIMEOUT)
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


def probe_entry(url, page_html, slug, on_ats):
    return pin_slug(SLUG_FIELD, slug, on_ats)
