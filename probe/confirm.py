"""Active confirmation against Greenhouse / Lever / Ashby public APIs.
Each confirmer returns a job count (None on failure).
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
    """Board display name from GH API. Also a corroboration signal for guessed slugs."""
    try:
        r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{token}", timeout=15)
        if r.ok:
            return r.json().get("name")
    except Exception:
        pass
    return None


# Membership marks a type as "needs active confirmation" throughout detection.
CONFIRMERS = {"greenhouse": _try_greenhouse, "lever": _try_lever, "ashby": _try_ashby}
