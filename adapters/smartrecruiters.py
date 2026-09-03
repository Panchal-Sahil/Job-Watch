"""SmartRecruiters ATS adapter."""

import re
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, polite_sleep, TIMEOUT, _slug_from_url

# Vanity pages embed the real company id in widget config or SR links.
# Most-specific-first; job-link patterns require a job-id-shaped next segment.
_SR_COMPANY_RES = [
    re.compile(r"""["']company_code["']\s*:\s*["']([A-Za-z0-9_.-]+)["']"""),
    re.compile(r"\bdcr_ci=([A-Za-z0-9_.-]+)"),
    re.compile(r"smartrecruiters\.com/my-applications/([A-Za-z0-9_.-]+)"),
    re.compile(r"smartrecruiters\.com/([A-Za-z0-9_.-]+)/"
               r"(?:\d{6,}|[0-9a-f]{8}-[0-9a-f]{4}-)"),
]

# Hosts whose path segment *is* the company id — no page lookup needed.
_SR_HOST_RE = re.compile(r"(^|\.)(careers|jobs)\.smartrecruiters\.com$", re.I)


def extract_smartrecruiters_company(html):
    """Real company id from a vanity page, or None. Shared with probe."""
    for pat in _SR_COMPANY_RES:
        m = pat.search(html or "")
        if m:
            return m.group(1)
    return None


def _resolve_vanity_company(url):
    """Fetch the vanity careers page and pull the real company id out of it."""
    r = HTTP.get(url, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
    return extract_smartrecruiters_company(r.text)


def fetch_smartrecruiters(board):
    """SmartRecruiters Posting API. Vanity domains need company-id resolution."""
    parsed = urlparse(board["url"])
    on_ats = bool(_SR_HOST_RE.search(parsed.netloc))
    company_id = _slug_from_url(board["url"], "company", board)

    # Vanity path segments are meaningless; resolve the real company id upfront.
    tried_resolve = False
    if not on_ats and not board.get("company"):
        company_id = _resolve_vanity_company(board["url"]) or company_id
        tried_resolve = True
    if not company_id:
        raise ValueError(f"Could not find SmartRecruiters company in: {board['url']}")

    company = board.get("name", company_id)
    jobs = []
    offset, limit, total = 0, 100, None
    while True:
        api = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
        r = HTTP.get(api, headers={"User-Agent": BROWSER_UA},
                         params={"limit": limit, "offset": offset}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        postings = data.get("content", [])

        # Stale pinned id returns 200 + 0 jobs; re-resolve once before believing.
        if not postings and offset == 0 and not on_ats and not tried_resolve:
            tried_resolve = True
            real = _resolve_vanity_company(board["url"])
            if real and real != company_id:
                company_id = real
                company = board.get("name", company_id)
                continue

        for p in postings:
            loc = p.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                             loc.get("country")) if x)
            jid = p.get("id")
            jobs.append({
                "id": f"smartrecruiters:{company_id}:{jid}",
                "title": (p.get("name") or "").strip(),
                "location": location,
                "posted": (p.get("releasedDate") or "")[:10],
                "url": f"https://jobs.smartrecruiters.com/{company_id}/{jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalFound", 0)
        offset += limit
        if not postings or offset >= total:
            break
        polite_sleep(0.3)
    return jobs
