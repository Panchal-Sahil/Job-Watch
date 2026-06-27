#!/usr/bin/env python3
"""probe.py — point it at a careers URL, find out what ATS the site runs.

    python3 probe.py <url> [--name "Display Name"] [--add]
    python3 probe.py "Display Name, <url>" [--add]   # pin the name inline
    # ...or separate the name from the url with a tab / 2+ spaces (a pasted
    #    `Name<TAB>url` list works as-is, e.g. via --file)

It fetches the page and figures out the Applicant Tracking System behind it:

  1. Host match     — the URL (or where it redirects) is already an ATS domain
                      (myworkdayjobs.com, icims.com, jobs.lever.co, ...).
  2. Embedded ATS   — a custom careers domain (careers.acme.com) that white-labels
                      a Greenhouse/Lever/Ashby/... backend. We grep the page HTML
                      for the ATS signature and pull the real slug/tenant/token.
  3. Active confirm — for Greenhouse/Lever/Ashby we hit the public API with the
                      discovered (or guessed) slug to *confirm* it returns jobs.
  4. Other ATS      — recognised but not yet supported here (Jobvite, Brassring,
                      Cornerstone, Workable, ...). Reported by name so you know
                      whether an adapter is worth building.

On a hit it prints a ready-to-paste `config.json` board entry. With `--add` it
inserts that entry into config.json's `boards` array in its ATS-type group (each
type stays one contiguous block, in canonical adapter order), preserving the style.

This is the "hidden ATS trick" from HANDOFF.md §7, turned into a tool. The 14
types it can map to a working adapter are in SUPPORTED_TYPES below.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

# Types we have a working adapter for (must stay in sync with jobwatch.ADAPTERS).
SUPPORTED_TYPES = {
    "workday", "greenhouse", "lever", "ashby", "phenom", "successfactors",
    "oracle", "radancy", "smartrecruiters", "bamboohr", "rippling", "ukg",
    "dayforce", "icims", "eightfold",
}

# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #

# (type, host-regex). Matched against the final (post-redirect) URL's host and
# the original host. A host match is high confidence — it *is* the ATS.
HOST_RULES = [
    ("workday", r"\.myworkdayjobs\.com$|\.myworkdaysite\.com$"),
    ("greenhouse", r"(^|\.)(job-boards|boards)\.greenhouse\.io$"),
    ("lever", r"(^|\.)jobs\.lever\.co$"),
    ("ashby", r"(^|\.)jobs\.ashbyhq\.com$"),
    ("icims", r"\.icims\.com$"),
    ("smartrecruiters", r"(^|\.)careers\.smartrecruiters\.com$|(^|\.)jobs\.smartrecruiters\.com$"),
    ("bamboohr", r"\.bamboohr\.com$"),
    ("rippling", r"(^|\.)ats\.rippling\.com$"),
    ("ukg", r"\.ultipro\.(com|ca)$|recruiting\.ultipro"),
    ("dayforce", r"(^|\.)jobs\.dayforcehcm\.com$"),
    ("oracle", r"\.oraclecloud\.com$"),
    ("radancy", r"\.talentbrew\.com$|\.tbcdn\.talentbrew"),
]

# (type, html-regex, slug-capture-regex-or-None). Looked for in the page HTML to
# catch a white-labeled backend. The 2nd regex, if set, pulls the real slug from
# the first matching URL in the page. Medium confidence (HTML can lie — e.g.
# "Greenhouse Gas" ESG copy, a "Workday" job *title*) so GH/Lever/Ashby get
# actively confirmed before we trust them.
#
# ORDER MATTERS: most-specific signatures first. Front-end platforms (Radancy,
# Phenom) often embed links to a secondary ATS (a stray myworkdayjobs.com URL),
# so their own specific CDN/script signatures must be checked BEFORE Workday's
# generic host substring — otherwise a TalentBrew site gets mislabeled "workday".
HTML_SIGNATURES = [
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io|grnh\.se|greenhouse\.io/embed",
     r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co|api\.lever\.co",
     r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com|ashbyhq\.com/posting-api",
     r"jobs\.ashbyhq\.com/([a-z0-9_-]+)"),
    ("radancy", r"talentbrew|tbcdn|/search-jobs\?orgIds", None),
    ("phenom", r"var\s+phApp|phApp\.ddo|widgetApiEndpoint", None),
    ("eightfold", r"eightfold\.ai|pcsxConfig|/api/pcsx/", None),
    ("icims", r"\.icims\.com|iCIMS_JobsTable|iCIMS_JobCardItem", None),
    ("oracle", r"\.oraclecloud\.com|/hcmUI/CandidateExperience", None),
    ("dayforce", r"dayforcehcm\.com", None),
    ("smartrecruiters", r"smartrecruiters\.com", None),
    ("bamboohr", r"\.bamboohr\.com", None),
    ("rippling", r"ats\.rippling\.com", None),
    ("ukg", r"\.ultipro\.(?:com|ca)|recruiting\.ultipro", None),
    ("workday", r"\.myworkdayjobs\.com|\.myworkdaysite\.com", None),
]

# Recognised but unsupported — reported by name only (no adapter).
OTHER_ATS = [
    ("Jobvite", r"jobvite\.com|jobs\.jobvite"),
    ("IBM/Infinite Brassring (Kenexa)", r"brassring\.com|kenexa"),
    ("Cornerstone OnDemand", r"\.csod\.com|cornerstoneondemand"),
    ("Workable", r"workable\.com|apply\.workable"),
    ("Yello", r"yello\.co"),
    ("Beamery", r"beamery\.com|beamery"),
    ("Avature", r"avature\.net|avature"),
    ("JazzHR", r"applytojob\.com|jazzhr"),
    ("Recruitee", r"\.recruitee\.com"),
    ("Breezy HR", r"breezy\.hr"),
    ("Teamtailor", r"teamtailor\.com"),
    ("Paylocity", r"recruiting\.paylocity\.com"),
    ("ADP Workforce Now", r"workforcenow\.adp\.com|recruiting\.adp\.com"),
    ("Oracle Taleo", r"taleo\.net|tbe\.taleo"),
    ("SAP SuccessFactors (RMK)", r"successfactors\.com|/sfcareer/|rmkcdn"),
    ("Jobylon", r"jobylon\.com"),
    ("Personio", r"\.jobs\.personio\."),
    ("Pinpoint", r"pinpointhq\.com"),
    ("Workday (peakon/other)", r"\.wd\d+\."),
]

# --------------------------------------------------------------------------- #
# Active confirmation (Greenhouse / Lever / Ashby public APIs)
# --------------------------------------------------------------------------- #


def _try_greenhouse(sess, token):
    try:
        r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", timeout=15)
        if r.ok:
            return len(r.json().get("jobs", []))
    except Exception:
        pass
    return None


def _try_lever(sess, slug):
    try:
        r = sess.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=15)
        if r.ok and isinstance(r.json(), list):
            return len(r.json())
    except Exception:
        pass
    return None


def _try_ashby(sess, slug):
    try:
        r = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=15)
        if r.ok:
            return len(r.json().get("jobs", []))
    except Exception:
        pass
    return None


def _greenhouse_name(sess, token):
    """The board's own display name from the Greenhouse boards API. Doubles as a
    corroboration signal: a guessed token 'linkedin' resolving to name 'LinkedIn'
    makes a wrong-company match obvious in the report."""
    try:
        r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{token}", timeout=15)
        if r.ok:
            return r.json().get("name")
    except Exception:
        pass
    return None


CONFIRMERS = {"greenhouse": _try_greenhouse, "lever": _try_lever, "ashby": _try_ashby}
CANONICAL = {
    "greenhouse": lambda s: f"https://job-boards.greenhouse.io/{s}",
    "lever": lambda s: f"https://jobs.lever.co/{s}",
    "ashby": lambda s: f"https://jobs.ashbyhq.com/{s}",
}
OVERRIDE_FIELD = {"greenhouse": "token", "lever": "company", "ashby": "board"}


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #


def _slug_candidates(url, html):
    """Plausible company slugs to guess against GH/Lever/Ashby when there's no
    explicit signature: hostname labels (minus www/careers/jobs) and the slugged
    <title>."""
    cands = []
    host = urlparse(url).netloc.lower()
    # Never guess generic/ATS-domain words — "greenhouse"/"lever"/"ashby" etc. are
    # valid boards (the ATS vendors' own), which would be false positives.
    stop = {"www", "careers", "jobs", "com", "io", "co", "ca", "net", "org",
            "greenhouse", "job-boards", "boards", "lever", "ashby", "ashbyhq",
            "myworkdayjobs", "myworkdaysite", "icims", "smartrecruiters",
            "dayforcehcm", "oraclecloud", "bamboohr", "rippling", "ultipro",
            # Aggregators / job portals: their own host label is a real board for
            # the *vendor*, not the company the page is actually about (the company
            # sits in the path, e.g. linkedin.com/company/<X>). Guessing the host
            # label here finds RippleMatch's / LinkedIn's / Jobvite's own board.
            "ripplematch", "linkedin", "indeed", "glassdoor", "ziprecruiter",
            "jobvite", "eightfold", "phenompeople", "smartrecruiter",
            # Generic words that are valid demo/portal boards → false positives.
            "example", "test", "demo", "staging", "app", "apply", "hr",
            "talent", "company", "recruiting", "hiring", "work", "search",
            "openings", "opening", "positions", "opportunities", "current",
            "students", "campus", "intro", "join", "team", "results", "openroles"}
    labels = [l for l in host.split(".") if l not in stop]
    cands += labels
    m = re.search(r"<title[^>]*>([^<|:]+)", html, re.I)
    if m:
        title_slug = re.sub(r"[^a-z0-9]+", "", m.group(1).lower())[:30]
        if title_slug not in stop:  # same generic-word guard for <title> slugs
            cands.append(title_slug)
    seen, out = set(), []
    for c in cands:
        c = c.strip("-")
        if c and len(c) >= 3 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _host_matches(host, pattern):
    return re.search(pattern, host) is not None


def _path_slug(url):
    """Last non-empty path segment, dropping a leading locale (en-US)."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]
    return segs[-1] if segs else None


