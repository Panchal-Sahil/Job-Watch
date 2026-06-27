"""Active confirmation against the Greenhouse / Lever / Ashby public APIs.

A page can *look* like Greenhouse (ESG "greenhouse gas" copy) or carry a stray
ATS link, so for these three platforms we hit the public board API with the
discovered-or-guessed slug to confirm it actually returns a board before trusting
the match. Each confirmer returns a job count (None on any failure).
"""


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


# type -> confirmer. Membership in this map is what marks a type as "needs active
# confirmation" throughout detection.
CONFIRMERS = {"greenhouse": _try_greenhouse, "lever": _try_lever, "ashby": _try_ashby}
