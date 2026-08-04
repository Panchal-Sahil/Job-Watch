"""JazzHR ATS adapter (HTML-scraped).

JazzHR hosts company job boards at <tenant>.applytojob.com/apply.
There is no public JSON API; jobs come from the listing HTML table.

Row IDs encode the posting timestamp: row_job_YYYYMMDDHHMMSS_<internalid>.
The short alphanumeric job slug (e.g. hKQA66W5Ad) from the detail URL is
used as the stable dedup key.
"""

import re
from html import unescape
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, TIMEOUT


_ROW = re.compile(
    r'<tr\s+id="row_job_(\d{14})_[^"]*"[^>]*>(.*?)</tr>', re.DOTALL
)
_LINK = re.compile(
    r'<a\s+class="job_title_link"\s+href="/apply/jobs/details/(\w+)[^"]*">([^<]+)</a>'
)
_LOC = re.compile(r'</td>\s*<td[^>]*>\s*(.*?)\s*</td>', re.DOTALL)


def fetch_jazzhr(board):
    """JazzHR public board at <tenant>.applytojob.com/apply (HTML-scraped)."""
    parsed = urlparse(board["url"])
    tenant = parsed.netloc.split(".")[0]
    host = parsed.netloc
    company = board.get("name", tenant)

    r = HTTP.get(
        f"https://{host}/apply/jobs",
        headers={"User-Agent": BROWSER_UA},
        timeout=TIMEOUT,
    )
    r.raise_for_status()

    jobs = []
    for m in _ROW.finditer(r.text):
        ts, body = m.group(1), m.group(2)
        posted = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        lm = _LINK.search(body)
        loc_m = _LOC.search(body)
        if not lm:
            continue
        slug, title = lm.group(1), unescape(lm.group(2).strip())
        location = loc_m.group(1).strip() if loc_m else ""
        jobs.append({
            "id": f"jazzhr:{tenant}:{slug}",
            "title": title,
            "location": location,
            "posted": posted,
            "url": f"https://{host}/apply/jobs/details/{slug}",
            "company": company,
        })
    return jobs
