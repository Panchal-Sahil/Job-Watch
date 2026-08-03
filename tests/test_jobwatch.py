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
