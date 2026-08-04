"""iCIMS ATS adapter — handles both of iCIMS's two career-site products.

1. Classic portal (a real `<sub>.icims.com` host). The job list is a server-side
   HTML "iframe" view:

       https://<sub>.icims.com/jobs/search?<display params>

   The outer page is a wrapper; the job cards load from the same path with
   `in_iframe=1`. Each card exposes everything we need in plain HTML:

       <li class="iCIMS_JobCardItem">
         ...Job Locations...  <span>CA-ON-Greater Toronto Area</span>
         ...Posted Date...    <span title="6/25/2026 2:33 PM">...
         <div class="...title"><a href=".../jobs/5927/avp.../job"><h3>AVP ...</h3></a>

   Pagination is `pr=N` (0-indexed); the page count comes from a "Page X of Y"
   label.

2. "careers-home" SPA (a white-labeled vanity domain like `careers.amd.com`,
   `careers.kpmg.ca`, `www.pepsicojobs.com`). These don't serve the classic view
   at all — they're a React app backed by a clean JSON API at `/api/jobs`
   (`?keywords=&page=N`, 10 per page). See `_fetch_careers_home`.

The board's host picks the path: a true `*.icims.com` host is the classic portal;
anything else is treated as a careers-home vanity domain.
"""

import html
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

from adapters.common import BROWSER_UA, new_session, TIMEOUT

_HOME_PAGE_SIZE = 10  # careers-home /api/jobs returns 10 jobs per page

_CARD = re.compile(r'<li class="iCIMS_JobCardItem">(.*?)</li>', re.S)
_HREF = re.compile(r'href="([^"]*?/jobs/(\d+)/[^"]*?)"')
_TITLE = re.compile(r"<h3[^>]*>\s*(.*?)\s*</h3>", re.S)
# Card layouts vary between iCIMS tenants: the location label may be "Job
# Locations" or "Location", and the Posted Date may sit in a header `<span>` or
# in an "additional fields" `<dt>/<dd>` block. Match by label, not by position.
_LOC = re.compile(
    r'field-label">(?:Job Locations|Location)</span>\s*<span\s*>\s*(.*?)\s*</span>', re.S
)
_POSTED = re.compile(r'Posted Date</(?:span|dt)>.*?<span title="([^"]*)"', re.S)
_PAGES = re.compile(r"Page\s+\d+\s+of\s+(\d+)")
_TAGS = re.compile(r"<[^>]+>")


def _clean(text):
    return html.unescape(_TAGS.sub("", text)).strip()


def _norm_date(raw):
    """iCIMS gives an absolute timestamp like '6/25/2026 2:33 PM' -> '2026-06-25'."""
    raw = raw.strip()
    try:
        return datetime.strptime(raw.split()[0], "%m/%d/%Y").strftime("%Y-%m-%d")
    except (ValueError, IndexError):
        return raw


def _parse_cards(page_html, namespace, company):
    jobs = []
    for card in _CARD.findall(page_html):
        href = _HREF.search(card)
        if not href:
            continue
        jid = href.group(2)
        url = href.group(1).split("?")[0]
        title = _TITLE.search(card)
        loc = _LOC.search(card)
        posted = _POSTED.search(card)
        jobs.append({
            "id": f"icims:{namespace}:{jid}",
            "title": _clean(title.group(1)) if title else "",
            "location": _clean(loc.group(1)) if loc else "",
            "posted": _norm_date(posted.group(1)) if posted else "",
            "url": url,
            "company": company,
        })
    return jobs


def _fetch_careers_home(board):
    """iCIMS "careers-home" SPA on a vanity domain. Pages its /api/jobs JSON
    endpoint, searching the early-careers terms (board 'query' / config
    'query_terms') so we don't page the whole company; an empty term fetches all.
    Merges across terms by req_id."""
    host = urlparse(board["url"]).netloc
    company = board.get("name", host)
    # One stable namespace for every job on this board (the vanity host), so a job's
    # id never shifts — `client_code` isn't present on every row.
    namespace = host
    api = f"https://{host}/api/jobs"
    sess = new_session()
    sess.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})

    terms = board.get("query") or [""]
    if isinstance(terms, str):
        terms = [terms]

    by_id = {}
    for term in terms:
        page = 1
        while True:
            params = {"page": page}
            if term:
                params["keywords"] = term
            r = sess.get(api, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            rows = data.get("jobs", [])
            for row in rows:
                jd = row.get("data", {})
                jid = jd.get("req_id") or jd.get("slug")
                by_id[jid] = {
                    "id": f"icims:{namespace}:{jid}",
                    "title": (jd.get("title") or "").strip(),
                    "location": (jd.get("full_location") or jd.get("location_name") or "").strip(),
                    "posted": (jd.get("posted_date") or "")[:10],
                    "url": jd.get("apply_url") or board["url"],
                    "company": company,
                }
            total = data.get("totalCount") or 0
            if len(rows) < _HOME_PAGE_SIZE or page * _HOME_PAGE_SIZE >= total or page > 200:
                break
            page += 1
    return list(by_id.values())


def fetch_icims(board):
    parsed = urlparse(board["url"])
    host = parsed.netloc  # careersen-mackenzieinvestments.icims.com
    # A true *.icims.com host is the classic portal; anything else is a vanity
    # domain running the newer careers-home SPA (different API entirely).
    if not host.endswith(".icims.com"):
        return _fetch_careers_home(board)
    # Namespace: the leading hostname label, sans an optional "careersXX-" prefix.
    # Derived from the (stable) host so job IDs survive a board rename; the display
    # name only feeds the `company` field.
    namespace = re.sub(r"^(?:careers|jobs)[a-z]*-", "", host.split(".")[0])
    company = board.get("name", namespace)
    search = f"https://{host}/jobs/search"

    # Preserve any real search facets from the board URL (searchKeyword/Category/…),
    # but force the server-rendered iframe view that contains the job cards.
    base_params = dict(parse_qsl(parsed.query))
    base_params["in_iframe"] = "1"

    sess = new_session()
    sess.headers["User-Agent"] = BROWSER_UA

    first = sess.get(search, params={**base_params, "pr": 0}, timeout=TIMEOUT)
    first.raise_for_status()
    m = _PAGES.search(first.text)
    pages = int(m.group(1)) if m else 1

    jobs = _parse_cards(first.text, namespace, company)
    for pr in range(1, pages):
        r = sess.get(search, params={**base_params, "pr": pr}, timeout=TIMEOUT)
        r.raise_for_status()
        jobs.extend(_parse_cards(r.text, namespace, company))
    return jobs