def probe(url):
    """Return a dict describing the detection, e.g.
       {type, confidence, evidence, slug, config: {...}}  or  {type: None, ...}."""
    sess = requests.Session()
    sess.headers["User-Agent"] = BROWSER_UA

    final_url, html, status = url, "", None
    try:
        r = sess.get(url, timeout=30, allow_redirects=True)
        final_url, status, html = r.url, r.status_code, r.text or ""
    except Exception as e:
        html = ""
        note = f"(page fetch failed: {e}; detecting from URL only)"
    else:
        note = f"(HTTP {status}, {len(html)} bytes)"

    hosts = {urlparse(url).netloc.lower(), urlparse(final_url).netloc.lower()}

    # 1) Host match — the URL already lives on an ATS domain.
    for atype, pat in HOST_RULES:
        if any(_host_matches(h, pat) for h in hosts):
            # For GH/Lever/Ashby the company slug is the URL's last path segment.
            slug = _path_slug(final_url) if atype in CONFIRMERS else None
            res = {"type": atype, "confidence": "high", "evidence": f"host matches {atype}",
                   "note": note, "slug": slug}
            _confirm_and_build(sess, res, url, html)
            return res

    # 2) Embedded ATS signature in the page HTML (white-labeled backend).
    for atype, sig, slug_re in HTML_SIGNATURES:
        if re.search(sig, html, re.I):
            slug = None
            if slug_re:
                m = re.search(slug_re, html, re.I)
                slug = m.group(1) if m else None
            res = {"type": atype, "confidence": "medium",
                   "evidence": f"page embeds {atype} signature", "note": note, "slug": slug}
            _confirm_and_build(sess, res, url, html)
            # GH/Lever/Ashby with no confirmed jobs is likely a false positive
            # (ESG "greenhouse gas" text, a "Workday" job title) — keep looking.
            if atype in CONFIRMERS and res.get("job_count") is None:
                continue
            return res

    # 3) Recognised but unsupported (Beamery, Avature, Jobvite, ...). Checked
    # BEFORE the active slug-guess on purpose: a real ATS signature on the page is
    # far stronger evidence than a name guess. Doing the guess first lets a stray
    # board mask the true platform — e.g. an Equifax page that is actually Beamery,
    # or jobs.jobvite.com getting mislabeled as a coincidental Lever board.
    for name, sig in OTHER_ATS:
        if re.search(sig, html, re.I):
            return {"type": None, "other": name, "confidence": "medium",
                    "evidence": f"page embeds {name} signature (no adapter yet)",
                    "note": note, "slug": None}

    # 4) Last resort — actively guess GH/Lever/Ashby slugs from host/title. The
    # weakest signal: a confirmed board proves the *slug* exists, not that this
    # company owns it. So we require the board to be NON-EMPTY — an empty guessed
    # board is no corroboration at all (some unrelated org owns a board literally
    # named "jobs"/"coop"/"currentopenings", and it just happens to be empty). Flag
    # low-confidence so the user verifies the listed jobs really are this company's.
    for slug in _slug_candidates(final_url, html):
        for atype, confirm in CONFIRMERS.items():
            n = confirm(sess, slug)
            if n:  # non-empty only — 0 or None gives no evidence of ownership
                res = {"type": atype, "confidence": "low",
                       "evidence": f"GUESSED slug '{slug}' from domain → {atype} API "
                                   f"returned {n} jobs — verify these are this company's",
                       "note": note, "slug": slug, "job_count": n}
                _augment_name(sess, res)
                _build_config(res, url, slug)
                return res

    return {"type": None, "other": None, "confidence": None,
            "evidence": "no known ATS signature found", "note": note, "slug": None}


