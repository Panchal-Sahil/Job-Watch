"""Workday ATS adapter."""

import re
import time
from urllib.parse import urlparse

import requests

from adapters.common import HEADERS


def fetch_workday(board):
    """Fetch all postings from one Workday board.

    A Workday URL looks like:
        https://acme.wd5.myworkdayjobs.com/en-US/External_Careers
    From it we derive the JSON endpoint:
        https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External_Careers/jobs
    which we POST to, paging via offset.
    """
    url = board["url"]
    parsed = urlparse(url)
    host = parsed.netloc  # acme.wd5.myworkdayjobs.com

    # The site slug is the path segment after the optional locale (en-US, en-CA…)
    # and, on the myworkdaysite.com variant, after a leading "recruiting/<tenant>".
    segs = [s for s in parsed.path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]  # drop locale

    # Tenant: from config override, else the "recruiting/<tenant>" path segment
    # (myworkdaysite.com), else the first hostname label (myworkdayjobs.com).
    tenant = board.get("tenant")
    if not tenant and len(segs) >= 2 and segs[0] == "recruiting":
        tenant = segs[1]
        segs = segs[2:]
    if not tenant:
        tenant = host.split(".")[0]  # acme

    site = board.get("site") or (segs[0] if segs else None)
    if not site:
        raise ValueError(f"Could not determine Workday site slug from URL: {url}")

    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    company = board.get("name", tenant)

    jobs = []
    offset, limit = 0, 20
    total = None  # Workday reports total only on the first page; capture it once.
    while True:
        body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        resp = requests.post(endpoint, headers=HEADERS, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        for p in postings:
            ext = p.get("externalPath", "")
            bullets = p.get("bulletFields") or [ext]
            jobs.append(
                {
                    "id": f"{tenant}:{bullets[0]}",
                    "title": p.get("title", "").strip(),
                    "location": p.get("locationsText", "").strip(),
                    "posted": p.get("postedOn", "").strip(),
                    "url": f"https://{host}{ext}" if ext else url,
                    "company": company,
                }
            )
        if total is None:
            total = data.get("total", 0)
        offset += limit
        if not postings or offset >= total:
            break
        time.sleep(0.5)  # be polite
    return jobs
