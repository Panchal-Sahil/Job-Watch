"""Avature ATS adapter (HTML-scraped).

Avature powers vanity career portals (e.g. jobs.siemens.com, emplois.bnc.ca,
jobs.ea.com). There is no public JSON API; jobs come from the HTML listing pages.

Two page templates appear across Avature portals:
  - "article": <article class="article article--result"> with list-item-* spans
  - "table": <tr> rows with <a data-map="job-detail-link">

Pagination follows the "Next" link (paginationNextLink) in the HTML using
either folderOffset/folderRecordsPerPage or jobOffset/jobRecordsPerPage params.

The board 'url' is the full search URL with any pre-configured filter params
(e.g. early-career category filters) already baked in.
"""

import re
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

from adapters.common import BROWSER_UA

_ART_RE = re.compile(
    r'<article[^>]*article--result[^>]*>(.*?)</article>', re.DOTALL
)
_TITLE_RE = re.compile(
    r'article__header__text__title[^>]*>.*?href="([^"]+)"[^>]*>\s*([^<]+)',
    re.DOTALL,
)
_CITY_RE = re.compile(r'list-item-jobCity[^>]*>([^<]+)')
_STATE_RE = re.compile(r'list-item-jobState[^>]*>([^<]+)')
_COUNTRY_RE = re.compile(r'list-item-jobCountry[^>]*>([^<]+)')
_LOC_PLAIN_RE = re.compile(r'list-item-location[^>]*>([^<]+)')
_ID_RE = re.compile(r'list-item-(?:job)?[Ii]d[^>]*>[^<]*?(\d{3,})')

_ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_ROW_LINK_RE = re.compile(
    r'<a[^>]+data-map="job-detail-link"[^>]+href="([^"]+)"[^>]*>\s*([^<]+)'
)
_ROW_LOC_RE = re.compile(r'<td[^>]*>\s*([^<\n]+?)\s*</td>')

_NEXT_RE = re.compile(r'paginationNextLink.*?href="([^"]+)"', re.DOTALL)


def _parse_articles(html, host, company):
    jobs = []
    for m in _ART_RE.finditer(html):
        body = m.group(1)
        title_m = _TITLE_RE.search(body)
        if not title_m:
            continue
        url = unescape(title_m.group(1))
        title = unescape(title_m.group(2).strip())

        city_m = _CITY_RE.search(body)
        if city_m:
            parts = [city_m.group(1).strip()]
            state_m = _STATE_RE.search(body)
            country_m = _COUNTRY_RE.search(body)
            if state_m:
                parts.append(state_m.group(1).strip())
            if country_m:
                parts.append(country_m.group(1).strip())
            location = ', '.join(p for p in parts if p)
        else:
            loc_m = _LOC_PLAIN_RE.search(body)
            location = unescape(loc_m.group(1).strip()) if loc_m else ""

        id_m = _ID_RE.search(body)
        if id_m:
            job_id = id_m.group(1)
        else:
            job_id = url.rstrip('/').split('?')[0].split('/')[-1]

        jobs.append({
            "id": f"avature:{host}:{job_id}",
            "title": title,
            "location": location,
            "posted": "",
            "url": url,
            "company": company,
        })
    return jobs


def _parse_rows(html, host, company):
    jobs = []
    for m in _ROW_RE.finditer(html):
        body = m.group(1)
        link_m = _ROW_LINK_RE.search(body)
        if not link_m:
            continue
        url = unescape(link_m.group(1))
        title = unescape(link_m.group(2).strip())
        loc_m = _ROW_LOC_RE.search(body)
        location = unescape(loc_m.group(1).strip()) if loc_m else ""
        job_id = url.rstrip('/').split('?')[0].split('/')[-1]
        jobs.append({
            "id": f"avature:{host}:{job_id}",
            "title": title,
            "location": location,
            "posted": "",
            "url": url,
            "company": company,
        })
    return jobs


def fetch_avature(board):
    """Avature vanity portal (HTML-scraped, article and table templates)."""
    url = board["url"]
    host = urlparse(url).netloc
    company = board.get("name", host)
    seen_ids = set()
    jobs = []
    page_url = url

    for _ in range(200):
        r = requests.get(page_url, headers={"User-Agent": BROWSER_UA}, timeout=30)
        r.raise_for_status()
        html = r.text

        if "article--result" in html:
            page_jobs = _parse_articles(html, host, company)
        else:
            page_jobs = _parse_rows(html, host, company)

        for job in page_jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                jobs.append(job)

        next_m = _NEXT_RE.search(html)
        if not next_m:
            break
        page_url = urljoin(url, unescape(next_m.group(1)))

    return jobs
