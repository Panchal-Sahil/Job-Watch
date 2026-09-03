"""The detection pipeline: probe(url) and its slug/name/config helpers."""

import re
from html import unescape
from urllib.parse import urlparse

import requests

from adapters.common import BROWSER_UA
from probe.confirm import CONFIRMERS, _greenhouse_name
from probe.result import DetectionResult
from probe.signatures import HOST_RULES, HTML_SIGNATURES, OTHER_ATS, OVERRIDE_FIELD


def _slug_candidates(url, html):
    """Plausible company slugs from hostname labels and <title>."""
    cands = []
    host = urlparse(url).netloc.lower()
    # ATS-domain words and aggregator hosts would be false positives.
    stop = {"www", "careers", "jobs", "com", "io", "co", "ca", "net", "org",
            "greenhouse", "job-boards", "boards", "lever", "ashby", "ashbyhq",
            "myworkdayjobs", "myworkdaysite", "icims", "smartrecruiters",
            "dayforcehcm", "oraclecloud", "bamboohr", "rippling", "ultipro",
            "ripplematch", "linkedin", "indeed", "glassdoor", "ziprecruiter",
            "jobvite", "eightfold", "phenompeople", "smartrecruiter",
            "example", "test", "demo", "staging", "app", "apply", "hr",
            "talent", "company", "recruiting", "hiring", "work", "search",
            "openings", "opening", "positions", "opportunities", "current",
            "students", "campus", "intro", "join", "team", "results", "openroles"}
    labels = [l for l in host.split(".") if l not in stop]
    cands += labels
    m = re.search(r"<title[^>]*>([^<|:]+)", html, re.I)
    if m:
        title_slug = re.sub(r"[^a-z0-9]+", "", m.group(1).lower())[:30]
        if title_slug not in stop:
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
    """RippleMatch's exact company display name from page title or og:title."""
    for pat in (r'og:title"\s+content="[^"]*?[-–]\s*(.+?)\s*\|\s*RippleMatch',
                r'<title[^>]*>[^<]*?[-–]\s*(.+?)\s*\|\s*RippleMatch'):
        m = re.search(pat, page_html, re.I)
        if m:
            return unescape(m.group(1)).strip()
    slug = _path_slug(url)
    return slug.replace("-", " ").title() if slug else None


def probe(url):
    """Detect the ATS behind a careers URL. Returns a DetectionResult."""
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

    # 1) Host match
    for atype, pat in HOST_RULES:
        if any(_host_matches(h, pat) for h in hosts):
            slug = _path_slug(final_url) if atype in CONFIRMERS else None
            res = DetectionResult(type=atype, confidence="high",
                                  evidence=f"host matches {atype}", note=note, slug=slug)
            _confirm_and_build(sess, res, url, html)
            return res

    # 2) Embedded ATS signature in page HTML
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
            # GH/Lever/Ashby with no confirmed jobs is likely a false positive.
            if atype in CONFIRMERS and res.job_count is None:
                continue
            return res

    # 3) Recognised but unsupported — checked BEFORE slug-guess because a real
    # signature is stronger than a name guess (prevents mislabeling, e.g. an
    # Equifax/Beamery page coincidentally matching a Lever slug).
    for name, sig in OTHER_ATS:
        if re.search(sig, html, re.I):
            return DetectionResult(type=None, other=name, confidence="medium",
                                   evidence=f"page embeds {name} signature (no adapter yet)",
                                   note=note, slug=None)

    # 4) Last-resort slug guess — require non-empty board (empty gives no
    # evidence of ownership; could be an unrelated org's board named "jobs").
    for slug in _slug_candidates(final_url, html):
        for atype, confirm in CONFIRMERS.items():
            n = confirm(sess, slug)
            if n:
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
            # Authoritative slug — trust even if empty/closed.
            n = CONFIRMERS[atype](sess, slug)
            res.job_count = n
            if n is not None:
                res.evidence += f" — confirmed via API ({n} jobs)"
            else:
                res.evidence += " — slug from URL (API returned no board; may be empty/closed)"
        else:
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
    """Resolve display name from GH API so we don't fall back to host-munging."""
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
            entry["url"] = url
        else:
            entry[OVERRIDE_FIELD[atype]] = slug
            entry["url"] = url
    elif atype == "smartrecruiters":
        # Vanity domains carry a meaningless path segment — pin the real company id.
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
        # Vanity domains proxy the UI but not /hcmRestApi — pin the real host.
        host = urlparse(url).netloc.lower()
        if not host.endswith(".oraclecloud.com"):
            from adapters.oracle import extract_oracle_host
            real = extract_oracle_host(html)
            if real and real != host:
                entry["host"] = real
        entry["url"] = url
    elif atype == "ripplematch":
        entry["company"] = res.api_name or name
        entry["url"] = url
    else:
        entry["url"] = url

    res.config = entry


def _guess_name(url, atype=None, slug=None):
    """Best-effort display name (user can override with --name)."""
    host = urlparse(url).netloc.lower()
    on_ats = atype and any(_host_matches(host, p) for t, p in HOST_RULES if t == atype)
    if on_ats:
        if atype == "icims":
            base = re.sub(r"^(?:careers|jobs)[a-z]*-", "", host.split(".")[0])
        elif atype == "dayforce":
            segs = [s for s in urlparse(url).path.split("/") if s]
            if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
                segs = segs[1:]
            base = segs[0] if segs else host.split(".")[0]
        elif atype == "jobvite":
            segs = [s for s in urlparse(url).path.split("/") if s]
            base = segs[0] if segs else host.split(".")[0]
        elif atype == "gem":
            segs = [s for s in urlparse(url).path.split("/") if s]
            base = segs[0] if segs else host.split(".")[0]
        elif atype in CONFIRMERS and slug:
            base = slug
        else:
            base = host.split(".")[0]
    else:
        labels = [l for l in host.split(".")
                  if l not in ("www", "careers", "jobs", "com", "io", "co", "ca", "net", "org")]
        base = labels[0] if labels else host
    return base.replace("-", " ").replace("_", " ").title()