def _confirm_and_build(sess, res, url, html):
    atype = res["type"]
    slug = res.get("slug")
    if atype in CONFIRMERS:
        if slug:
            # Authoritative slug (ATS path or embedded signature). Confirm it, but
            # trust it even if the board is currently empty/closed — the slug is
            # correct, the board just has no open jobs right now.
            n = CONFIRMERS[atype](sess, slug)
            res["job_count"] = n
            if n is not None:
                res["evidence"] += f" — confirmed via API ({n} jobs)"
            else:
                res["evidence"] += " — slug from URL (API returned no board; may be empty/closed)"
        else:
            # No slug known — guess from host/title candidates.
            for cand in _slug_candidates(url, html):
                n = CONFIRMERS[atype](sess, cand)
                if n is not None:
                    res["slug"] = slug = cand
                    res["job_count"] = n
                    res["evidence"] += f" — slug '{cand}' confirmed via API ({n} jobs)"
                    break
    _augment_name(sess, res)
    _build_config(res, url, res.get("slug"))


def _augment_name(sess, res):
    """Resolve a real display name from the ATS API where one exists, so we don't
    fall back to munging the host into junk like 'App'/'Jobs Ca'. Greenhouse is the
    one with a clean board-name endpoint; for low-confidence guesses the resolved
    name is also appended to the evidence so a wrong-company match is visible."""
    if res.get("name") or res.get("type") != "greenhouse" or not res.get("slug"):
        return
    bn = _greenhouse_name(sess, res["slug"])
    if bn:
        res["api_name"] = bn
        if res.get("confidence") == "low":
            res["evidence"] += f" (board name: '{bn}' — confirm it's the right company)"


