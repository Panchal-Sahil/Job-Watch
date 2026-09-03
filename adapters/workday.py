"""Workday ATS adapter."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from adapters.common import HEADERS, HTTP, polite_sleep, TIMEOUT

# Workday collapses multi-office postings into "2 Locations" placeholders.
_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)

_PAGE_LIMIT = 20  # server-enforced; limit=21+ is a hard 400

# Workday throttles by pod, not tenant — a single shared pool drew 429s from
# multiple wd5 tenants while wd3 was fine. Per-pod pools isolate the queues.
# 8 threads stays below the ~19 concurrent requests that never triggered throttling.
_PAGE_WORKERS = 8
_POOLS = {}
_POOLS_LOCK = threading.Lock()

# Limits how many boards on the same pod fetch concurrently from the outer pool.
# Without it, 20+ boards sharing wd5 fire page-0 simultaneously and draw 429s.
# At 6: 6 boards × 1 page-0 + 8 paging threads stays under the safe ~19 threshold.
POD_LIMIT = 6
_SEMS = {}
_SEMS_LOCK = threading.Lock()


def _sem_for(host):
    key = _pod(host)
    with _SEMS_LOCK:
        sem = _SEMS.get(key)
        if sem is None:
            sem = _SEMS[key] = threading.Semaphore(POD_LIMIT)
        return sem

# Absolute seconds, not scaled by DELAY_SCALE — the server is telling us to wait.
_MAX_ATTEMPTS = 4
_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


def _pod(host):
    """Last three host labels — acme.wd5.myworkdayjobs.com and
    other.wd5.myworkdayjobs.com share one pod and one rate limit."""
    return ".".join(host.split(".")[-3:])


def _pool_for(host):
    key = _pod(host)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = _POOLS[key] = ThreadPoolExecutor(
                max_workers=_PAGE_WORKERS, thread_name_prefix=f"wd-{key}")
        return pool


_RETRYABLE = (429, 502, 503, 520)


def _post_page(endpoint, offset):
    """POST one page, retrying transient errors. Non-retryable statuses left
    for the caller (underscore-tenant retry inspects 404/422 itself)."""
    body = {"appliedFacets": {}, "limit": _PAGE_LIMIT, "offset": offset, "searchText": ""}
    for attempt in range(_MAX_ATTEMPTS):
        resp = HTTP.post(endpoint, headers=HEADERS, json=body, timeout=TIMEOUT)
        if resp.status_code not in _RETRYABLE or attempt == _MAX_ATTEMPTS - 1:
            return resp
        try:
            wait = float(resp.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            wait = 0
        time.sleep(min(max(wait, _BACKOFF * 2 ** attempt), _MAX_BACKOFF))
    return resp


def _resolve_locations(host, tenant, site, ext):
    """Fetch a job's detail endpoint; return real locations or "" on failure."""
    try:
        detail = f"https://{host}/wday/cxs/{tenant}/{site}{ext}"
        resp = HTTP.get(detail, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        locs = [info.get("location", "").strip()] + [
            loc.strip() for loc in info.get("additionalLocations", [])
        ]
        return ", ".join(loc for loc in locs if loc)
    except Exception:
        return ""


def _fetch_page(endpoint, offset):
    """Fetch one page from the pod pool. Raises on bad status."""
    polite_sleep(0.5)
    resp = _post_page(endpoint, offset)
    resp.raise_for_status()
    return resp.json().get("jobPostings", [])


def fetch_workday(board):
    """Fetch all postings from one Workday board."""
    url = board["url"]
    parsed = urlparse(url)
    host = parsed.netloc

    segs = [s for s in parsed.path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]  # drop locale

    tenant = board.get("tenant")
    if not tenant and len(segs) >= 2 and segs[0] == "recruiting":
        tenant = segs[1]
        segs = segs[2:]
    from_host = not tenant
    if not tenant:
        tenant = host.split(".")[0]

    site = board.get("site") or (segs[0] if segs else None)
    if not site:
        raise ValueError(f"Could not determine Workday site slug from URL: {url}")

    endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    name = board.get("name")

    # Hostname can't carry underscores, so "vhr_genband" becomes "vhr-genband"
    # in the URL — retry once with underscores if Workday rejects it.
    retry_underscore = from_host and "-" in tenant

    # title_ok: advisory filter hint so we skip resolving locations for titles
    # that won't survive filtering. Ignoring it is always correct.
    title_ok = board.get("title_ok") or (lambda _t: True)

    with _sem_for(host):
        # Page 0 must be sequential — it settles the tenant and reports total.
        while True:
            resp = _post_page(endpoint, 0)
            if retry_underscore and resp.status_code in (404, 422):
                retry_underscore = False
                tenant = tenant.replace("-", "_")
                endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
                continue
            resp.raise_for_status()
            break
        data = resp.json()
        pages = [data.get("jobPostings", [])]
        total = data.get("total", 0)

        # Total is known — fan remaining pages out in parallel.
        if pages[0]:
            pages.extend(_pool_for(host).map(
                lambda off: _fetch_page(endpoint, off),
                range(_PAGE_LIMIT, total, _PAGE_LIMIT),
            ))

    jobs = []
    for postings in pages:
        for p in postings:
            ext = p.get("externalPath", "")
            bullets = p.get("bulletFields") or [ext]
            location = p.get("locationsText", "").strip()
            title = p.get("title", "").strip()
            if (ext and board.get("resolve_multi_location")
                    and _MULTI_LOC_RE.match(location) and title_ok(title)):
                resolved = _resolve_locations(host, tenant, site, ext)
                if resolved:
                    location = resolved
                polite_sleep(0.5)
            jobs.append(
                {
                    "id": f"{tenant}:{bullets[0]}",
                    "title": title,
                    "location": location,
                    "posted": p.get("postedOn", "").strip(),
                    "url": f"https://{host}/{site}{ext.removesuffix('/apply')}" if ext else url,
                    "company": name or tenant,
                }
            )
    return jobs
