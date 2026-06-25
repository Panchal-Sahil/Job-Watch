#!/usr/bin/env python3
"""jobwatch — poll company ATS boards and print newly-posted jobs that match your filters.

Run it whenever you like:  python3 jobwatch.py
It remembers which jobs it has already shown you (seen.json), so each run only
prints what's NEW since last time.

Currently supports Workday boards. Other ATS types (Greenhouse, Lever, Ashby)
plug in as small adapters following the same shape — see ADAPTERS at the bottom.
"""

import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

from adapters.ashby import fetch_ashby
from adapters.common import HEADERS, _slug_from_url
from adapters.workday import fetch_workday

MAX_WORKERS = 8  # how many boards to fetch at once

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SEEN_PATH = HERE / "seen.json"


# --------------------------------------------------------------------------- #
# Normalized job shape every adapter returns:
#   { "id", "title", "location", "posted", "url", "company" }
# --------------------------------------------------------------------------- #


def fetch_greenhouse(board):
    """Greenhouse public board API.
    URL like https://job-boards.greenhouse.io/<token>  ->  token."""
    token = _slug_from_url(board["url"], "token", board)
    company = board.get("name", token)
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    resp = requests.get(api, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    jobs = []
    for p in resp.json().get("jobs", []):
        jobs.append(
            {
                "id": f"gh:{token}:{p.get('id')}",
                "title": (p.get("title") or "").strip(),
                "location": (p.get("location") or {}).get("name", "").strip(),
                "posted": (p.get("updated_at") or "")[:10],
                "url": p.get("absolute_url", board["url"]),
                "company": company,
            }
        )
    return jobs


def fetch_lever(board):
    """Lever public postings API.
    URL like https://jobs.lever.co/<company>  ->  company."""
    slug = _slug_from_url(board["url"], "company", board)
    company = board.get("name", slug)
    api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = requests.get(api, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    jobs = []
    for p in resp.json():
        cats = p.get("categories") or {}
        created = p.get("createdAt")
        posted = (
            time.strftime("%Y-%m-%d", time.gmtime(created / 1000)) if created else ""
        )
        jobs.append(
            {
                "id": f"lever:{slug}:{p.get('id')}",
                "title": (p.get("text") or "").strip(),
                "location": (cats.get("location") or "").strip(),
                "posted": posted,
                "url": p.get("hostedUrl", board["url"]),
                "company": company,
            }
        )
    return jobs


def fetch_smartrecruiters(board):
    """SmartRecruiters public Posting API.
    URL like https://careers.smartrecruiters.com/<company>/ -> company."""
    company_id = _slug_from_url(board["url"], "company", board)
    company = board.get("name", company_id)
    api = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
    jobs = []
    offset, limit, total = 0, 100, None
    while True:
        r = requests.get(api, headers={"User-Agent": BROWSER_UA},
                         params={"limit": limit, "offset": offset}, timeout=30)
        r.raise_for_status()
        data = r.json()
        postings = data.get("content", [])
        for p in postings:
            loc = p.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                             loc.get("country")) if x)
            jid = p.get("id")
            jobs.append({
                "id": f"smartrecruiters:{company_id}:{jid}",
                "title": (p.get("name") or "").strip(),
                "location": location,
                "posted": (p.get("releasedDate") or "")[:10],
                "url": f"https://jobs.smartrecruiters.com/{company_id}/{jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalFound", 0)
        offset += limit
        if not postings or offset >= total:
            break
        time.sleep(0.3)
    return jobs


def fetch_bamboohr(board):
    """BambooHR public careers API.
    URL like https://<sub>.bamboohr.com/careers -> uses that host."""
    host = urlparse(board["url"]).netloc
    sub = host.split(".")[0]
    company = board.get("name", sub)
    r = requests.get(f"https://{host}/careers/list",
                     headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json().get("result", []):
        loc = p.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        jid = p.get("id")
        jobs.append({
            "id": f"bamboo:{sub}:{jid}",
            "title": (p.get("jobOpeningName") or "").strip(),
            "location": location or ("Remote" if p.get("isRemote") else ""),
            "posted": "",
            "url": f"https://{host}/careers/{jid}",
            "company": company,
        })
    return jobs


def fetch_rippling(board):
    """Rippling ATS public board API.
    URL like https://ats.rippling.com/<slug>/jobs -> slug."""
    segs = [s for s in urlparse(board["url"]).path.split("/") if s]
    slug = board.get("board") or (segs[0] if segs else None)
    company = board.get("name", slug)
    api = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    r = requests.get(api, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
                     timeout=30)
    r.raise_for_status()
    jobs = []
    for p in r.json():
        jobs.append({
            "id": f"rippling:{slug}:{p.get('uuid')}",
            "title": (p.get("name") or "").strip(),
            "location": (p.get("workLocation") or {}).get("label", ""),
            "posted": "",
            "url": p.get("url", board["url"]),
            "company": company,
        })
    return jobs


def fetch_ukg(board):
    """UKG Pro Recruiting (UltiPro) public job board API.
    URL like https://recruiting.ultipro.ca/<TENANT>/JobBoard/<guid>/ -> both."""
    parsed = urlparse(board["url"])
    host = parsed.netloc
    segs = [s for s in parsed.path.split("/") if s]
    tenant = segs[0]
    guid = segs[segs.index("JobBoard") + 1] if "JobBoard" in segs else segs[2]
    company = board.get("name", tenant)
    api = (f"https://{host}/{tenant}/JobBoard/{guid}/JobBoardView/LoadSearchResults")
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/json",
               "Content-Type": "application/json"}

    jobs, skip, total = [], 0, None
    while True:
        body = {"opportunitySearch": {"Top": 100, "Skip": skip, "QueryString": "",
                                      "OrderBy": [], "Filters": []}}
        r = requests.post(api, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        opps = data.get("opportunities", [])
        for p in opps:
            locs = "; ".join(l.get("LocalizedDescription", "") for l in p.get("Locations", []))
            jid = p.get("Id")
            jobs.append({
                "id": f"ukg:{tenant}:{jid}",
                "title": (p.get("Title") or "").strip(),
                "location": locs,
                "posted": (p.get("PostedDate") or "")[:10],
                "url": f"https://{host}/{tenant}/JobBoard/{guid}/OpportunityDetail?opportunityId={jid}",
                "company": company,
            })
        if total is None:
            total = data.get("totalCount", 0)
        skip += 100
        if not opps or skip >= total:
            break
        time.sleep(0.3)
    return jobs


def fetch_oracle(board):
    """Oracle Recruiting Cloud (Candidate Experience) public REST API.
    URL like https://HOST/hcmUI/CandidateExperience/en/sites/SITE/jobs -> HOST + SITE.
    Note: vanity domains (e.g. jobs.nokia.com) proxy the UI but not the API, so
    they need the real `*.oraclecloud.com` host passed via "host" in config."""
    parsed = urlparse(board["url"])
    host = board.get("host") or parsed.netloc
    company = board.get("name", host)
    segs = [s for s in parsed.path.split("/") if s]
    site = board.get("site")
    if not site and "sites" in segs:
        site = segs[segs.index("sites") + 1]
    if not site:
        raise ValueError(f"Could not find Oracle site in URL: {board['url']}")

    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    jobs = []
    offset, limit, total = 0, 200, None
    while True:
        finder = (f"findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,"
                  f"limit={limit},offset={offset}")
        params = {"onlyData": "true",
                  "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                  "finder": finder}
        r = requests.get(api, headers={"User-Agent": BROWSER_UA}, params=params, timeout=30)
        r.raise_for_status()
        item = (r.json().get("items") or [{}])[0]
        reqs = item.get("requisitionList", [])
        for p in reqs:
            jid = p.get("Id")
            jobs.append({
                "id": f"oracle:{host}:{jid}",
                "title": (p.get("Title") or "").strip(),
                "location": (p.get("PrimaryLocation") or "").strip(),
                "posted": (p.get("PostedDate") or "")[:10],
                "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}",
                "company": company,
            })
        if total is None:
            total = item.get("TotalJobsCount", 0)
        offset += limit
        if not reqs or offset >= total:
            break
        time.sleep(0.3)
    return jobs


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


# Default search terms used to narrow large keyword-driven boards (Phenom / SF)
# down to early-careers roles instead of pulling the whole company.
EARLY_CAREERS_TERMS = ["intern", "co-op", "coop", "student", "graduate", "apprentice"]

# Canadian province/territory codes, used to recover clean locations from SF slugs.
CA_PROV = {"ON", "BC", "QC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _extract_js_object(text, marker):
    """Pull the first balanced {...} JSON object appearing after `marker`."""
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[i : j + 1])
    return None


def fetch_phenom(board):
    """Phenom People career site. Reads the site's `phApp` config off the landing
    page, then queries its /widgets endpoint with early-careers keywords."""
    url = board["url"]
    host = urlparse(url).netloc
    company = board.get("name", host)
    ua = {"User-Agent": BROWSER_UA}

    page = requests.get(url, headers=ua, timeout=30)
    page.raise_for_status()
    cfg = _extract_js_object(page.text, "var phApp") or {}
    endpoint = cfg.get("widgetApiEndpoint") or f"https://{host}/widgets"
    locale = cfg.get("locale", "en_global")
    country = cfg.get("country", "global")
    page_id = cfg.get("pageId", "page1")

    terms = board.get("query") or EARLY_CAREERS_TERMS
    if isinstance(terms, str):
        terms = [terms]

    by_id = {}
    headers = {"User-Agent": BROWSER_UA, "Content-Type": "application/json"}
    for term in terms:
        frm = 0
        while True:
            payload = {
                "lang": locale, "deviceType": "desktop", "country": country,
                "pageName": "search-results", "ddoKey": "refineSearch",
                "from": frm, "jobs": True, "counts": True,
                "all_fields": ["country", "state", "city", "category"],
                "size": 100, "clearAll": False, "jdsource": "facets",
                "pageId": page_id, "siteType": "external", "keywords": term,
                "global": True, "selected_fields": {},
                "sort": {"order": "", "field": ""}, "locationData": {},
            }
            r = requests.post(endpoint, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            rs = r.json().get("refineSearch", {})
            data = rs.get("data", {}) or {}
            postings = data.get("jobs", [])
            for p in postings:
                jid = p.get("jobId") or p.get("jobSeqNo")
                by_id[jid] = {
                    "id": f"phenom:{host}:{jid}",
                    "title": html.unescape(p.get("title") or "").strip(),
                    "location": (p.get("cityStateCountry") or p.get("cityState")
                                 or p.get("location") or "").strip(),
                    "posted": (p.get("postedDate") or p.get("dateCreated") or "")[:10],
                    "url": p.get("applyUrl", url),
                    "company": company,
                }
            frm += 100
            if not postings or frm >= rs.get("totalHits", 0):
                break
            time.sleep(0.3)
    return list(by_id.values())


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
                from urllib.parse import unquote
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


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def _word_match(keyword, text):
    """True if keyword appears as a whole word in text. Allows the plural/`-ship`
    forms (intern -> interns/internship) but NOT longer unrelated words, so
    "intern" matches "Internship" but not "Internal"/"International"."""
    pat = r"\b" + re.escape(keyword) + r"(s|ship|ships)?\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def _keyword_match(keyword, text):
    """Match one filter keyword against a title. A keyword starting with `re:` is
    treated as a raw, case-sensitive regex (used for things like a trailing
    entry-level roman numeral, `re:\\bI{1,2}\\b`); otherwise it's a whole-word,
    case-insensitive match."""
    if keyword.startswith("re:"):
        return re.search(keyword[3:], text) is not None
    return _word_match(keyword, text)


def matches(job, filters):
    title = job["title"]
    loc = job["location"].lower()

    # title_groups: a list of keyword-lists. The title must match at least one
    # keyword in EVERY group (AND across groups, OR within a group). Use this to
    # require e.g. (early-career) AND (tech domain). Falls back to the simpler
    # title_any (a single OR group) if title_groups isn't set.
    groups = filters.get("title_groups")
    if groups:
        for group in groups:
            if not any(_keyword_match(k, title) for k in group):
                return False
    else:
        title_any = filters.get("title_any", [])
        if title_any and not any(_keyword_match(k, title) for k in title_any):
            return False

    title_none = filters.get("title_none", [])
    if any(_keyword_match(k, title) for k in title_none):
        return False

    loc_any = [k.lower() for k in filters.get("location_any", [])]
    if loc_any and not any(k in loc for k in loc_any):
        return False

    return True


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #

ADAPTERS = {
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "phenom": fetch_phenom,
    "successfactors": fetch_successfactors,
    "oracle": fetch_oracle,
    "radancy": fetch_radancy,
    "smartrecruiters": fetch_smartrecruiters,
    "bamboohr": fetch_bamboohr,
    "rippling": fetch_rippling,
    "ukg": fetch_ukg,
}


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def fetch_board(board):
    """Fetch one board. Returns (name, jobs, error) — never raises, so one bad
    board can't sink the whole run. Safe to call from worker threads."""
    name = board.get("name", board.get("url", "?"))
    kind = board.get("type", "workday")
    adapter = ADAPTERS.get(kind)
    if not adapter:
        return name, [], f"no adapter for type '{kind}'"
    try:
        return name, adapter(board), None
    except Exception as e:
        return name, [], str(e)


def main():
    if not CONFIG_PATH.exists():
        sys.exit(
            f"No config found. Copy config.example.json to {CONFIG_PATH.name} and add your boards."
        )

    config = load_json(CONFIG_PATH, {})
    filters = config.get("filters", {})
    boards = config.get("boards", [])
    workers = config.get("max_workers", MAX_WORKERS)
    seen = set(load_json(SEEN_PATH, []))

    # Fetch every board concurrently; total time ~= the slowest single board.
    total = len(boards)
    print(f"Fetching {total} boards (up to {workers} at a time)...", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_board, b) for b in boards]
        for done, fut in enumerate(as_completed(futures), 1):
            name, jobs, error = fut.result()
            results.append((name, jobs, error))
            status = f"{len(jobs)} jobs" if error is None else f"ERROR: {error[:50]}"
            print(f"  [{done:2}/{total}] {name:32} {status}", flush=True)

    # Diff + filter sequentially (fast, and keeps `seen` mutation single-threaded).
    new_jobs = []
    for name, jobs, error in results:
        if error:
            print(f"  ! {name}: {error}", file=sys.stderr)
            continue
        for job in jobs:
            if job["id"] in seen:
                continue
            seen.add(job["id"])  # mark seen even if filtered out, so it stays quiet next time
            if matches(job, filters):
                new_jobs.append(job)

    if new_jobs:
        print(f"\n  {len(new_jobs)} new matching job(s):\n")
        for job in sorted(new_jobs, key=lambda j: (j["company"], j["title"])):
            print(f"  • {job['title']}")
            print(f"      {job['company']} — {job['location']}  ({job['posted']})")
            print(f"      {job['url']}\n")
    else:
        print("  No new matching jobs.")

    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=0))


if __name__ == "__main__":
    main()
