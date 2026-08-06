"""RippleMatch public job-board adapter.

RippleMatch hosts white-labeled company career pages at
app.ripplematch.com/v2/public/company/<slug> (a Vue SPA). The jobs come from a
public JSON search API:

  GET https://app.ripplematch.com/api/public/jobs/unified?company=<name>&page=<n>

The `company` filter is the company's *display name* ("Palo Alto Networks"), not
the URL slug, and it is mandatory: without it the endpoint returns RippleMatch's
entire cross-company job universe (millions of rows). probe.py pins the exact
name (parsed from the page <title>) into the board's `company` field.

Response shape:
  {"jobs": [{"id","roleName","companyName","locations","postedDate","applyUrl"}],
   "pagination": {"page","per_page","has_next","total_items"}}
"""

import html

from adapters.common import BROWSER_UA, HTTP, TIMEOUT

API = "https://app.ripplematch.com/api/public/jobs/unified"

# The company filter narrows ~2.2M rows to a handful. If it ever silently stops
# applying we must not walk the whole universe: a real company page reports a
# tiny total_items (single/double digits), so anything above this is a filter
# regression, not a big employer. Well below the unfiltered ~2.2M, well above any
# plausible single-company count.
MAX_TOTAL = 5000
PAGE_CAP = 50  # backstop in case pagination.has_next never clears


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
            # The server filter is a name *search*, so a substring can leak in a
            # different company. Keep only rows whose company actually matches —
            # containment either way tolerates title/API name drift (whitespace,
            # "Inc" vs "Inc.") while still blocking another company's rows.
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
