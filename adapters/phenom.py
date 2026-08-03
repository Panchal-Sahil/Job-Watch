"""Phenom People ATS adapter."""

import html
import json
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA, polite_sleep


def _extract_js_object(text, marker):
    """Pull the first balanced {...} JSON object appearing after `marker`."""
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[i : j + 1])
    return None


def fetch_phenom(board):
    """Phenom People career site. Reads the site's `phApp` config off the landing
    page, then queries its /widgets endpoint with early-careers keywords."""
    url = board["url"]
    host = urlparse(url).netloc
    company = board.get("name", host)
    ua = {"User-Agent": BROWSER_UA}

    page = requests.get(url, headers=ua, timeout=30)
    page.raise_for_status()
    cfg = _extract_js_object(page.text, "var phApp") or {}
    endpoint = cfg.get("widgetApiEndpoint") or f"https://{host}/widgets"
    locale = cfg.get("locale", "en_global")
    country = cfg.get("country", "global")
    page_id = cfg.get("pageId", "page1")

    # Search terms come from the board's `query` (else config's `query_terms`,
    # injected by jobwatch); an empty term searches everything.
    terms = board.get("query") or [""]
    if isinstance(terms, str):
        terms = [terms]

    by_id = {}
    headers = {"User-Agent": BROWSER_UA, "Content-Type": "application/json"}
    for term in terms:
        frm = 0
        while True:
            payload = {
                "lang": locale, "deviceType": "desktop", "country": country,
                "pageName": "search-results", "ddoKey": "refineSearch",
                "from": frm, "jobs": True, "counts": True,
                "all_fields": ["country", "state", "city", "category"],
                "size": 100, "clearAll": False, "jdsource": "facets",
                "pageId": page_id, "siteType": "external", "keywords": term,
                "global": True, "selected_fields": {},
                "sort": {"order": "", "field": ""}, "locationData": {},
            }
            r = requests.post(endpoint, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            rs = r.json().get("refineSearch", {})
            data = rs.get("data", {}) or {}
            postings = data.get("jobs", [])
            for p in postings:
                jid = p.get("jobId") or p.get("jobSeqNo")
                by_id[jid] = {
                    "id": f"phenom:{host}:{jid}",
                    "title": html.unescape(p.get("title") or "").strip(),
                    "location": (p.get("cityStateCountry") or p.get("cityState")
                                 or p.get("location") or "").strip(),
                    "posted": (p.get("postedDate") or p.get("dateCreated") or "")[:10],
                    "url": p.get("applyUrl", url),
                    "company": company,
                }
            frm += 100
            if not postings or frm >= rs.get("totalHits", 0):
                break
            polite_sleep(0.3)
    return list(by_id.values())
