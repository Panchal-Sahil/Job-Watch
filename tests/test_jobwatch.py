"""Unit tests for jobwatch's filter logic and board plumbing (offline, stdlib only).

`matches()` decides what actually reaches you, and it had no tests. It matters more
than usual now that its title half is split out as `_title_matches()` and handed to
adapters as a prefilter hint: the Workday adapter uses it to skip a per-job location
lookup for titles that can't pass. That shortcut is only sound while the prefilter is
never stricter than the real filter — `test_prefilter_never_stricter_than_matches`
below is the property that pins it.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobwatch
from adapters import common

# Shaped like the real config: (early-career) AND (tech domain), minus senior roles.
FILTERS = {
    "title_groups": [["intern", "co-op", "new grad"],
                     ["software", "data", "re:\\bML\\b"]],
    "title_none": ["senior", "staff", "manager"],
    "location_any": ["toronto", "remote", "canada"],
    "location_none": ["united states", "india"],
    "location_rescue": ["canada", "ontario"],
}


def job(title, location="Toronto, ON"):
    return {"id": "x:1", "title": title, "location": location,
            "posted": "", "url": "", "company": "Acme"}


class TestTitleMatches(unittest.TestCase):
    def test_all_groups_must_match(self):
        """AND across groups, OR within one."""
        self.assertTrue(jobwatch._title_matches("Software Intern", FILTERS))
        self.assertTrue(jobwatch._title_matches("Data Co-op Student", FILTERS))
        # early-career, but no tech domain
        self.assertFalse(jobwatch._title_matches("Marketing Intern", FILTERS))
        # tech domain, but not early-career
        self.assertFalse(jobwatch._title_matches("Software Developer", FILTERS))

    def test_title_none_rejects(self):
        self.assertFalse(jobwatch._title_matches("Senior Software Intern", FILTERS))
        self.assertFalse(jobwatch._title_matches("Data Intern Manager", FILTERS))

    def test_title_any_used_when_no_groups(self):
        filters = {"title_any": ["intern", "co-op"]}
        self.assertTrue(jobwatch._title_matches("Marketing Intern", filters))
        self.assertFalse(jobwatch._title_matches("Marketing Lead", filters))

    def test_title_groups_wins_over_title_any(self):
        filters = {**FILTERS, "title_any": ["anything"]}
        self.assertTrue(jobwatch._title_matches("Software Intern", filters))

    def test_word_boundary_and_plural_tolerance(self):
        """"intern" matches Internship but not Internal — the whole point of _word_match."""
        self.assertTrue(jobwatch._title_matches("Software Internship", FILTERS))
        self.assertFalse(jobwatch._title_matches("Internal Software Tools", FILTERS))

    def test_regex_keyword(self):
        """A `re:` keyword is a raw, case-sensitive regex."""
        self.assertTrue(jobwatch._title_matches("ML Intern", FILTERS))
        self.assertFalse(jobwatch._title_matches("HTML Intern", FILTERS))

    def test_trailing_roman_numeral_regex(self):
        """The entry-level "…Engineer I" rule has to stay anchored to a trailing
        I, or the bare `\\bI\\b` fires on an initialism like "I&C Engineer" and
        pulls in senior non-early-career roles."""
        filters = {"title_groups": [["intern", "re:\\bI\\b(?=\\s*[(,\u2013\u2014-]|\\s*$)"],
                                    ["software", "systems", "data"]]}
        for title in ("Software Engineer I",
                      "Data Engineer I (Production Support)",
                      "Software Engineer I, Toronto"):
            self.assertTrue(jobwatch._title_matches(title, filters), title)
        self.assertFalse(
            jobwatch._title_matches("I&C Engineer / Control Systems Specialist", filters))

    def test_empty_filters_pass_everything(self):
        self.assertTrue(jobwatch._title_matches("Chief Executive Officer", {}))


class TestMatchesLocation(unittest.TestCase):
    def test_location_any_required(self):
        self.assertTrue(jobwatch.matches(job("Software Intern", "Toronto, ON"), FILTERS))
        self.assertFalse(jobwatch.matches(job("Software Intern", "Berlin, DE"), FILTERS))

    def test_location_none_drops(self):
        self.assertFalse(
            jobwatch.matches(job("Software Intern", "Remote, United States"), FILTERS))

    def test_location_rescue_saves_a_dual_posting(self):
        """"Remote (United States | Canada)" is a Canadian posting too — keep it."""
        self.assertTrue(
            jobwatch.matches(job("Software Intern", "Remote (United States | Canada)"),
                             FILTERS))

    def test_location_matching_is_case_insensitive(self):
        self.assertTrue(jobwatch.matches(job("Software Intern", "TORONTO, ON"), FILTERS))

    def test_keyword_must_not_run_into_a_longer_word(self):
        """Regression: location matching is substring-based, so the two-letter
        province codes used to swallow city names — ", pe" (PEI) matched "East
        Peoria, Illinois" and ", ab" (Alberta) matched "Abu Dhabi". Both let
        foreign postings through: the Peoria one via location_rescue, undoing a
        correct location_none drop."""
        filters = {**FILTERS,
                   "location_any": [", ab", ", pe", "canada"],
                   "location_none": ["illinois"],
                   "location_rescue": [", pe", "canada"]}
        self.assertFalse(
            jobwatch.matches(job("Software Intern", "East Peoria, Illinois"), filters))
        self.assertFalse(
            jobwatch.matches(job("Software Intern", "Abu Dhabi, Abu Dhabi, ae"), filters))
        # the codes still match a real province
        self.assertTrue(
            jobwatch.matches(job("Software Intern", "Charlottetown, PE"), filters))
        self.assertTrue(
            jobwatch.matches(job("Software Intern", "Calgary, AB"), filters))

    def test_punctuation_keyword_still_matches_at_end_of_string(self):
        """A boundary is only required on an alphanumeric edge, so "u.s." — which
        is usually title-final — is not broken by the fix above."""
        filters = {**FILTERS, "location_none": ["u.s."], "location_rescue": []}
        self.assertFalse(
            jobwatch.matches(job("Software Intern", "Toronto, U.S."), filters))

    def test_ambiguous_city_is_dropped_unless_a_province_confirms_it(self):
        """"London" is in location_any for London, Ontario, but a UK posting can
        report a bare "London" with no country. Listing it in location_none makes
        the Canadian one depend on a rescue keyword naming the province."""
        filters = {**FILTERS,
                   "location_any": ["london", "toronto", "canada"],
                   "location_none": ["london"],
                   "location_rescue": ["ontario", ", on", "canada"]}
        self.assertFalse(jobwatch.matches(job("Software Intern", "London"), filters))
        self.assertTrue(
            jobwatch.matches(job("Software Intern", "London, Ontario, Canada"), filters))
        self.assertTrue(jobwatch.matches(job("Software Intern", "London, ON"), filters))


class TestPrefilterInvariant(unittest.TestCase):
    """The property the Workday shortcut rests on: a title the prefilter rejects can
    never be surfaced, whatever its location. If this fails, an adapter that skipped
    enrichment on a title_ok(...) == False could be hiding a job that should appear."""

    TITLES = ["Software Intern", "Marketing Intern", "Senior Data Co-op", "Data Intern",
              "Software Developer", "ML Intern", "Internal Tools Lead", "New Grad Software"]
    LOCATIONS = ["Toronto, ON", "Berlin, DE", "Remote (United States | Canada)",
                 "Remote, United States", "3 Locations", ""]

    def test_prefilter_never_stricter_than_matches(self):
        for title in self.TITLES:
            if jobwatch._title_matches(title, FILTERS):
                continue
            for loc in self.LOCATIONS:
                with self.subTest(title=title, location=loc):
                    self.assertFalse(jobwatch.matches(job(title, loc), FILTERS))


class TestShortError(unittest.TestCase):
    """The board-status line is the only record of a failure in a redirected run
    (the full text goes to stderr), so it has to stay self-diagnosing."""

    def test_short_error_is_left_alone(self):
        msg = "404 Client Error: Not Found for url: https://boards-api.greenhouse.io/x"
        self.assertEqual(jobwatch._short_error(msg), msg)

    def test_long_error_keeps_head_and_tail(self):
        """A head-only cut rendered every urllib3 failure as the same prefix. The
        host is at the front and the cause is at the back — both have to survive."""
        msg = ("HTTPSConnectionPool(host='careers.capgemini.com', port=443): Max retries "
               "exceeded with url: /services/recruiting/v1/jobs " + "x" * 200 +
               " (Caused by ReadTimeoutError('Read timed out. (read timeout=20)'))")
        out = jobwatch._short_error(msg)
        self.assertLessEqual(len(out), 140)
        self.assertIn("careers.capgemini.com", out)
        self.assertIn("Read timed out", out)

    def test_newlines_are_collapsed(self):
        self.assertEqual(jobwatch._short_error("a\n  b\tc"), "a b c")


class TestSharedSession(unittest.TestCase):
    """The retry policy's *scope* is the load-bearing part: retrying statuses would
    duplicate Workday's 429 backoff and would re-ask a permanently closed board."""

    def test_timeout_is_a_connect_read_pair(self):
        self.assertIsInstance(common.TIMEOUT, tuple)
        connect, read = common.TIMEOUT
        self.assertLess(connect, read)

    def test_retries_transport_but_never_statuses(self):
        retry = common._RETRY
        self.assertEqual(retry.total, 2)
        self.assertEqual(retry.connect, 2)
        self.assertEqual(retry.read, 2)
        self.assertEqual(retry.status, 0)
        self.assertFalse(retry.status_forcelist)
        # allowed_methods=None means "every method", POST included — safe only
        # because every POST in these adapters is a paging query.
        self.assertIsNone(retry.allowed_methods)

    def test_shared_session_refuses_cookies(self):
        """It's shared across boards and threads, so it must not accumulate state."""
        jar = common.HTTP.cookies
        self.assertFalse(jar.get_policy().set_ok(mock.Mock(), mock.Mock()))

    def test_new_session_mounts_retries_on_both_schemes(self):
        sess = common.new_session()
        for scheme in ("https://", "http://"):
            self.assertEqual(sess.adapters[scheme].max_retries, common._RETRY)


