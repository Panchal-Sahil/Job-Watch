"""The detection pipeline: probe(url) and its slug/name/config helpers.

probe() tries four strategies, strongest first — host match, embedded HTML
signature, recognized-but-unsupported, then a last-resort slug guess — and returns
a dict describing the detection plus a ready-to-paste config.json board entry.
"""

import re
from html import unescape
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA
from probe.confirm import CONFIRMERS, _greenhouse_name
from probe.result import DetectionResult
from probe.signatures import HOST_RULES, HTML_SIGNATURES, OTHER_ATS, OVERRIDE_FIELD


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


def _ripplematch_name(page_html, url):
    """RippleMatch's exact company display name — the value its jobs API filters
    on. Prefer the page title ('... Careers - <Name> | RippleMatch' in <title> or
    og:title); fall back to the URL slug titleized (app.ripplematch.com/v2/public/
    company/<slug>). The name lands in the board's `company` field, so getting it
    right is what makes the board actually fetch."""
    for pat in (r'og:title"\s+content="[^"]*?[-–]\s*(.+?)\s*\|\s*RippleMatch',
                r'<title[^>]*>[^<]*?[-–]\s*(.+?)\s*\|\s*RippleMatch'):
        m = re.search(pat, page_html, re.I)
        if m:
            return unescape(m.group(1)).strip()
    slug = _path_slug(url)
    return slug.replace("-", " ").title() if slug else None


def probe(url):
    """Return a DetectionResult describing the detection. On a match it carries the
    type/confidence/evidence/slug and a ready-to-paste `config` board entry; on no
    match `type` is None (with `other` set for a recognized-but-unsupported ATS)."""
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
            res = DetectionResult(type=atype, confidence="high",
                                  evidence=f"host matches {atype}", note=note, slug=slug)
            _confirm_and_build(sess, res, url, html)
            return res

    # 2) Embedded ATS signature in the page HTML (white-labeled backend).
    for atype, sig, slug_re in HTML_SIGNATURES:
        if re.search(sig, html, re.I):
            slug = None
            if slug_re:
                m = re.search(slug_re, html, re.I)
                slug = m.group(1) if m else None
            res = DetectionResult(type=atype, confidence="medium",
                                  evidence=f"page embeds {atype} signature",
                                  note=note, slug=slug)
            _confirm_and_build(sess, res, url, html)
            # GH/Lever/Ashby with no confirmed jobs is likely a false positive
            # (ESG "greenhouse gas" text, a "Workday" job title) — keep looking.
            if atype in CONFIRMERS and res.job_count is None:
                continue
            return res

    # 3) Recognised but unsupported (Beamery, Avature, Jobvite, ...). Checked
    # BEFORE the active slug-guess on purpose: a real ATS signature on the page is
    # far stronger evidence than a name guess. Doing the guess first lets a stray
    # board mask the true platform — e.g. an Equifax page that is actually Beamery,
    # or jobs.jobvite.com getting mislabeled as a coincidental Lever board.
    for name, sig in OTHER_ATS:
        if re.search(sig, html, re.I):
            return DetectionResult(type=None, other=name, confidence="medium",
                                   evidence=f"page embeds {name} signature (no adapter yet)",
                                   note=note, slug=None)

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
                res = DetectionResult(
                    type=atype, confidence="low",
                    evidence=f"GUESSED slug '{slug}' from domain → {atype} API "
                             f"returned {n} jobs — verify these are this company's",
                    note=note, slug=slug, job_count=n)
                _augment_name(sess, res)
                _build_config(res, url, slug, html)
                return res

    return DetectionResult(type=None, other=None, confidence=None,
                           evidence="no known ATS signature found", note=note, slug=None)


def _confirm_and_build(sess, res, url, html):
    atype = res.type
    slug = res.slug
    if atype in CONFIRMERS:
        if slug:
            # Authoritative slug (ATS path or embedded signature). Confirm it, but
            # trust it even if the board is currently empty/closed — the slug is
            # correct, the board just has no open jobs right now.
            n = CONFIRMERS[atype](sess, slug)
            res.job_count = n
            if n is not None:
                res.evidence += f" — confirmed via API ({n} jobs)"
            else:
                res.evidence += " — slug from URL (API returned no board; may be empty/closed)"
        else:
            # No slug known — guess from host/title candidates.
            for cand in _slug_candidates(url, html):
                n = CONFIRMERS[atype](sess, cand)
                if n is not None:
                    res.slug = slug = cand
                    res.job_count = n
                    res.evidence += f" — slug '{cand}' confirmed via API ({n} jobs)"
                    break
    if atype == "ripplematch" and not res.api_name:
        res.api_name = _ripplematch_name(html, url)
    _augment_name(sess, res)
    _build_config(res, url, res.slug, html)


def _augment_name(sess, res):
    """Resolve a real display name from the ATS API where one exists, so we don't
    fall back to munging the host into junk like 'App'/'Jobs Ca'. Greenhouse is the
    one with a clean board-name endpoint; for low-confidence guesses the resolved
    name is also appended to the evidence so a wrong-company match is visible."""
    if res.name or res.type != "greenhouse" or not res.slug:
        return
    bn = _greenhouse_name(sess, res.slug)
    if bn:
        res.api_name = bn
        if res.confidence == "low":
            res.evidence += f" (board name: '{bn}' — confirm it's the right company)"


def _build_config(res, url, slug, html=""):
    """Attach a ready-to-paste config.json board entry to res."""
    atype = res.type
    name = res.name or res.api_name or _guess_name(url, atype, slug)
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
        # On careers./jobs.smartrecruiters.com the first path segment *is* the
        # company id. On a vanity domain it is not ("company", "en-ca", "jobs") —
        # pin the real id embedded in the page instead of a meaningless segment.
        host = urlparse(url).netloc.lower()
        on_ats = any(_host_matches(host, p) for t, p in HOST_RULES if t == atype)
        segs = [s for s in urlparse(url).path.split("/") if s]
        if on_ats:
            if segs:
                entry["company"] = segs[0]
        else:
            from adapters.smartrecruiters import extract_smartrecruiters_company
            real = extract_smartrecruiters_company(html)
            if real:
                entry["company"] = real
        entry["url"] = url
    elif atype == "oracle":
        # Vanity CE domains (careers.oracle.com, jobs.akamai.com) proxy the UI but
        # not the /hcmRestApi endpoint — pin the real *.oraclecloud.com API host
        # embedded in the page so the board fetches without a per-run page lookup.
        host = urlparse(url).netloc.lower()
        if not host.endswith(".oraclecloud.com"):
            from adapters.oracle import extract_oracle_host
            real = extract_oracle_host(html)
            if real and real != host:
                entry["host"] = real
        entry["url"] = url
    elif atype == "ripplematch":
        # The adapter filters the API by exact company display name — pin it (from
        # the page title, resolved into api_name) rather than the opaque URL slug.
        entry["company"] = res.api_name or name
        entry["url"] = url
    else:
        entry["url"] = url

    res.config = entry


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
        elif atype == "jobvite":
            segs = [s for s in urlparse(url).path.split("/") if s]
            base = segs[0] if segs else host.split(".")[0]
        elif atype == "gem":
            segs = [s for s in urlparse(url).path.split("/") if s]
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