def _build_config(res, url, slug):
    """Attach a ready-to-paste config.json board entry to res."""
    atype = res["type"]
    name = res.get("name") or res.get("api_name") or _guess_name(url, atype, slug)
    entry = {"name": name, "type": atype}

    if atype in OVERRIDE_FIELD and slug:
        host = urlparse(url).netloc.lower()
        on_ats = any(_host_matches(host, p) for t, p in HOST_RULES if t == atype)
        if on_ats:
            # Already on the ATS domain — adapter reads the slug from the URL.
            entry["url"] = url
        else:
            # White-labeled: keep the user-facing URL but pin the real slug.
            entry[OVERRIDE_FIELD[atype]] = slug
            entry["url"] = url
    elif atype == "smartrecruiters":
        segs = [s for s in urlparse(url).path.split("/") if s]
        if segs:
            entry["company"] = segs[0]
        entry["url"] = url
    else:
        entry["url"] = url

    res["config"] = entry


def _guess_name(url, atype=None, slug=None):
    """Best-effort display name (the user can override with --name)."""
    host = urlparse(url).netloc.lower()
    on_ats = atype and any(_host_matches(host, p) for t, p in HOST_RULES if t == atype)
    if on_ats:
        if atype == "icims":
            base = re.sub(r"^(?:careers|jobs)[a-z]*-", "", host.split(".")[0])
        elif atype == "dayforce":
            segs = [s for s in urlparse(url).path.split("/") if s]
            if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
                segs = segs[1:]  # drop locale (en-US) → namespace is the name
            base = segs[0] if segs else host.split(".")[0]
        elif atype in CONFIRMERS and slug:  # greenhouse / lever / ashby
            base = slug
        else:  # workday, oracle, bamboohr, rippling, ukg, smartrecruiters, radancy
            base = host.split(".")[0]
    else:
        labels = [l for l in host.split(".")
                  if l not in ("www", "careers", "jobs", "com", "io", "co", "ca", "net", "org")]
        base = labels[0] if labels else host
    return base.replace("-", " ").replace("_", " ").title()


# --------------------------------------------------------------------------- #
# Output / config append
# --------------------------------------------------------------------------- #


