"""Workday ATS adapter."""

import re
from urllib.parse import urlparse

import requests

from adapters.common import HEADERS, polite_sleep

# Workday's list endpoint collapses a posting tied to several offices into a count
# placeholder ("2 Locations") instead of city names — which defeats location filters.
_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)


def _resolve_locations(host, tenant, site, ext):
    """Fetch a single job's detail endpoint and return its real locations joined as
    "City A, City B". Returns "" on any failure so the caller keeps the placeholder."""
    try:
        detail = f"https://{host}/wday/cxs/{tenant}/{site}{ext}"
        resp = requests.get(detail, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        locs = [info.get("location", "").strip()] + [
            loc.strip() for loc in info.get("additionalLocations", [])
        ]
        return ", ".join(loc for loc in locs if loc)
    except Exception:
        return ""


def fetch_workday(board):
    """Fetch all postings from one Workday board.

    A Workday URL looks like:
        https://acme.wd5.myworkdayjobs.com/en-US/External_Careers
    From it we derive the JSON endpoint:
        https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External_Careers/jobs
    which we POST to, paging via offset.

    The tenant is taken from the hostname unless config pins it or the URL carries a
    "recruiting/<tenant>" segment; a hostname-derived tenant is retried once with its
    hyphens turned back into underscores if Workday rejects it (see below).
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
    from_host = not tenant
    if not tenant:
        tenant = host.split(".")[0]  # acme

    site = board.get("site") or (segs[0] if segs else None)
    if not site:
        raise ValueError(f"Could not determine Workday site slug from URL: {url}")

    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    name = board.get("name")

    # A tenant may contain an underscore, which a hostname label can't: tenant
    # "vhr_genband" is served from vhr-genband.wd1.myworkdayjobs.com, and asking for
    # the hyphenated name gets a 422. The two are indistinguishable in the URL, so
    # when the tenant came from the hostname, retry once with the underscores back.
    # (Only that path — a pinned or recruiting/<tenant> tenant is already literal.)
    retry_underscore = from_host and "-" in tenant

    # Resolving a "N Locations" placeholder costs a request per posting, and on a big
    # board most postings are filtered out on their title alone — in which case the
    # real location can't change the outcome. jobwatch supplies the title half of its
    # filter here so we only pay for postings that could actually be surfaced.
    # Absent (probe, tests), everything is a candidate — same behavior as before.
    title_ok = board.get("title_ok") or (lambda _t: True)

    jobs = []
    offset, limit = 0, 20
    total = None  # Workday reports total only on the first page; capture it once.
    while True:
        body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        resp = requests.post(endpoint, headers=HEADERS, json=body, timeout=30)
        if retry_underscore and resp.status_code in (404, 422):
            retry_underscore = False
            tenant = tenant.replace("-", "_")
            endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
            continue  # same offset — nothing was consumed
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        for p in postings:
            ext = p.get("externalPath", "")
            bullets = p.get("bulletFields") or [ext]
            location = p.get("locationsText", "").strip()
            title = p.get("title", "").strip()
            # Resolve "N Locations" placeholders to real city names (one extra
            # request per affected posting) when enabled in config — but only for
            # titles that could survive the filter (see title_ok above).
            if (ext and board.get("resolve_multi_location")
                    and _MULTI_LOC_RE.match(location) and title_ok(title)):
                resolved = _resolve_locations(host, tenant, site, ext)
                if resolved:
                    location = resolved
                polite_sleep(0.5)  # be polite about the extra detail fetch
            jobs.append(
                {
                    "id": f"{tenant}:{bullets[0]}",
                    "title": title,
                    "location": location,
                    "posted": p.get("postedOn", "").strip(),
                    "url": f"https://{host}{ext}" if ext else url,
                    "company": name or tenant,
                }
            )
        if total is None:
            total = data.get("total", 0)
        offset += limit
        if not postings or offset >= total:
            break
        polite_sleep(0.5)  # be polite
    return jobs
