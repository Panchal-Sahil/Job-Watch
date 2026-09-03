"""RippleMatch public job-board adapter. The `company` filter is the company's
display name, not the URL slug; without it the API returns millions of rows.
"""

import html

from adapters.common import BROWSER_UA, HTTP, TIMEOUT

API = "https://app.ripplematch.com/api/public/jobs/unified"

# Without the company filter the API returns ~2.2M rows. Anything above this
# means the filter silently stopped working.
MAX_TOTAL = 5000
PAGE_CAP = 50


def fetch_ripplematch(board):
    company = board.get("company") or board.get("name")
    if not company:
        raise ValueError(
            "ripplematch board needs a 'company' (the exact RippleMatch display "
            "name) — without it the API returns every company's jobs")
    want = company.strip().lower()
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}

    jobs, page = [], 1
    while page <= PAGE_CAP:
        r = HTTP.get(API, params={"company": company, "page": page},
                         headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        pag = data.get("pagination") or {}

        total = pag.get("total_items")
        if total is not None and total > MAX_TOTAL:
            raise ValueError(
                f"ripplematch company filter '{company}' matched {total} jobs — "
                "the filter isn't narrowing (refusing to page the whole board)")

        for p in data.get("jobs", []):
            # Server filter is a name search — substring can leak other companies.
            cn = (p.get("companyName") or "").strip().lower()
            if want not in cn and cn not in want:
                continue
            jid = p.get("id")
            jobs.append({
                "id": f"ripplematch:{jid}",
                "title": html.unescape((p.get("roleName") or "").strip()),
                "location": "; ".join(p.get("locations") or []),
                "posted": (p.get("postedDate") or "")[:10],
                "url": p.get("applyUrl") or board["url"],
                "company": p.get("companyName") or company,
            })

        if not pag.get("has_next"):
            break
        page += 1
    return jobs
