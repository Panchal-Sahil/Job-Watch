"""Gem ATS adapter."""

from adapters.common import HEADERS, HTTP, TIMEOUT, _slug_from_url

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

    # response is an array (batch) — get the first result's data
    data = resp.json()[0]["data"]

    # company display name from the board metadata, fall back to config/slug
    board_info = data.get("jobBoardExternal") or {}
    company = board_info.get("teamDisplayName") or company

    # loop over each job posting and normalize to the 6-key contract
    jobs = []
    for p in data["oatsExternalJobPostings"]["jobPostings"]:

        # locations is an array of objects — join their "name" fields
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