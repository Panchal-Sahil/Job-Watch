"""iCIMS ATS adapter — classic portal (*.icims.com) and careers-home SPA (vanity domains)."""

import html
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlparse

from adapters.common import BROWSER_UA, new_session, TIMEOUT

_HOME_PAGE_SIZE = 10

_CARD = re.compile(r'<li class="iCIMS_JobCardItem">(.*?)</li>', re.S)
_HREF = re.compile(r'href="([^"]*?/jobs/(\d+)/[^"]*?)"')
_TITLE = re.compile(r"<h3[^>]*>\s*(.*?)\s*</h3>", re.S)
# Card layouts vary between tenants — match by label, not position.
_LOC = re.compile(
    r'field-label">(?:Job Locations|Location)</span>\s*<span\s*>\s*(.*?)\s*</span>', re.S
)
_POSTED = re.compile(r'Posted Date</(?:span|dt)>.*?<span title="([^"]*)"', re.S)
_PAGES = re.compile(r"Page\s+\d+\s+of\s+(\d+)")
_TAGS = re.compile(r"<[^>]+>")


def _clean(text):
    return html.unescape(_TAGS.sub("", text)).strip()


def _norm_date(raw):
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
        url = href.group(1).split("?")[0].removesuffix("/login")
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
    """Pages /api/jobs on a vanity-domain careers-home SPA."""
    host = urlparse(board["url"]).netloc
    company = board.get("name", host)
    namespace = host  # stable id namespace — client_code isn't on every row
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
                job_url = f"https://{host}/jobs/{jid}"
                by_id[jid] = {
                    "id": f"icims:{namespace}:{jid}",
                    "title": (jd.get("title") or "").strip(),
                    "location": (jd.get("full_location") or jd.get("location_name") or "").strip(),
                    "posted": (jd.get("posted_date") or "")[:10],
                    "url": job_url,
                    "company": company,
                }
            total = data.get("totalCount") or 0
            if len(rows) < _HOME_PAGE_SIZE or page * _HOME_PAGE_SIZE >= total or page > 200:
                break
            page += 1
    return list(by_id.values())


def fetch_icims(board):
    parsed = urlparse(board["url"])
    host = parsed.netloc
    if not host.endswith(".icims.com"):
        return _fetch_careers_home(board)
    # Strip "careersXX-" prefix for a stable namespace — display name only feeds `company`.
    namespace = re.sub(r"^(?:careers|jobs)[a-z]*-", "", host.split(".")[0])
    company = board.get("name", namespace)
    search = f"https://{host}/jobs/search"

    base_params = dict(parse_qsl(parsed.query))
    base_params["in_iframe"] = "1"  # server-rendered iframe view has the job cards

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
