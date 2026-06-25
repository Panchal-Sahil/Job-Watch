"""SAP SuccessFactors (RMK) ATS adapter."""

import html
import re
import time
from urllib.parse import unquote, urlparse

import requests

from adapters.common import BROWSER_UA

# Canadian province/territory codes, used to recover clean locations from SF slugs.
CA_PROV = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}


def fetch_successfactors(board):
    """SAP SuccessFactors (RMK) career site. Hits the tile-search-results endpoint,
    preserving the board URL's own query string (its Canada/student facets), and
    parses the returned HTML tiles."""
    parsed = urlparse(board["url"])
    company = board.get("name", parsed.netloc)
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