class TestFetchBoard(unittest.TestCase):
    def setUp(self):
        # fetch_board reads config through cached module globals; pin them so these
        # tests don't depend on the live config.json.
        for attr, value in (("_FILTERS", FILTERS), ("_DEFAULT_QUERY", ["intern"]),
                            ("_RESOLVE_MULTI_LOC", True)):
            patcher = mock.patch.object(jobwatch, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _capture(self, board):
        """Register a fake adapter that records the board dict it was handed."""
        seen = {}
        with mock.patch.dict(jobwatch.ADAPTERS,
                             {"fake": lambda b: seen.update(b) or []}):
            name, jobs, error = jobwatch.fetch_board({**board, "type": "fake"})
        self.assertIsNone(error)
        return seen

    def test_injects_title_ok_predicate(self):
        got = self._capture({"name": "Acme", "url": "https://x"})
        self.assertTrue(callable(got["title_ok"]))
        self.assertTrue(got["title_ok"]("Software Intern"))
        self.assertFalse(got["title_ok"]("Marketing Manager"))

    def test_injects_query_and_resolve_flag(self):
        got = self._capture({"name": "Acme", "url": "https://x"})
        self.assertEqual(got["query"], ["intern"])
        self.assertTrue(got["resolve_multi_location"])

    def test_board_overrides_are_preserved(self):
        got = self._capture({"name": "Acme", "url": "https://x", "query": ["phd"],
                             "resolve_multi_location": False})
        self.assertEqual(got["query"], ["phd"])
        self.assertFalse(got["resolve_multi_location"])

    def test_unknown_type_returns_error_not_raise(self):
        name, jobs, error = jobwatch.fetch_board({"name": "Acme", "type": "nope"})
        self.assertEqual(jobs, [])
        self.assertIn("no adapter", error)

    def test_adapter_exception_becomes_error(self):
        def boom(board):
            raise ValueError("kaboom")

        with mock.patch.dict(jobwatch.ADAPTERS, {"fake": boom}):
            name, jobs, error = jobwatch.fetch_board({"name": "Acme", "type": "fake"})
        self.assertEqual(jobs, [])
        self.assertEqual(error, "kaboom")


if __name__ == "__main__":
    unittest.main()
