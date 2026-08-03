"""Tiny stand-ins for requests.Session/Response so probe's detection logic can be
exercised offline. Routes a GET to a canned response by substring-matching the URL.

Used by the characterization tests: probe() and the GH/Lever/Ashby confirmers all
go through `requests.Session().get(...)`, so a fake Session that returns scripted
responses lets us pin behavior without hitting the network.
"""

import json as _json


class FakeResponse:
    def __init__(self, url="", status=200, text="", payload=None, headers=None):
        self.url = url
        self.status_code = status
        self.text = text
        self._payload = payload
        self.headers = headers or {}  # e.g. Retry-After on a 429

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if self._payload is None:
            return _json.loads(self.text)
        return self._payload


class FakeSession:
    """Maps a list of (url_substring, FakeResponse-or-callable) rules. The first
    rule whose substring is in the requested URL wins. A callable receives the URL
    and returns a FakeResponse, so a rule can echo the requested URL back as r.url
    (probe reads r.url as the final, post-redirect URL)."""

    def __init__(self, rules, default=None):
        self.rules = rules
        self.default = default
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append(url)
        for needle, resp in self.rules:
            if needle in url:
                return resp(url) if callable(resp) else resp
        if self.default is not None:
            return self.default(url) if callable(self.default) else self.default
        raise AssertionError(f"FakeSession: no rule for {url}")


def page(text, status=200):
    """A response for the initial page fetch — echoes the requested URL as r.url."""
    return lambda url: FakeResponse(url=url, status=status, text=text)


def api(payload, status=200):
    """A JSON API response (for the confirmer endpoints)."""
    return lambda url: FakeResponse(url=url, status=status, payload=payload)


# --- Adapter-level HTTP fake -------------------------------------------------
# The adapters (unlike probe) call `requests.get`/`requests.post` and
# `requests.Session()` directly on the module, and several page through a JSON
# API or run a multi-request handshake. `FakeRequests` stands in for the whole
# `requests` module (patched as `adapters.<name>.requests`) and routes every
# call — module-level or via a Session it hands out — through one shared table.


class RequestException(Exception):
    """Stand-in for requests.RequestException (successfactors catches it)."""


class FakeRequests:
    """A stand-in for the `requests` module for adapter tests. Routes get/post by
    (method, url-substring) to a canned response or a callable. Rules are a list of
    `(method, needle, target)` where method is "GET"/"POST"/"*", and target is a
    FakeResponse (returned as-is) or a callable `(url, **kwargs) -> FakeResponse` —
    the kwargs carry `json=`/`params=`/`data=`, so a rule can return a different
    page per request body. The same table backs module-level get/post and every
    Session() it hands out, so a landing-GET -> csrf-GET -> search-POST handshake is
    scripted in one place. First matching rule wins."""

    RequestException = RequestException

    def __init__(self, rules, default=None):
        self.rules = rules
        self.default = default
        self.calls = []  # list of (method, url, kwargs)

    def _route(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for m, needle, target in self.rules:
            if m in ("*", method) and needle in url:
                return target(url, **kw) if callable(target) else target
        if self.default is not None:
            return self.default(url, **kw) if callable(self.default) else self.default
        raise AssertionError(f"FakeRequests: no {method} rule for {url}")

    def get(self, url, **kw):
        return self._route("GET", url, **kw)

    def post(self, url, **kw):
        return self._route("POST", url, **kw)

    def Session(self):
        return _FakeReqSession(self)


class _FakeReqSession:
    """Session handed out by FakeRequests.Session(). Shares the parent's route table
    and exposes a mutable `.headers` dict (adapters do `sess.headers[k] = v`)."""

    def __init__(self, parent):
        self._parent = parent
        self.headers = {}

    def get(self, url, **kw):
        return self._parent._route("GET", url, **kw)

    def post(self, url, **kw):
        return self._parent._route("POST", url, **kw)


def jresp(payload=None, text="", status=200, headers=None):
    """A static JSON/text response usable directly as a route target (no lambda —
    FakeRequests returns non-callable targets as-is, so kwargs don't matter)."""
    return FakeResponse(status=status, text=text, payload=payload, headers=headers)
