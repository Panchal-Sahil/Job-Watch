"""Eightfold (PCSX career site) ATS adapter."""

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, new_session, TIMEOUT

# The page embeds its config (HTML-escaped) with the API domain. The cookie
# block also carries a "domain", so we take the FIRST match — that's the site's
# own (e.g. apply.careers.microsoft.com lists "microsoft.com" before the
# "careers.microsoft.com" cookie domain).
_DOMAIN_RE = re.compile(r'(?:&#34;|")domain(?:&#34;|")\s*:\s*(?:&#34;|")([a-z0-9.\-]+\.[a-z]{2,})')
_CSRF_RE = re.compile(r'name="_csrf"\s+content="([^"]+)"')

# Eightfold's /api/pcsx/search caps results at 10 per page regardless of `num`,
# so we always page through with `start`.
PAGE = 10


def fetch_eightfold(board):
    """Eightfold career site (the PCSX / careerhub variant). Loads the careers
    page for its CSRF token + API domain, then pages the `/api/pcsx/search`
    JSON endpoint. Like Phenom, it searches early-careers keywords (from the
    board's `query`, else config's `query_terms`) to avoid pulling the whole
    company; an empty term searches everything."""
    url = board["url"]
    host = urlparse(url).netloc
    ua = {"User-Agent": BROWSER_UA}

    sess = new_session()
    sess.headers.update(ua)
    page = sess.get(url, timeout=TIMEOUT)
    page.raise_for_status()

    m = _CSRF_RE.search(page.text)
    if not m:
        raise RuntimeError("no _csrf token on page (not an Eightfold PCSX site?)")
    csrf = m.group(1)

    domain = board.get("domain")
    if not domain:
        dm = _DOMAIN_RE.search(page.text)
        if not dm:
            raise RuntimeError("could not determine Eightfold API domain")
        domain = dm.group(1)

    company = board.get("name", domain)
    api = f"https://{host}/api/pcsx/search"
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json",
               "Referer": url, "X-CSRF-Token": csrf}

    terms = board.get("query") or [""]
    if isinstance(terms, str):
        terms = [terms]

    by_id = {}
    for term in terms:
        start = 0
        while True:
            params = {"domain": domain, "start": start, "num": PAGE,
                      "sort_by": "relevance"}
            if term:
                params["query"] = term
            r = sess.get(api, params=params, headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json().get("data", {}) or {}
            positions = data.get("positions", [])
            for p in positions:
                pid = p.get("id")
                ts = p.get("postedTs") or p.get("creationTs")
                posted = (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                          if ts else "")
                by_id[pid] = {
                    "id": f"ef:{domain}:{pid}",
                    "title": (p.get("name") or "").strip(),
                    "location": "; ".join(p.get("locations")
                                           or p.get("standardizedLocations") or []),
                    "posted": posted,
                    "url": f"https://{host}{p.get('positionUrl', '')}",
                    "company": company,
                }
            start += len(positions)
            if len(positions) < PAGE or start >= data.get("count", 0) or start > 2000:
                break
    return list(by_id.values())
