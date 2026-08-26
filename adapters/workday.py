"""Workday ATS adapter."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from adapters.common import HEADERS, HTTP, polite_sleep, TIMEOUT

# Workday's list endpoint collapses a posting tied to several offices into a count
# placeholder ("2 Locations") instead of city names — which defeats location filters.
_MULTI_LOC_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)

# Server-enforced: limit=21 and above is a hard HTTP 400 (verified on three tenants).
# A 4,000-job board is therefore 200+ requests, so they are issued in parallel once
# page 0 reports the total — see the pod pools below.
_PAGE_LIMIT = 20

# Pages are fanned out over a pool per Workday *pod* (the `wd5.myworkdayjobs.com` half
# of the hostname), not one per board and not one shared by all.
#
# Workday throttles by pod, not by tenant: fanning every board's pages over a single
# 32-thread pool drew 429s from four separate wd5 tenants in one run, while wd3 — more
# boards, comparable request volume, but spread over many small boards — was fine. A
# pod is one queue on their side, so it gets one bounded queue on ours. Sizing:
#
#   * per-pod, not global — one pod's backlog can't starve another's pages, which a
#     single shared pool with a per-pod semaphore would allow (every thread parked
#     waiting on the busy pod);
#   * per-pod, not per-board — a per-board pool multiplies threads by boards in flight,
#     and lets several tenants on one pod burst at it simultaneously, which is the
#     thing that drew the 429s;
#   * 8 deep, below the ~19 concurrent requests one pod already absorbed under
#     sequential paging (one in flight per board, all boards at once, never throttled).
#
# Peak threads: `max_workers` outer + 8 per pod actually paging (8 pods in config, and
# pools are created lazily, so in practice far fewer). Page tasks only issue HTTP,
# never submitting back into a pool, so an outer thread waiting on one cannot deadlock.
_PAGE_WORKERS = 8
_POOLS = {}
_POOLS_LOCK = threading.Lock()

# Per-pod concurrency limit for board-level fetching. The pod pools limit
# *paging* concurrency within a pod; this limits how many boards on the same
# pod can be fetching at once from the outer thread pool. Without it, 20+
# boards sharing a pod (wd5 in practice) fire page-0 requests simultaneously
# — the resulting burst draws 429s that the retry loop can't clear because
# every board backs off and retries in sync. At the default (6), 6 boards ×
# 1 page-0 request each plus 8 paging threads stays under the ~19 concurrent
# requests per pod that never triggered throttling. Override via
# `workday_pod_limit` in config.json.
POD_LIMIT = 6
_SEMS = {}
_SEMS_LOCK = threading.Lock()


def _sem_for(host):
    """A bounded semaphore for `host`'s pod, created on first use."""
    key = _pod(host)
    with _SEMS_LOCK:
        sem = _SEMS.get(key)
        if sem is None:
            sem = _SEMS[key] = threading.Semaphore(POD_LIMIT)
        return sem

# A 429 costs the whole board, so back off and retry rather than fail. Absolute
# seconds, deliberately not scaled by DELAY_SCALE: this is the server telling us to
# wait, not our own politeness margin.
_MAX_ATTEMPTS = 4
_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


def _pod(host):
    """The pod a Workday host belongs to — `acme.wd5.myworkdayjobs.com` and
    `other.wd5.myworkdayjobs.com` share one, and so share a rate limit.
    (`wd3.myworkdaysite.com` has no tenant label; the last three labels cover both.)"""
    return ".".join(host.split(".")[-3:])


def _pool_for(host):
    """The page pool for `host`'s pod, created on first use."""
    key = _pod(host)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = _POOLS[key] = ThreadPoolExecutor(
                max_workers=_PAGE_WORKERS, thread_name_prefix=f"wd-{key}")
        return pool


_RETRYABLE = (429, 502, 503, 520)


def _post_page(endpoint, offset):
    """POST one page of the job list, retrying on transient errors (429, 502, 503, 520).

    Returns the response. Any other bad status is left for the caller to raise on,
    so the underscore-tenant retry can still inspect a 404/422 itself.
    """
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
    return resp  # unreachable: the loop returns on its last attempt


def _resolve_locations(host, tenant, site, ext):
    """Fetch a single job's detail endpoint and return its real locations joined as
    "City A, City B". Returns "" on any failure so the caller keeps the placeholder."""
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
    """Fetch one page and return its `jobPostings` (possibly empty).

    Used for every page after the first, from the pod's pool. Raises on a bad status,
    which the caller lets propagate so the board fails as a whole.
    """
    polite_sleep(0.5)  # stagger the fan-out, same weight as the old per-page pause
    resp = _post_page(endpoint, offset)
    resp.raise_for_status()
    return resp.json().get("jobPostings", [])


def fetch_workday(board):
    """Fetch all postings from one Workday board.

    A Workday URL looks like:
        https://acme.wd5.myworkdayjobs.com/en-US/External_Careers
    From it we derive the JSON endpoint:
        https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External_Careers/jobs
    which we POST to, paging via offset. Page 0 is fetched first for its `total`;
    every remaining offset is then known, so they go out in parallel (see
    `_fetch_page`) instead of one round-trip after another.

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

    with _sem_for(host):
        # Page 0 stays sequential: it settles the underscore retry (which rewrites the
        # endpoint) and reports `total`, and both have to be known before the remaining
        # offsets can be computed and issued at once.
        while True:
            resp = _post_page(endpoint, 0)
            if retry_underscore and resp.status_code in (404, 422):
                retry_underscore = False
                tenant = tenant.replace("-", "_")
                endpoint = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
                continue  # same offset — nothing was consumed
            resp.raise_for_status()
            break
        data = resp.json()
        pages = [data.get("jobPostings", [])]
        total = data.get("total", 0)

        # Workday pages by numeric offset and `total` is known now, so the rest of the
        # requests are all computable up front rather than one-after-another. Ordering
        # the results by offset keeps the returned list identical to the serial version;
        # `map` also re-raises the first page's failure, so a bad page still fails the
        # whole board rather than quietly returning a short list.
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
                    "url": f"https://{host}/{site}{ext.removesuffix('/apply')}" if ext else url,
                    "company": name or tenant,
                }
            )
    return jobs
