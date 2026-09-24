"""Phenom People ATS adapter."""

import html
import json
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, polite_sleep, TIMEOUT


def _extract_js_object(text, marker):
    """Pull the first balanced {...} JSON object after `marker`."""
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
    url = board["url"]
    host = urlparse(url).netloc
    company = board.get("name", host)
    ua = {"User-Agent": BROWSER_UA}

    page = HTTP.get(url, headers=ua, timeout=TIMEOUT)
    page.raise_for_status()
    cfg = _extract_js_object(page.text, "var phApp") or {}
    endpoint = cfg.get("widgetApiEndpoint") or f"https://{host}/widgets"
    locale = cfg.get("locale", "en_global")
    country = cfg.get("country", "global")
    page_id = cfg.get("pageId", "page1")

    # Some sites serve different job pools under a path prefix (e.g. /campus/).
    # phApp.widgetApiEndpoint always points to root /widgets.
    base_url = cfg.get("baseUrl", "")
    if base_url:
        url_path = urlparse(url).path
        base_path = urlparse(base_url).path
        idx = url_path.find(base_path)
        if idx > 0:
            ep = urlparse(endpoint)
            endpoint = ep._replace(path=url_path[:idx] + ep.path).geturl()

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
            r = HTTP.post(endpoint, headers=headers, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            rs = r.json().get("refineSearch", {})
            data = rs.get("data", {}) or {}
            postings = data.get("jobs", [])
            for p in postings:
                jid = p.get("jobId") or p.get("jobSeqNo")
                apply = p.get("applyUrl", url)
                # Phenom sites fronting Workday: use Workday ID format for cross-adapter dedup.
                ap = urlparse(apply)
                if "myworkdayjobs.com" in ap.netloc:
                    tenant = ap.netloc.split(".")[0]
                    job_id = f"{tenant}:{jid}"
                else:
                    job_id = f"phenom:{host}:{jid}"
                if apply.endswith("/apply"):
                    apply = apply[:-6]
                # Multi-location postings carry the primary city in
                # cityStateCountry and every city in multi_location.
                locs = [l.strip() for l in p.get("multi_location") or []
                        if isinstance(l, str) and l.strip()]
                location = "; ".join(locs) if len(locs) > 1 else (
                    p.get("cityStateCountry") or p.get("cityState")
                    or p.get("location") or "").strip()
                by_id[jid] = {
                    "id": job_id,
                    "title": html.unescape(p.get("title") or "").strip(),
                    "location": location,
                    "posted": (p.get("postedDate") or p.get("dateCreated") or "")[:10],
                    "url": apply,
                    "company": company,
                }
            frm += 100
            if not postings or frm >= rs.get("totalHits", 0):
                break
            polite_sleep(0.3)
    return list(by_id.values())
