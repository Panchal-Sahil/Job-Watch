"""Yello (Recsolu) ATS adapter.

URL pattern: https://<subdomain>.yello.co/job_boards/<board_id>
Search API:  GET /job_boards/<board_id>/search?query=&filters=[]&page_number=N
Returns JSON with an HTML fragment, count_on_page, and more_requisitions flag.
"""

import re
from html import unescape
from urllib.parse import urlparse

from adapters.common import HEADERS, HTTP, TIMEOUT, polite_sleep

_JOB_RE = re.compile(
    r'href="/jobs/([^?"]+)\?job_board_id=[^"]*"[^>]*>'
    r'(.*?)</a>.*?'
    r'<div>\s*<span>(\d+)</span>\s*</div>.*?'
    r'search-results__post-time[^>]*>(.*?)</div>',
    re.S,
)

_HEADERS = {**HEADERS, "Accept": "application/json"}


def _parse_board_url(url):
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    segs = [s for s in p.path.split("/") if s]
    board_id = segs[-1] if segs else ""
    return origin, board_id


def fetch_yello(board):
    origin, board_id = _parse_board_url(board["url"])
    company = board.get("name", board_id)
    search_url = f"{origin}/job_boards/{board_id}/search"

    jobs = []
    page = 1
    while True:
        resp = HTTP.get(
            search_url,
            params={"query": "", "filters": "[]", "page_number": page},
            headers=_HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        for slug, raw_title, req_id, posted in _JOB_RE.findall(data.get("html", "")):
            title = unescape(raw_title).strip()
            jobs.append({
                "id": f"yello:{board_id}:{req_id}",
                "title": title,
                "location": "",
                "posted": posted.strip(),
                "url": f"{origin}/jobs/{slug}?job_board_id={board_id}",
                "company": company,
            })

        if not data.get("more_requisitions") or data.get("count_on_page", 0) == 0:
            break
        page += 1
        polite_sleep(1)

    return jobs
