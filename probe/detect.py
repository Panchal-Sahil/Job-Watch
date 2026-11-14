"""The detection pipeline: probe(url) and its slug/name/config helpers."""

import re
import sys
from urllib.parse import urlparse

import requests

from adapters import ADAPTERS
from adapters.common import BROWSER_UA, name_from_slug
from probe.confirm import CONFIRMERS, _greenhouse_name
from probe.result import DetectionResult
from probe.signatures import HOST_RULES, HTML_SIGNATURES, OTHER_ATS


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
    host = urlparse(url).netloc.lower()
    on_ats = any(_host_matches(host, p) for t, p in HOST_RULES if t == atype)
    fields = _adapter_fields(atype, url, html, slug, on_ats)
    guess = fields.pop("name", None)  # a guess: an API-provided name beats it
    name = res.name or res.api_name or guess or _default_name(host, on_ats)
    res.config = {"name": name, "type": atype, **fields, "url": url}


def _adapter_fields(atype, url, html, slug, on_ats):
    """Fields the adapter's optional probe_entry hook wants pinned (see adapters/)."""
    fetch = ADAPTERS.get(atype)
    module = sys.modules.get(getattr(fetch, "__module__", None))
    hook = getattr(module, "probe_entry", None)
    return dict(hook(url, html, slug, on_ats)) if hook else {}


def _default_name(host, on_ats):
    """Best-effort display name from the host (user can override with --name)."""
    if on_ats:
        base = host.split(".")[0]
    else:
        labels = [l for l in host.split(".")
                  if l not in ("www", "careers", "jobs", "com", "io", "co", "ca", "net", "org")]
        base = labels[0] if labels else host
    return name_from_slug(base)
