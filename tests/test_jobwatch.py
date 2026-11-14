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


class TestSchedule(unittest.TestCase):
    BOARDS = (
        [{"name": f"w5-{i}", "type": "workday", "url": f"https://t{i}.wd5.myworkdayjobs.com/x"}
         for i in range(4)]
        + [{"name": f"w1-{i}", "type": "workday", "url": f"https://t{i}.wd1.myworkdayjobs.com/x"}
           for i in range(3)]
        + [{"name": f"gh-{i}", "type": "greenhouse", "url": "https://x"} for i in range(5)]
        + [{"name": "lulu", "type": "avature", "url": "https://x"}])

    def test_is_a_permutation(self):
        got = jobwatch._schedule(self.BOARDS)
        self.assertCountEqual([b["name"] for b in got], [b["name"] for b in self.BOARDS])

    def test_every_lane_starts_in_the_first_round(self):
        # A slow type at the end of config must not wait behind every other group.
        first = [b["name"] for b in jobwatch._schedule(self.BOARDS)[:4]]
        self.assertEqual(first, ["w5-0", "w1-0", "lulu", "gh-0"])

    def test_pods_are_interleaved(self):
        pods = [jobwatch._lane(b) for b in jobwatch._schedule(self.BOARDS)]
        wd = [p for p in pods if "myworkdayjobs" in p]
        self.assertEqual(wd[:6], ["wd5.myworkdayjobs.com", "wd1.myworkdayjobs.com"] * 3)


CONFIG = {"filters": FILTERS, "query_terms": ["intern"], "resolve_multi_location": True,
          "request_delay_scale": 0.2, "workday_pod_limit": 6}


class TestMakeFetcher(unittest.TestCase):
    """fetch_board is built from a config value, so these tests hand it one directly
    instead of patching module globals."""

    def setUp(self):
        # make_fetcher applies the runtime knobs; restore them after each test.
        for module, attr in ((common, "DELAY_SCALE"), (jobwatch.workday_mod, "POD_LIMIT")):
            patcher = mock.patch.object(module, attr, getattr(module, attr))
            patcher.start()
            self.addCleanup(patcher.stop)

    def _capture(self, board, config=CONFIG):
        """Register a fake adapter that records the board dict it was handed."""
        seen = {}
        with mock.patch.dict(jobwatch.ADAPTERS,
                             {"fake": lambda b: seen.update(b) or []}):
            name, jobs, error = jobwatch.make_fetcher(config)({**board, "type": "fake"})
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

    def test_optional_knobs_absent_inject_nothing(self):
        got = self._capture({"name": "Acme", "url": "https://x"},
                            {"request_delay_scale": 0, "workday_pod_limit": 1})
        self.assertNotIn("query", got)
        self.assertNotIn("resolve_multi_location", got)
        self.assertTrue(got["title_ok"]("Anything At All"))

    def test_board_overrides_are_preserved(self):
        got = self._capture({"name": "Acme", "url": "https://x", "query": ["phd"],
                             "resolve_multi_location": False})
        self.assertEqual(got["query"], ["phd"])
        self.assertFalse(got["resolve_multi_location"])

    def test_applies_runtime_knobs(self):
        jobwatch.make_fetcher({**CONFIG, "request_delay_scale": 0.7, "workday_pod_limit": 3})
        self.assertEqual(common.DELAY_SCALE, 0.7)
        self.assertEqual(jobwatch.workday_mod.POD_LIMIT, 3)

    def test_missing_required_knob_fails_loud(self):
        with self.assertRaises(KeyError):
            jobwatch.make_fetcher({"filters": FILTERS})

    def test_unknown_type_returns_error_not_raise(self):
        fetch = jobwatch.make_fetcher(CONFIG)
        name, jobs, error = fetch({"name": "Acme", "type": "nope"})
        self.assertEqual(jobs, [])
        self.assertIn("no adapter", error)

    def test_adapter_exception_becomes_error(self):
        def boom(board):
            raise ValueError("kaboom")

        with mock.patch.dict(jobwatch.ADAPTERS, {"fake": boom}):
            fetch = jobwatch.make_fetcher(CONFIG)
            name, jobs, error = fetch({"name": "Acme", "type": "fake"})
        self.assertEqual(jobs, [])
        self.assertEqual(error, "kaboom")


def posting(jid, title="Software Intern", location="Toronto, ON", company="Acme"):
    return {"id": jid, "title": title, "location": location,
            "posted": "2026-11-01", "url": f"https://x/{jid}", "company": company}


