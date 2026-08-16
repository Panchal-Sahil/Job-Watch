"""ZohoRecruit ATS adapter.

ZohoRecruit career pages embed all published jobs as JSON inside a hidden
<input id="jobs"> element. The page is server-rendered (no separate API call
needed), but requires following a session redirect, so we use a fresh session.

URL pattern: https://<org>.zohorecruit.com/jobs/<PageName>
             https://<org>.zohorecruit.ca/jobs/<PageName>
"""

import html
import json
import re

from adapters.common import TIMEOUT, new_session

_JOBS_RE = re.compile(r'value="([^"]*)"[^>]*id="jobs"|id="jobs"[^>]*value="([^"]*)"')
_META_RE = re.compile(r'value="([^"]*)"[^>]*id="meta"|id="meta"[^>]*value="([^"]*)"')

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobwatch/1.0",
    "Accept": "text/html",
}


def _build_location(job):
    parts = [job.get("City", ""), job.get("State", ""), job.get("Country", "")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _title_slug(title):
    return re.sub(r'["\~`!@#$%^&*()\=+\[\]{}|:;<>\\/,.?\'\s]+', "-", title)


def fetch_zohorecruit(board):
    url = board["url"]
    sess = new_session()
    resp = sess.get(url, headers=_HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    page = resp.text

    m = _JOBS_RE.search(page)
    if not m:
        return []
    raw_jobs = json.loads(html.unescape(m.group(1) or m.group(2)))

    company = board.get("name", "")
    if not company:
        mm = _META_RE.search(page)
        if mm:
            meta = json.loads(html.unescape(mm.group(1) or mm.group(2)))
            org = meta.get("org_info") or {}
            company = org.get("company_name", "")
        if not company:
            company = url.split("/")[2].split(".")[0]

    base_url = url.rstrip("/")
    jobs = []
    for p in raw_jobs:
        jid = str(p.get("id", ""))
        title = (p.get("Posting_Title") or p.get("Job_Opening_Name") or "").strip()
        slug = _title_slug(title) if title else jid
        jobs.append({
            "id": f"zohorecruit:{jid}",
            "title": title,
            "location": _build_location(p),
            "posted": (p.get("Date_Opened") or "")[:10],
            "url": f"{base_url}/{jid}/{slug}",
            "company": company,
        })
    return jobs
