"""Tiny stand-ins for requests.Session/Response so probe's detection logic can be
exercised offline. Routes a GET to a canned response by substring-matching the URL.

Used by the characterization tests: probe() and the GH/Lever/Ashby confirmers all
go through `requests.Session().get(...)`, so a fake Session that returns scripted
responses lets us pin behavior without hitting the network.
"""

import json as _json


class FakeResponse:
    def __init__(self, url="", status=200, text="", payload=None):
        self.url = url
        self.status_code = status
        self.text = text
        self._payload = payload

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
