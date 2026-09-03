"""Radancy / TalentBrew career site adapter."""

import html
import re
from urllib.parse import urlparse

from adapters.common import BROWSER_UA, HTTP, polite_sleep, TIMEOUT


def fetch_radancy(board):
    """Radancy / TalentBrew. Server-rendered HTML, paginated with `?p=N`."""
    parsed = urlparse(board["url"])
    host = parsed.netloc
    company = board.get("name", host)
    base = f"https://{host}{parsed.path}"
    query = parsed.query
    ua = {"User-Agent": BROWSER_UA}

    # Three template variants: title from data-title / heading / link text;
    # location from a *location* span inside the <a> or in the block after it.
    anchor_re = re.compile(r'<a\s+([^>]*\bdata-job-id="[^"]+"[^>]*)>(.*?)</a>', re.S)
    title_re = re.compile(r'class="[^"]*title[^"]*"[^>]*>(.*?)</', re.S)
    # Close on any tag: location is <span> in some templates, <li> in others.
    loc_re = re.compile(r'class="[^"]*location[^"]*"[^>]*>(.*?)</', re.S)

    def attr(tag, name):
        m = re.search(name + r'="([^"]*)"', tag)
        return m.group(1) if m else ""

    jobs, seen_ids, page = [], set(), 1
    while True:
        sep = "&" if query else ""
        r = HTTP.get(f"{base}?{query}{sep}p={page}", headers=ua, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text
        new = 0
        matches = list(anchor_re.finditer(text))
        for i, m in enumerate(matches):
            tag, body = m.group(1), m.group(2)
            href, jid = attr(tag, "href"), attr(tag, "data-job-id")
            if "/job/" not in href or jid in seen_ids:
                continue
            seen_ids.add(jid)
            new += 1
            tm = title_re.search(body)
            title = (attr(tag, "data-title")
                     or (tm.group(1) if tm else "")
                     or re.sub(r"<[^>]+>", "", body))
            # Location from <a> body or block after it; bounded by next anchor
            # (or 2000 chars) so a missing location can't bleed from a neighbor.
            nxt = (matches[i + 1].start() if i + 1 < len(matches)
                   else min(len(text), m.end() + 2000))
            ml = loc_re.search(body) or loc_re.search(text[m.end():nxt])
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
        polite_sleep(0.3)
    return jobs