class TestRun(unittest.TestCase):
    """run() is fetch + diff against seen + filter. The fetcher is injected, so each
    test scripts what every board "returns" and asserts on the RunResult."""

    def _run(self, responses, seen=(), raw=False, filters=FILTERS):
        """responses: {board name: list of jobs, or an error string}."""
        def fetch(board):
            got = responses[board["name"]]
            return (board["name"], [], got) if isinstance(got, str) else (board["name"], got, None)

        boards = [{"name": n, "type": "greenhouse", "url": "https://x"} for n in responses]
        return jobwatch.run(boards, fetch, set(seen), filters, workers=2, raw=raw)

    def test_new_match_is_shown_and_marked_seen(self):
        res = self._run({"Acme": [posting("a:1")]})
        self.assertEqual([j["id"] for j in res.new_jobs], ["a:1"])
        self.assertEqual(res.seen, {"a:1"})

    def test_already_seen_job_is_not_shown_again(self):
        res = self._run({"Acme": [posting("a:1"), posting("a:2")]}, seen={"a:1"})
        self.assertEqual([j["id"] for j in res.new_jobs], ["a:2"])
        self.assertEqual(res.seen, {"a:1", "a:2"})

    def test_non_match_is_not_marked_seen(self):
        """Loosening a filter later must still surface jobs it used to hide."""
        res = self._run({"Acme": [posting("a:1", title="Marketing Manager")]})
        self.assertEqual(res.new_jobs, [])
        self.assertEqual(res.seen, set())

    def test_raw_still_hides_already_seen(self):
        res = self._run({"Acme": [posting("a:1"), posting("a:2")]}, seen={"a:1"}, raw=True)
        self.assertEqual([j["id"] for j in res.new_jobs], ["a:2"])

    def test_duplicate_id_in_one_run_is_shown_once(self):
        for raw in (False, True):
            with self.subTest(raw=raw):
                res = self._run({"A": [posting("dup")], "B": [posting("dup")]}, raw=raw)
                self.assertEqual(len(res.new_jobs), 1)

    def test_failed_board_is_reported_and_others_still_count(self):
        res = self._run({"Bad": "HTTP 500", "Good": [posting("g:1")]})
        self.assertEqual(res.failed, [("Bad", "HTTP 500")])
        self.assertEqual([j["id"] for j in res.new_jobs], ["g:1"])

    def test_input_seen_is_not_mutated(self):
        seen = {"old"}
        jobwatch.run([{"name": "A", "type": "greenhouse", "url": "https://x"}],
                     lambda b: ("A", [posting("a:1")], None), seen, FILTERS, workers=1)
        self.assertEqual(seen, {"old"})

    def test_new_jobs_sorted_by_company_then_title(self):
        res = self._run({"A": [posting("1", "Software Intern", company="Zed"),
                               posting("2", "Software Co-op", company="Acme"),
                               posting("3", "Data Intern", company="Acme")]})
        self.assertEqual([j["id"] for j in res.new_jobs], ["3", "2", "1"])

    def test_on_board_reports_every_board(self):
        calls = []
        boards = [{"name": n, "type": "greenhouse", "url": "https://x"} for n in "ABC"]
        jobwatch.run(boards, lambda b: (b["name"], [], None), set(), FILTERS, workers=2,
                     on_board=lambda done, total, name, *_: calls.append((done, total, name)))
        self.assertEqual(sorted(c[0] for c in calls), [1, 2, 3])
        self.assertEqual({c[2] for c in calls}, set("ABC"))
        self.assertTrue(all(c[1] == 3 for c in calls))


class TestReportLines(unittest.TestCase):
    """One renderer feeds both stdout (indented) and the saved .out log."""

    def test_lists_each_job(self):
        res = jobwatch.RunResult(new_jobs=[posting("a:1")], failed=[], seen=set())
        lines = jobwatch.report_lines(res)
        self.assertEqual(lines[0], "1 new matching job(s):")
        self.assertIn("• Software Intern", lines)
        self.assertIn("    Acme — Toronto, ON  (2026-11-01)", lines)
        self.assertIn("    https://x/a:1", lines)

    def test_no_jobs(self):
        res = jobwatch.RunResult(new_jobs=[], failed=[], seen=set())
        self.assertEqual(jobwatch.report_lines(res), ["No new matching jobs."])


if __name__ == "__main__":
    unittest.main()
