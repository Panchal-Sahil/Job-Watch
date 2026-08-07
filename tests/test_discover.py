"""Tests for discover.py — slug generation, name matching, and discovery logic."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discover import _slugify, _names_match, _discover_one, _load_existing
from tests.fakehttp import FakeResponse


# --------------------------------------------------------------------------- #
# Slug generation
# --------------------------------------------------------------------------- #

class TestSlugify(unittest.TestCase):

    def test_single_word(self):
        slugs = _slugify("Stripe")
        self.assertEqual(slugs[0], "stripe")

    def test_multi_word(self):
        slugs = _slugify("Palo Alto Networks")
        self.assertIn("paloaltonetworks", slugs)
        self.assertIn("palo-alto-networks", slugs)

    def test_strips_corp_suffix(self):
        slugs = _slugify("Acme Corporation")
        self.assertIn("acme", slugs)

    def test_strips_inc(self):
        slugs = _slugify("Figma Inc")
        self.assertIn("figma", slugs)

    def test_strips_technologies(self):
        slugs = _slugify("CrowdStrike Technologies")
        self.assertIn("crowdstrike", slugs)

    def test_the_prefix(self):
        slugs = _slugify("The Trade Desk")
        self.assertIn("thetradedesk", slugs)
        self.assertIn("tradedesk", slugs)
        self.assertIn("trade-desk", slugs)

    def test_ampersand(self):
        slugs = _slugify("Ben & Jerry")
        self.assertIn("benandjerry", slugs)
        self.assertIn("ben-and-jerry", slugs)

    def test_punctuation_stripped(self):
        slugs = _slugify("J.P. Morgan")
        self.assertIn("jpmorgan", slugs)
        self.assertIn("jp-morgan", slugs)

    def test_parenthetical_dropped(self):
        slugs = _slugify("Meta (Facebook)")
        self.assertIn("meta", slugs)
        self.assertNotIn("facebook", slugs[0])

    def test_empty(self):
        self.assertEqual(_slugify(""), [])
        self.assertEqual(_slugify("   "), [])

    def test_short_first_word_not_standalone(self):
        """First-word slug requires len >= 4 to avoid 'the', 'dji' etc."""
        slugs = _slugify("DJI Technology")
        # "dji" should appear via suffix-stripping, NOT as the first-word path
        self.assertIn("dji", slugs)
        # but the first-word code path skips it (len('dji') < 4)
        # just verify it's present from stripping
        self.assertIn("djitechnology", slugs)

    def test_three_word_generates_first_two(self):
        slugs = _slugify("JP Morgan Chase")
        self.assertIn("jpmorgan", slugs)
        self.assertIn("jp-morgan", slugs)

    def test_no_duplicates(self):
        slugs = _slugify("Stripe")
        self.assertEqual(len(slugs), len(set(slugs)))


# --------------------------------------------------------------------------- #
# Name matching
# --------------------------------------------------------------------------- #

class TestNamesMatch(unittest.TestCase):

    def test_exact(self):
        self.assertTrue(_names_match("Stripe", "Stripe"))

    def test_case_insensitive(self):
        self.assertTrue(_names_match("STRIPE", "stripe"))

    def test_substring(self):
        self.assertTrue(_names_match("Meta", "Meta Platforms"))

    def test_reverse_substring(self):
        self.assertTrue(_names_match("CrowdStrike Holdings", "CrowdStrike"))

    def test_rejects_unrelated(self):
        self.assertFalse(_names_match("PAN Foundation", "Palo Alto Networks"))

    def test_rejects_short_coincidence(self):
        self.assertFalse(_names_match("ABC News", "XYZ Corp"))

    def test_slug_vs_full_name(self):
        self.assertTrue(_names_match("paloaltonetworks", "Palo Alto Networks"))

    def test_with_suffix(self):
        self.assertTrue(_names_match("Figma", "Figma Inc"))

    def test_empty_rejects(self):
        self.assertFalse(_names_match("", "Stripe"))
        self.assertFalse(_names_match("Stripe", ""))

    def test_prefix_match(self):
        self.assertTrue(_names_match("DataDog", "Datadog Inc"))


# --------------------------------------------------------------------------- #
# Discovery with mocked HTTP
# --------------------------------------------------------------------------- #

def _fake_session(rules):
    """Build a mock requests.Session from (url_substring, FakeResponse) rules."""
    sess = mock.MagicMock()
    sess.headers = {}

    def fake_get(url, **kw):
        for needle, resp in rules:
            if needle in url:
                return resp(url) if callable(resp) else resp
        return FakeResponse(status=404, text="")

    sess.get = fake_get
    return sess


class TestDiscoverOne(unittest.TestCase):

    def test_greenhouse_hit(self):
        rules = [
            ("boards-api.greenhouse.io/v1/boards/acme/jobs",
             FakeResponse(status=200, payload={"jobs": [{"id": 1}]})),
            ("boards-api.greenhouse.io/v1/boards/acme",
             FakeResponse(status=200, payload={"name": "Acme Corp"})),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            hits = _discover_one("Acme Corp", set())

        self.assertEqual(len(hits), 1)
        ats, bname, n, url = hits[0]
        self.assertEqual(ats, "greenhouse")
        self.assertEqual(bname, "Acme Corp")
        self.assertEqual(n, 1)
        self.assertIn("boards.greenhouse.io/acme", url)

    def test_lever_hit(self):
        rules = [
            ("api.lever.co/v0/postings/acme",
             FakeResponse(status=200, payload=[{"id": "x"}])),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            hits = _discover_one("Acme", set())

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "lever")

    def test_ashby_hit(self):
        rules = [
            ("api.ashbyhq.com/posting-api/job-board/acme",
             FakeResponse(status=200, payload={"jobs": [{"id": "a"}]})),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            hits = _discover_one("Acme", set())

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "ashby")

    def test_no_hit(self):
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session([])
            hits = _discover_one("NoSuchCompany", set())

        self.assertEqual(hits, [])

    def test_skips_known_slug(self):
        rules = [
            ("boards-api.greenhouse.io/v1/boards/acme/jobs",
             FakeResponse(status=200, payload={"jobs": [{"id": 1}]})),
            ("boards-api.greenhouse.io/v1/boards/acme",
             FakeResponse(status=200, payload={"name": "Acme"})),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            hits = _discover_one("Acme", known_slugs={"acme"})

        self.assertEqual(hits, [])

    def test_empty_board_skipped(self):
        rules = [
            ("boards-api.greenhouse.io/v1/boards/acme/jobs",
             FakeResponse(status=200, payload={"jobs": []})),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            hits = _discover_one("Acme", set())

        self.assertEqual(hits, [])

    def test_name_mismatch_rejected(self):
        """If the board's display name doesn't match the input, skip it."""
        rules = [
            ("boards-api.greenhouse.io/v1/boards/pan/jobs",
             FakeResponse(status=200, payload={"jobs": [{"id": 1}]})),
            ("boards-api.greenhouse.io/v1/boards/pan",
             FakeResponse(status=200, payload={"name": "PAN Foundation"})),
        ]
        with mock.patch("discover.requests") as mock_req:
            mock_req.Session.return_value = _fake_session(rules)
            # "pan" isn't in _slugify output for "Palo Alto Networks", but let's
            # test the name guard directly by injecting "pan" via known_slugs bypass
            # Actually, let's test with a name whose slugs DO produce "pan"
            hits = _discover_one("PAN Corp", set())

        # "pan" slug → GH board "PAN Foundation" — names_match("PAN Foundation", "PAN Corp")
        # "panfoundation" vs "pancorp" → neither is substring of the other.
        # prefix(6): "panfou" vs "pancor" → no match.  Rejected.
        self.assertEqual(hits, [])


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #

class TestLoadExisting(unittest.TestCase):

    def test_extracts_names_and_slugs(self):
        config = {
            "boards": [
                {"name": "Stripe", "type": "greenhouse",
                 "url": "https://boards.greenhouse.io/stripe"},
                {"name": "Figma", "type": "lever",
                 "url": "https://jobs.lever.co/figma"},
                {"name": "Notion", "type": "ashby",
                 "url": "https://jobs.ashbyhq.com/notion"},
                {"name": "Big Corp", "type": "workday",
                 "url": "https://bigcorp.wd1.myworkdayjobs.com"},
            ],
        }
        with mock.patch("discover.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(config)
            names, slugs = _load_existing()

        self.assertIn("stripe", names)
        self.assertIn("figma", names)
        self.assertIn("notion", names)
        self.assertIn("big corp", names)
        self.assertIn("stripe", slugs)
        self.assertIn("figma", slugs)
        self.assertIn("notion", slugs)

    def test_no_config(self):
        with mock.patch("discover.CONFIG_PATH") as mock_path:
            mock_path.exists.return_value = False
            names, slugs = _load_existing()

        self.assertEqual(names, set())
        self.assertEqual(slugs, set())


if __name__ == "__main__":
    unittest.main()
