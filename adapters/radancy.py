"""Radancy / TalentBrew career site adapter."""

import html
import re
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA, polite_sleep


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

    # Three Radancy templates exist: (A) data-title attr + location span inside the
    # <a>; (B) title is the link text, location is a sibling span after it; (C) a
    # heading (`*job-title*`) + a `*result-location*` span inside the <a>. Match the
    # job <a> tag, then pull the title from data-title -> a `*title*` heading in the
    # body -> the stripped body, and the location from any `*location*` span in the
    # body OR the text just after (class names vary: job-location / result-location).
    anchor_re = re.compile(r'<a\s+([^>]*\bdata-job-id="[^"]+"[^>]*)>(.*?)</a>', re.S)
    title_re = re.compile(r'class="[^"]*title[^"]*"[^>]*>(.*?)</', re.S)
    # Close on any tag, not just </span>: the location element is a <span> in some
    # templates and an <li> in others (Capital One). Inner tags are stripped below.
    loc_re = re.compile(r'class="[^"]*location[^"]*"[^>]*>(.*?)</', re.S)

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
            # Location: inside the <a> body (templates A/C), else in the block
            # rendered after </a> (templates B/D) — bounded by the next anchor so a
            # job with no location can't borrow the following job's. For the last
            # anchor there's no next one to bound against; cap the window so a
            # location-less last job can't reach a footer "jobs by location" widget.
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
