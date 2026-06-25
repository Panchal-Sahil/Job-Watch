"""Dayforce (Ceridian) ATS adapter.

Dayforce career sites are SSR Next.js apps fronted by NextAuth + Cloudflare. The
job list comes from a JSON search API, but it requires a CSRF token:

  1. GET the landing page         -> sets the __cf_bm cookie
  2. GET /api/auth/csrf           -> NextAuth { "csrfToken": ... }
  3. POST /api/geo/<ns>/jobposting/search  with header X-CSRF-TOKEN
     (without it the API returns 403 Forbidden)

URL like https://jobs.dayforcehcm.com/<locale>/<namespace>/<jobBoardCode>/jobs/<id>
-> locale (en-CA), namespace (sobeys), jobBoardCode (privateclientsite).
"""

import re
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA

PAGE_SIZE = 25  # Dayforce fixes the search page size at 25


def fetch_dayforce(board):
    parsed = urlparse(board["url"])
    host = parsed.netloc  # jobs.dayforcehcm.com
    segs = [s for s in parsed.path.split("/") if s]

    # Optional leading locale segment (en-CA, fr-CA, en-US...).
    locale = board.get("locale")
    if not locale and segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        locale = segs[0]
        segs = segs[1:]
    locale = locale or "en-US"

    namespace = board.get("namespace") or (segs[0] if segs else None)
    job_board_code = board.get("board") or (segs[1] if len(segs) >= 2 else None)
    if not namespace or not job_board_code:
        raise ValueError(f"Could not parse Dayforce namespace/board from URL: {board['url']}")
    company = board.get("name", namespace)

    sess = requests.Session()
    sess.headers["User-Agent"] = BROWSER_UA
    referer = f"https://{host}/{locale}/{namespace}/{job_board_code}"

    # 1) prime cookies, 2) fetch the NextAuth CSRF token
    sess.get(referer, timeout=30).raise_for_status()
    csrf = sess.get(f"https://{host}/api/auth/csrf", timeout=30)
    csrf.raise_for_status()
    token = csrf.json().get("csrfToken", "")

    api = f"https://{host}/api/geo/{namespace}/jobposting/search"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": f"https://{host}",
        "Referer": referer,
        "X-CSRF-TOKEN": token,
    }

    jobs, start = [], 0
    while True:
        body = {
            "clientNamespace": namespace,
            "jobBoardCode": job_board_code,
            "cultureCode": locale,
            "searchText": "",
            "distanceUnit": 0 if locale.lower().startswith("fr") else 1,
            "paginationStart": start,
        }
        r = sess.post(api, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        for p in postings:
            jid = p.get("jobPostingId")
            locs = "; ".join(
                ", ".join(x for x in (loc.get("cityName"), loc.get("stateCode")) if x)
                or loc.get("formattedAddress", "")
                for loc in (p.get("postingLocations") or [])
            )
            jobs.append({
                "id": f"dayforce:{namespace}:{jid}",
                "title": (p.get("jobTitle") or "").strip(),
                "location": locs,
                "posted": (p.get("postingStartTimestampUTC") or "")[:10],
                "url": f"https://{host}/{locale}/{namespace}/{job_board_code}/jobs/{jid}",
                "company": company,
            })
        start = data.get("offset", start) + data.get("count", len(postings))
        if not postings or start >= data.get("maxCount", 0):
            break
    return jobs