def _format_entry(entry):
    """Render a board dict in config.json's hand-formatted 2-line style:
        { "name": ..., "type": ..., <overrides>,
          "url": ... }"""
    head = {k: v for k, v in entry.items() if k != "url"}
    head_str = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in head.items())
    return f'    {{ {head_str},\n      "url": {json.dumps(entry["url"])} }}'


def _append_to_config(entry):
    """Insert the board into config.json in its `type` group, keeping every type in
    one contiguous block. If the type already has boards, the entry joins the end of
    that block. If it's a brand-new type, a new block is opened in canonical adapter
    order (matching jobwatch.ADAPTERS) and set off by a blank line — so new additions
    land in their proper group rather than being dumped onto whatever sits last.
    Returns (group_label, count_of_type)."""
    from jobwatch import ADAPTERS  # canonical group order — single source of truth
    order = list(ADAPTERS)
    rank = lambda t: order.index(t) if t in order else len(order)

    text = CONFIG_PATH.read_text()
    boards = json.loads(text)["boards"]
    new_type = entry["type"]
    block = _format_entry(entry)

    # Map each board to its text span. Board dicts are flat (no nested braces),
    # so `{...}` blocks in the boards-array region line up 1:1 with `boards`.
    arr_start = text.index("[", text.index('"boards"'))
    blocks = list(re.finditer(r"\{[^{}]*\}", text[arr_start:]))
    if len(blocks) != len(boards):
        # Safety fallback: structure not as expected → append at end of array.
        marker = text.rfind("\n  ]")
        prev = text.rfind("}", 0, marker)
        new = text[:prev + 1] + ",\n" + block + text[prev + 1:]
        json.loads(new)
        CONFIG_PATH.write_text(new)
        return "(end, fallback)", sum(b.get("type") == new_type for b in boards) + 1

    same = [i for i, b in enumerate(boards) if b.get("type") == new_type]
    if same:
        # Existing group: tack onto the end of the block, no separator.
        insert_at = arr_start + blocks[same[-1]].end()
        new = text[:insert_at] + ",\n" + block + text[insert_at:]
        label = new_type
    else:
        # New group: open it in canonical order, set off by a blank line. Insert
        # after the last board whose type ranks at or before the new one.
        before = [i for i, b in enumerate(boards) if rank(b.get("type")) <= rank(new_type)]
        if before:
            insert_at = arr_start + blocks[before[-1]].end()
            new = text[:insert_at] + ",\n\n" + block + text[insert_at:]
        else:  # ranks before every existing board → first group in the array
            insert_at = arr_start + 1  # just after the opening "["
            new = text[:insert_at] + "\n" + block + ",\n" + text[insert_at:]
        label = f"(new '{new_type}' group)"

    json.loads(new)  # validate before writing
    CONFIG_PATH.write_text(new)
    return label, len(same) + 1


def _verify(entry):
    """Run the actual jobwatch fetch path for this board and report what it gets.
    Returns (job_count, error) — the same (never-raises) contract jobwatch uses,
    so this is exactly how the board will behave in a real run."""
    from jobwatch import fetch_board  # imports the adapter registry
    _name, jobs, error = fetch_board(entry)
    return len(jobs), error


def _handle_one(url, name, do_add):
    """Probe one URL, print its report, optionally add it. Returns a one-line
    status string for the batch summary."""
    res = probe(url)
    if name and res.get("config"):
        res["config"]["name"] = name
        res["name"] = name

    print(f"\n  URL:      {url}  {res['note']}")
    if not res["type"]:
        if res.get("other"):
            print(f"  ATS:      {res['other']}  [NOT SUPPORTED — no adapter]")
            print(f"  Evidence: {res['evidence']}")
            return f"{res['other']} (no adapter)"
        print("  ATS:      unknown — no known ATS signature found.")
        print("  Try the actual job-board/iframe URL, or it may be a bespoke/JS-injected site.")
        return "unknown"

    supported = res["type"] in SUPPORTED_TYPES
    tag = "supported" if supported else "NO ADAPTER"
    print(f"  ATS:      {res['type']}  [{tag}]   (confidence: {res['confidence']})")
    print(f"  Evidence: {res['evidence']}")
    if res.get("slug"):
        print(f"  Slug:     {res['slug']}")
    print("\n  config.json board entry:\n")
    print(_format_entry(res["config"]))

    if not do_add:
        print("\n  (re-run with --add to append it to config.json — it's added"
              " only if the board actually fetches jobs)")
        return f"{res['type']} (not added)"

    if not supported:
        print(f"\n  Not added: '{res['type']}' has no adapter yet.")
        return f"{res['type']} (no adapter, skipped)"

    # Verify it actually works in jobwatch before touching config.json. The
    # validity test is whether the fetch *succeeds* — a reachable board with 0
    # current postings is still valid (a company can just have nothing open now),
    # so only an error blocks the add. 0 jobs is surfaced as a heads-up.
    print("\n  Verifying (fetching the board the same way jobwatch will)...")
    n, error = _verify(res["config"])
    if error:
        print(f"  Not added: board returned an error — {error}.")
        return f"{res['type']} (NOT added: error — {error})"

    if n == 0:
        note = "fetched 0 jobs — board is reachable but has no postings right now"
    else:
        note = f"fetched {n} job{'s' if n != 1 else ''}"
    group, count = _append_to_config(res["config"])
    print(f"  ✓ Added to {CONFIG_PATH.name} in the '{group}' group "
          f"({count} of this type) — {note}")
    return f"{res['type']} ✓ added — {note}"


