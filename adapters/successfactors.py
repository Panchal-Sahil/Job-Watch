"""SAP SuccessFactors (RMK) ATS adapter.

SuccessFactors career sites ship in two template families. The **classic** RMK
template renders job *tiles* as HTML from a `tile-search-results/` GET; the
**modern** Career Site Builder (CSB) template renders client-side from a
`POST /services/recruiting/v1/jobs` JSON API and serves an empty stub to the old
tile endpoint. `fetch_successfactors` tries the JSON API first and falls back to
scraping tiles, so a board entry needs only its search URL regardless of template.
"""

import html
import json
import re
import time
from urllib.parse import quote, unquote, urlparse

import requests

from adapters.common import BROWSER_UA

# Canadian province/territory codes, used to recover clean locations from SF slugs.
CA_PROV = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}

# Job-fields that hold a location on the modern JSON API, tried in this order. The
# CSB config maps location onto an arbitrary custom field, so which key carries it
# varies per site (Deloitte uses `mfield1`, Teck uses `jobLocationShort`); when none
# of these is present we fall back to sniffing any field whose values look like
# "City, PROV" placenames.
_MODERN_LOC_KEYS = ("jobLocationShort", "displayLocation", "location", "city",
                    "mfield1", "mfield2", "mfield3")
_LOC_LIKE = re.compile(r"[A-Za-z].*,\s*[A-Z]{2}\b")


def _modern_location(resp):
    """Best-effort location string from a modern-API job. Cleans embedded markup
    (Teck ships `Red Dog, AK, USA, 99752<br/>`) and joins multi-site roles with
    '; ' so location filters have real placenames to match."""
    def clean(val):
        vals = val if isinstance(val, list) else [val]
        out = []
        for v in vals:
            v = re.sub(r"<[^>]+>", " ", str(v))
            v = re.sub(r"\s+", " ", v).strip().strip(",").strip()
            if v:
                out.append(v)
        return list(dict.fromkeys(out))  # dedupe, preserve order

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


def _fetch_modern(url, netloc, company):
    """Fetch jobs from a modern Career Site Builder board via its JSON API. Returns
    a normalized job list, or None if this site isn't the modern template (the API
    404s / 401s / lacks the expected shape) so the caller can fall back to classic
    tile scraping."""
    sess = requests.Session()
    sess.headers["User-Agent"] = BROWSER_UA
    # Warm the session on the board's own page to pick up any required cookies.
    try:
        sess.get(url, timeout=30)
    except requests.RequestException:
        return None
    api = f"https://{netloc}/services/recruiting/v1/jobs"
    jobs, seen, page = [], set(), 0
    while page < 200:  # safety cap
        body = json.dumps({"keywords": "", "locale": "en_US", "location": "",
                           "pageNumber": page, "sortBy": "recent"})
        r = sess.post(api, headers={"Content-Type": "application/json"},
                      data=body, timeout=30)
        # Classic sites answer this path with 401/404 and no results block — the
        # signal to abandon the modern path and let the caller scrape tiles.
        if not r.ok:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        if not isinstance(data, dict) or "jobSearchResult" not in data:
            return None
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
        # Stop when a page yields nothing new: `totalJobs` overcounts the distinct
        # postings (it counts location expansions), so paging by it re-serves the
        # last page. An all-duplicate/empty page means we've walked the whole board.
        if new == 0:
            break
        page += 1
        time.sleep(0.3)
    return jobs


def fetch_successfactors(board):
    """SAP SuccessFactors career site. Tries the modern Career Site Builder JSON API
    first, then falls back to scraping the classic RMK tile endpoint."""
    parsed = urlparse(board["url"])
    company = board.get("name", parsed.netloc)
    jobs = _fetch_modern(board["url"], parsed.netloc, company)
    if jobs is not None:
        return jobs
    return _fetch_classic(parsed, company)


def _fetch_classic(parsed, company):
    """Classic RMK career site. Hits the tile-search-results endpoint, preserving the
    board URL's own query string (its Canada/student facets), and parses the returned
    HTML tiles."""
    # Swap the search path segment for the tile-results endpoint.
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
        r = requests.get(f"{base}?{query}{sep}startrow={startrow}", headers=ua, timeout=30)
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
            else:  # TELUS-style templates hide location; recover City+PROV from slug
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
        if len(tiles) < 10 or startrow > 2000:  # last page / safety cap
            break
        time.sleep(0.3)
    return jobs
