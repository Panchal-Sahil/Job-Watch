"""SAP SuccessFactors (RMK) ATS adapter. Modern CSB JSON API, classic tile fallback."""

import html
import json
import re
from urllib.parse import quote, unquote, urlparse

import requests

from adapters.common import BROWSER_UA, HTTP, new_session, polite_sleep, TIMEOUT

CA_PROV = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}

# CSB maps location onto an arbitrary custom field per site.
_MODERN_LOC_KEYS = ("jobLocationShort", "displayLocation", "location", "city",
                    "mfield1", "mfield2", "mfield3")
_LOC_LIKE = re.compile(r"[A-Za-z].*,\s*[A-Z]{2}\b")


def _modern_location(resp):
    """Best-effort location from a modern-API job. Strips embedded HTML."""
    def clean(val):
        vals = val if isinstance(val, list) else [val]
        out = []
        for v in vals:
            v = re.sub(r"<[^>]+>", " ", str(v))
            v = re.sub(r"\s+", " ", v).strip().strip(",").strip()
            if v:
                out.append(v)
        return list(dict.fromkeys(out))

    for key in _MODERN_LOC_KEYS:
        if resp.get(key):
            vals = clean(resp[key])
            if vals:
                return "; ".join(vals)
    for val in resp.values():
        if isinstance(val, list) and val and all(isinstance(v, str) for v in val):
            vals = clean(val)
            if vals and any(_LOC_LIKE.search(v) for v in vals):
                return "; ".join(vals)
    return ""


def _detect_locale(html):
    """Extract locale from the page's html lang attribute; fall back to en_US."""
    m = re.search(r'<html[^>]*\blang=["\']?([a-zA-Z]{2}[_-][a-zA-Z]{2})', html)
    return m.group(1).replace("-", "_") if m else "en_US"


def _fetch_modern(url, netloc, company):
    """Returns job list, or None if this isn't a modern template (caller falls back)."""
    sess = new_session()
    sess.headers["User-Agent"] = BROWSER_UA
    try:
        sess.get(url, timeout=TIMEOUT)  # warm cookies
    except requests.RequestException:
        return None
    api = f"https://{netloc}/services/recruiting/v1/jobs"
    jobs, seen, page = [], set(), 0
    while page < 200:
        body = json.dumps({"keywords": "", "locale": "en_US", "location": "",
                           "pageNumber": page, "sortBy": "recent"})
        # Transport failure: fall back on page 0, keep collected jobs after.
        try:
            r = sess.post(api, headers={"Content-Type": "application/json"},
                          data=body, timeout=TIMEOUT)
        except requests.RequestException:
            return None if page == 0 else jobs
        # Page 0 fail = not modern; later fail = paged past end.
        bad = (not r.ok)
        data = None
        if not bad:
            try:
                data = r.json()
            except ValueError:
                bad = True
        if not bad and (not isinstance(data, dict) or "jobSearchResult" not in data):
            bad = True
        if bad:
            return None if page == 0 else jobs
        results = data["jobSearchResult"] or []
        new = 0
        for item in results:
            resp = item.get("response") or {}
            jid = resp.get("id")
            if jid is None or jid in seen:
                continue
            seen.add(jid)
            new += 1
            title = (resp.get("unifiedStandardTitle") or resp.get("urlTitle") or "").strip()
            url_title = resp.get("unifiedStandardTitle") or "untitled"
            jobs.append({
                "id": f"sf:{netloc}:{jid}",
                "title": html.unescape(title),
                "location": _modern_location(resp),
                "posted": resp.get("unifiedStandardStart", "") or "",
                "url": f"https://{netloc}/job/{quote(str(url_title))}/{jid}-en_US",
                "company": company,
            })
        # totalJobs overcounts (location expansions), so stop on all-duplicate page.
        if new == 0:
            break
        page += 1
        polite_sleep(0.3)
    return jobs


def fetch_successfactors(board):
    parsed = urlparse(board["url"])
    company = board.get("name", parsed.netloc)
    jobs = _fetch_modern(board["url"], parsed.netloc, company)
    if jobs is not None:
        return jobs
    return _fetch_classic(parsed, company)


def _fetch_classic(parsed, company):
    """Classic RMK tile endpoint."""
    path = re.sub(r"/(search|searchjobs|SearchJobs)/?$", "/tile-search-results/",
                  parsed.path, flags=re.I)
    if "tile-search-results" not in path:
        path = path.rstrip("/") + "/tile-search-results/"
    base = f"https://{parsed.netloc}{path}"
    query = parsed.query
    ua = {"User-Agent": BROWSER_UA}

    tile_re = re.compile(
        r'<li class="job-tile job-id-(\d+).*?data-url="([^"]+)".*?'
        r'section-title title"[^>]*>(.*?)</span>', re.S)
    loc_re = re.compile(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>', re.S)

    jobs, startrow = [], 0
    while True:
        sep = "&" if query else ""
        r = HTTP.get(f"{base}?{query}{sep}startrow={startrow}", headers=ua, timeout=TIMEOUT)
        r.raise_for_status()
        tiles = re.findall(r"<li class=\"job-tile.*?</li>", r.text, re.S)
        if not tiles:
            break
        for t in tiles:
            m = tile_re.search(t)
            if not m:
                continue
            jid, data_url, title = m.group(1), m.group(2), m.group(3)
            ml = loc_re.search(t)
            if ml:
                location = re.sub(r"<[^>]+>|\s+", " ", ml.group(1)).strip()
            else:  # TELUS-style: recover City+PROV from slug
                parts = unquote(data_url).split("/job/")[-1].rsplit("/", 2)[0].split("-")
                city = parts[0] if parts else ""
                prov = next((p for p in reversed(parts) if p in CA_PROV), "")
                location = f"{city}, {prov}" if prov else city
            jobs.append({
                "id": f"sf:{parsed.netloc}:{jid}",
                "title": html.unescape(re.sub(r"<[^>]+>|\s+", " ", title)).strip(),
                "location": html.unescape(location).strip(),
                "posted": "",
                "url": f"https://{parsed.netloc}{data_url}",
                "company": company,
            })
        startrow += len(tiles)
        if len(tiles) < 10 or startrow > 2000:
            break
        polite_sleep(0.3)
    return jobs
