"""Radancy / TalentBrew career site adapter."""

import html
import re
import time
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA


def fetch_radancy(board):
    """Radancy / TalentBrew career site (the `/search-jobs?orgIds=...` platform).
    Jobs are server-rendered into the main search page; we preserve the board URL's
    own query string (orgIds + location filter) and paginate with `?p=N`."""
    parsed = urlparse(board["url"])
    host = parsed.netloc
    company = board.get("name", host)
    base = f"https://{host}{parsed.path}"
    query = parsed.query
    ua = {"User-Agent": BROWSER_UA}

    # Two Radancy templates exist: an older one (data-title attr, location span
    # inside the <a>) and a newer one (title is the link text, location is a
    # sibling span after it). This handles both by matching the job <a> tag, then
    # looking for a `*job-location*` span in the link body OR the text just after.
    anchor_re = re.compile(r'<a\s+([^>]*\bdata-job-id="[^"]+"[^>]*)>(.*?)</a>', re.S)
    loc_re = re.compile(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>', re.S)

    def attr(tag, name):
        m = re.search(name + r'="([^"]*)"', tag)
        return m.group(1) if m else ""

    jobs, seen_ids, page = [], set(), 1
    while True:
        sep = "&" if query else ""
        r = requests.get(f"{base}?{query}{sep}p={page}", headers=ua, timeout=30)
        r.raise_for_status()
        text = r.text
        new = 0
        for m in anchor_re.finditer(text):
            tag, body = m.group(1), m.group(2)
            href, jid = attr(tag, "href"), attr(tag, "data-job-id")
            if "/job/" not in href or jid in seen_ids:
                continue
            seen_ids.add(jid)
            new += 1
            title = attr(tag, "data-title") or re.sub(r"<[^>]+>", "", body)
            ml = loc_re.search(body) or loc_re.search(text[m.end():m.end() + 300])
            location = re.sub(r"<[^>]+>|\s+", " ", ml.group(1)).strip() if ml else ""
            jobs.append({
                "id": f"radancy:{host}:{jid}",
                "title": html.unescape(re.sub(r"\s+", " ", title)).strip(),
                "location": html.unescape(location),
                "posted": "",
                "url": f"https://{host}{href}",
                "company": company,
            })
        if new == 0 or page > 40:  # no fresh items (last page) / safety cap
            break
        page += 1
        time.sleep(0.3)
    return jobs
