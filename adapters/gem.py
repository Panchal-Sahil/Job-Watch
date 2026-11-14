"""Gem ATS adapter."""

from urllib.parse import urlparse

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url, name_from_slug

QUERY = """query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { id extId title locations { name } }
  }
  jobBoardExternal(vanityUrlPath: $boardId) { teamDisplayName }
}"""


def fetch_gem(board):
    slug = _slug_from_url(board["url"], "company", board)
    company = board.get("name", slug)
    api = "https://jobs.gem.com/api/public/graphql/batch"
    resp = HTTP.post(api, json=[{
        "operationName": "JobBoardList",
        "query": QUERY,
        "variables": {"boardId": slug},
    }], headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()

    data = resp.json()[0]["data"]

    board_info = data.get("jobBoardExternal") or {}
    company = board_info.get("teamDisplayName") or company

    jobs = []
    for p in data["oatsExternalJobPostings"]["jobPostings"]:
        locs = p.get("locations") or []
        location = " ; ".join(loc["name"] for loc in locs if loc.get("name"))

        ext_id = p.get("extId") or p["id"]

        jobs.append({
            "id": f"gem:{slug}:{ext_id}",
            "title": (p.get("title") or "").strip(),
            "location": location,
            "posted": "",
            "url": f"https://jobs.gem.com/{slug}/{ext_id}",
            "company": company,
        })
    return jobs


def probe_entry(url, page_html, slug, on_ats):
    """On jobs.gem.com the first path segment is the company."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    return {"name": name_from_slug(segs[0])} if on_ats and segs else {}