def _split_named(line):
    """Parse the explicit `Display Name <sep> https://url` form, where <sep> is a
    comma+space (`Acme, https://...`), a tab, or two-or-more spaces — so a pasted
    `Name<TAB>url` / `Name   url` list pins the name just like the comma form does.
    Returns (name, url), else (None, None) so the caller falls back to plain URL
    extraction. A leading Markdown bullet/quote is stripped, and a "name" that is
    itself a URL is rejected (so a bare line, a single-space prose line, or one
    with several URLs isn't misread as named)."""
    m = re.match(r'\s*(?:[-*>]\s+)?(.+?)(?:\s*,\s+|\t+| {2,})\s*(https?://\S+)\s*$', line)
    if not m:
        return None, None
    name = m.group(1).strip().strip('"\'')
    if not name or re.match(r'https?://', name):
        return None, None
    return name, m.group(2).rstrip(".,;")


def _extract_entries(text):
    """Pull (name, url) pairs out of arbitrary text, one logical entry per line.
    A line may be a plain URL — Markdown (`- [Name](url)`, bullets, tables) or
    prose, where name is None and gets guessed later — or the explicit
    `Display Name, https://url` form, which pins the config entry's name so probe
    never has to guess it. HTML comments (`<!-- ... -->`) and `#` heading/comment
    lines are dropped; duplicate URLs removed, order preserved."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    out, seen = [], set()
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        name, url = _split_named(ln)
        if url:
            pairs = [(name, url)]
        else:  # no Name, URL shape — pull every bare URL out of the line
            pairs = [(None, u.rstrip(".,;"))
                     for u in re.findall(r'https?://[^\s)\]>"\'`]+', ln)]
        for nm, u in pairs:
            if u and u not in seen:
                seen.add(u)
                out.append((nm, u))
    return out


def _collect_entries(args):
    """Gather (name, url) pairs from positional args and/or --file. Positionals
    are treated as lines too, so `probe.py "Acme, https://..."` works the same as
    a file line. Returns a list of (name-or-None, url)."""
    text = "\n".join(args.urls)
    if args.file:
        text += "\n" + (sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
    return _extract_entries(text)


def main():
    ap = argparse.ArgumentParser(description="Detect which ATS one or more careers URLs run on.")
    ap.add_argument("urls", nargs="*",
                    help="Careers URLs, or 'Name, url' / 'Name<TAB>url' to pin the name")
    ap.add_argument("--file", help="Read URLs (or 'Name, url' lines) from a file ('-' for stdin)")
    ap.add_argument("--name", help="Display name (only applied when given a single URL)")
    ap.add_argument("--add", action="store_true",
                    help="Add to config.json — only boards that fetch without error")
    args = ap.parse_args()

    entries = _collect_entries(args)
    if not entries:
        ap.error("give at least one URL (positional or 'Name, url') or --file")
    if args.name and len(entries) > 1:
        ap.error("--name only works with a single URL")

    summary = []
    for name, url in entries:
        try:
            # Inline 'Name, url' wins; --name covers the single-URL no-inline case.
            status = _handle_one(url, name or args.name, args.add)
        except Exception as e:  # never let one bad URL sink the batch
            print(f"\n  URL:      {url}\n  ERROR: {e}")
            status = f"ERROR: {e}"
        summary.append((url, status))

    if len(entries) > 1:
        print("\n" + "=" * 70 + "\n  Summary:\n")
        for url, status in summary:
            print(f"  {status:42}  {url}")
    print()


if __name__ == "__main__":
    main()
