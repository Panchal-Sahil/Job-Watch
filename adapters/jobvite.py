"""Jobvite ATS adapter (HTML-scraped).

Jobvite hosts company boards at jobs.jobvite.com/<slug>. There is no public
JSON API; jobs are server-rendered into HTML tables. All postings appear on a
single page (no pagination). No posted-date is exposed in the listing HTML.
"""

import re
from html import unescape
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, TIMEOUT


_ROW = re.compile(
    r'<td\s+class="jv-job-list-name">\s*'
    r'<a\s+href="(/[^"]+/job/([^"]+))">\s*(.*?)\s*</a>\s*'
    r'</td>\s*'
    r'<td\s+class="jv-job-list-location">\s*(.*?)\s*</td>',
    re.DOTALL,
)


def fetch_jobvite(board):
    """Jobvite public board at jobs.jobvite.com/<slug> (HTML-scraped)."""
    url = board["url"]
    parsed = urlparse(url)
    host = parsed.netloc
    segs = [s for s in parsed.path.split("/") if s]
    slug = board.get("board") or (segs[0] if segs else host.split(".")[0])
    company = board.get("name", slug)

    page_url = f"https://{host}/{slug}"
    r = HTTP.get(page_url, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
    r.raise_for_status()

    jobs = []
    for m in _ROW.finditer(r.text):
        href, job_id, title_raw, loc_raw = m.groups()
        title = unescape(re.sub(r"\s+", " ", title_raw)).strip()
        location = unescape(re.sub(r"\s+", " ", loc_raw)).strip()
        jobs.append({
            "id": f"jobvite:{slug}:{job_id}",
            "title": title,
            "location": location,
            "posted": "",
            "url": f"https://{host}{href}",
            "company": company,
        })
    return jobs
