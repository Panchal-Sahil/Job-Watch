"""Unit tests for the ATS adapters, with HTTP mocked (offline, stdlib only).

Each adapter derives a JSON endpoint from its board dict, fetches (often paging),
and normalizes rows into the six-key contract dict. These tests patch each
adapter module's `requests` with a `FakeRequests` (see tests/fakehttp.py) that
scripts responses by (method, url-substring), and assert:

  * the normalized output — id format, parsed title/location/url/posted;
  * the six-key contract (every dict has exactly the required keys, stable id);
  * paging *termination* — a 2-page scenario that sets the adapter's own total
    field, so the loop must page once and then stop (the class of bug that hid
    in successfactors: a mishandled past-the-end page).

`time.sleep` is no-opped so the polite inter-page delays don't slow the suite.
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import (ashby, bamboohr, dayforce, eightfold, greenhouse, icims,
                      lever, oracle, phenom, radancy, rippling, smartrecruiters,
                      successfactors, ukg, workday)
from tests.fakehttp import FakeRequests, jresp

REQUIRED_KEYS = {"id", "title", "location", "posted", "url", "company"}


class AdapterTestCase(unittest.TestCase):
    """Base: patch an adapter module's `requests`, no-op sleep, assert the contract."""

    def run_adapter(self, module, fetch, board, rules, default=None):
        fake = FakeRequests(rules, default=default)
        with mock.patch.object(module, "requests", fake), \
             mock.patch("time.sleep", lambda *a, **k: None):
            jobs = fetch(board)
        return jobs, fake

    def assert_contract(self, jobs):
        self.assertTrue(jobs, "adapter returned no jobs")
        for j in jobs:
            self.assertEqual(set(j), REQUIRED_KEYS,
                             f"job dict keys {set(j)} != contract {REQUIRED_KEYS}")
            self.assertTrue(j["id"], "job id must be non-empty (it's the dedup key)")


class TestGreenhouse(AdapterTestCase):
    def test_normalizes_and_derives_token(self):
        payload = {"jobs": [{
            "id": 123, "title": "  Software Intern  ",
            "location": {"name": "Toronto, ON"},
            "updated_at": "2026-01-15T10:00:00Z",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
        }]}
        jobs, fake = self.run_adapter(
            greenhouse, greenhouse.fetch_greenhouse,
            {"name": "Acme", "url": "https://job-boards.greenhouse.io/acme"},
            [("GET", "boards-api.greenhouse.io/v1/boards/acme/jobs", jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "gh:acme:123")
        self.assertEqual(j["title"], "Software Intern")
        self.assertEqual(j["location"], "Toronto, ON")
        self.assertEqual(j["posted"], "2026-01-15")
        self.assertEqual(j["url"], "https://job-boards.greenhouse.io/acme/jobs/123")
        self.assertEqual(j["company"], "Acme")


class TestLever(AdapterTestCase):
    def test_normalizes_and_converts_epoch(self):
        payload = [{
            "id": "abc-1", "text": "Data Intern",
            "categories": {"location": "Remote"},
            "createdAt": 1704067200000,  # 2024-01-01 UTC
            "hostedUrl": "https://jobs.lever.co/acme/abc-1",
        }]
        jobs, _ = self.run_adapter(
            lever, lever.fetch_lever,
            {"url": "https://jobs.lever.co/acme"},
            [("GET", "api.lever.co/v0/postings/acme", jresp(payload=payload))])
        self.assert_contract(jobs)
        j = jobs[0]
        self.assertEqual(j["id"], "lever:acme:abc-1")
        self.assertEqual(j["location"], "Remote")
        self.assertEqual(j["posted"], "2024-01-01")
        self.assertEqual(j["company"], "acme")  # falls back to slug


class TestAshby(AdapterTestCase):
    def test_normalizes(self):
        payload = {"jobs": [{
            "id": "xyz", "title": "New Grad Engineer", "location": "Vancouver, BC",
            "publishedDate": "2026-02-01T00:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/acme/xyz",
        }]}
        jobs, _ = self.run_adapter(
            ashby, ashby.fetch_ashby,
            {"url": "https://jobs.ashbyhq.com/acme"},
            [("GET", "api.ashbyhq.com/posting-api/job-board/acme", jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "ashby:acme:xyz")
        self.assertEqual(jobs[0]["posted"], "2026-02-01")


class TestBambooHR(AdapterTestCase):
    def test_location_and_remote_fallback(self):
        payload = {"result": [
            {"id": 1, "jobOpeningName": "Intern", "location": {"city": "Ottawa", "state": "ON"}},
            {"id": 2, "jobOpeningName": "Remote Intern", "location": {}, "isRemote": True},
        ]}
        jobs, _ = self.run_adapter(
            bamboohr, bamboohr.fetch_bamboohr,
            {"url": "https://acme.bamboohr.com/careers"},
            [("GET", "acme.bamboohr.com/careers/list", jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "bamboo:acme:1")
        self.assertEqual(jobs[0]["location"], "Ottawa, ON")
        self.assertEqual(jobs[1]["location"], "Remote")  # empty location, isRemote


class TestRippling(AdapterTestCase):
    def test_normalizes(self):
        payload = [{"uuid": "u1", "name": "Intern",
                    "workLocation": {"label": "Toronto"},
                    "url": "https://ats.rippling.com/acme/jobs/u1"}]
        jobs, _ = self.run_adapter(
            rippling, rippling.fetch_rippling,
            {"url": "https://ats.rippling.com/acme/jobs"},
            [("GET", "api.rippling.com/platform/api/ats/v1/board/acme/jobs", jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "rippling:acme:u1")
        self.assertEqual(jobs[0]["location"], "Toronto")


class TestSmartRecruiters(AdapterTestCase):
    def test_pages_and_stops_on_total(self):
        def route(url, **kw):
            offset = kw["params"]["offset"]
            if offset == 0:
                content = [{"id": f"p{i}", "name": f"Job {i}",
                            "location": {"city": "Toronto", "region": "ON", "country": "CA"},
                            "releasedDate": "2026-01-15T00:00:00Z"} for i in range(100)]
                return jresp(payload={"totalFound": 150, "content": content})
            content = [{"id": f"p{i}", "name": f"Job {i}",
                        "location": {"city": "Montreal", "region": "QC", "country": "CA"},
                        "releasedDate": "2026-01-16T00:00:00Z"} for i in range(100, 150)]
            return jresp(payload={"totalFound": 150, "content": content})

        jobs, fake = self.run_adapter(
            smartrecruiters, smartrecruiters.fetch_smartrecruiters,
            {"url": "https://careers.smartrecruiters.com/Acme"},
            [("GET", "api.smartrecruiters.com/v1/companies/Acme/postings", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 150)  # paged twice, then stopped
        self.assertEqual(jobs[0]["id"], "smartrecruiters:Acme:p0")
        self.assertEqual(jobs[0]["location"], "Toronto, ON, CA")
        posts = [c for c in fake.calls if c[0] == "GET"]
        self.assertEqual(len(posts), 2)  # exactly two pages, no spin


class TestOracle(AdapterTestCase):
    def test_pages_and_stops_on_total(self):
        def route(url, **kw):
            offset = int(kw["params"]["finder"].split("offset=")[1])
            if offset == 0:
                reqs = [{"Id": i, "Title": f"Job {i}", "PrimaryLocation": "Calgary, AB",
                         "PostedDate": "2026-01-15"} for i in range(200)]
                return jresp(payload={"items": [{"TotalJobsCount": 250, "requisitionList": reqs}]})
            reqs = [{"Id": i, "Title": f"Job {i}", "PrimaryLocation": "Edmonton, AB",
                     "PostedDate": "2026-01-16"} for i in range(200, 250)]
            return jresp(payload={"items": [{"TotalJobsCount": 250, "requisitionList": reqs}]})

        jobs, fake = self.run_adapter(
            oracle, oracle.fetch_oracle,
            {"url": "https://acme.fa.ca2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"},
            [("GET", "acme.fa.ca2.oraclecloud.com/hcmRestApi", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 250)
        self.assertEqual(jobs[0]["id"], "oracle:acme.fa.ca2.oraclecloud.com:0")
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 2)


class TestUKG(AdapterTestCase):
    def test_pages_and_stops_on_total(self):
        def route(url, **kw):
            skip = kw["json"]["opportunitySearch"]["Skip"]
            if skip == 0:
                opps = [{"Id": i, "Title": f"Job {i}",
                         "Locations": [{"LocalizedDescription": "Toronto, ON"}],
                         "PostedDate": "2026-01-15"} for i in range(100)]
                return jresp(payload={"totalCount": 130, "opportunities": opps})
            opps = [{"Id": i, "Title": f"Job {i}",
                     "Locations": [{"LocalizedDescription": "Ottawa, ON"}],
                     "PostedDate": "2026-01-16"} for i in range(100, 130)]
            return jresp(payload={"totalCount": 130, "opportunities": opps})

        jobs, fake = self.run_adapter(
            ukg, ukg.fetch_ukg,
            {"url": "https://recruiting.ultipro.ca/ACME1000/JobBoard/abc-guid/"},
            [("POST", "recruiting.ultipro.ca/ACME1000/JobBoard/abc-guid/JobBoardView/LoadSearchResults", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 130)
        self.assertEqual(jobs[0]["id"], "ukg:ACME1000:0")
        self.assertEqual(jobs[0]["location"], "Toronto, ON")
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 2)


class TestWorkday(AdapterTestCase):
    def _job(self, i, loc="Toronto"):
        return {"title": f"Job {i}", "externalPath": f"/job/{i}",
                "bulletFields": [f"R{i}"], "locationsText": loc,
                "postedOn": "Posted 5 Days Ago"}

    def test_pages_and_stops_on_total(self):
        def route(url, **kw):
            offset = kw["json"]["offset"]
            if offset == 0:
                return jresp(payload={"total": 25,
                                      "jobPostings": [self._job(i) for i in range(20)]})
            return jresp(payload={"total": 25,
                                  "jobPostings": [self._job(i) for i in range(20, 25)]})

        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers"},
            [("POST", "acme.wd5.myworkdayjobs.com/wday/cxs/acme/External_Careers/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 25)
        self.assertEqual(jobs[0]["id"], "acme:R0")
        self.assertEqual(jobs[0]["url"], "https://acme.wd5.myworkdayjobs.com/job/0")
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 2)

    def test_resolve_multi_location_expands_placeholder(self):
        list_payload = {"total": 1, "jobPostings": [self._job(0, loc="3 Locations")]}
        detail_payload = {"jobPostingInfo": {"location": "Toronto",
                                             "additionalLocations": ["Montreal", "Vancouver"]}}
        jobs, _ = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers",
             "resolve_multi_location": True},
            [("POST", "/wday/cxs/acme/External_Careers/jobs", jresp(payload=list_payload)),
             ("GET", "/wday/cxs/acme/External_Careers/job/0", jresp(payload=detail_payload))])
        self.assertEqual(jobs[0]["location"], "Toronto, Montreal, Vancouver")

    def test_myworkdaysite_tenant_derivation(self):
        payload = {"total": 1, "jobPostings": [self._job(0)]}
        jobs, _ = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://wd3.myworkdaysite.com/en-US/recruiting/gflenv/Careers"},
            [("POST", "wd3.myworkdaysite.com/wday/cxs/gflenv/Careers/jobs", jresp(payload=payload))])
        self.assertEqual(jobs[0]["id"], "gflenv:R0")  # tenant from recruiting/<tenant>

    def test_missing_site_raises(self):
        with self.assertRaises(ValueError):
            self.run_adapter(
                workday, workday.fetch_workday,
                {"url": "https://acme.wd5.myworkdayjobs.com/"}, [])


class TestDayforce(AdapterTestCase):
    def _job(self, i):
        return {"jobPostingId": i, "jobTitle": f"Job {i}",
                "postingLocations": [{"cityName": "Halifax", "stateCode": "NS"}],
                "postingStartTimestampUTC": "2026-01-15T00:00:00Z"}

    def test_csrf_handshake_and_paging(self):
        def route(url, **kw):
            start = kw["json"]["paginationStart"]
            if start == 0:
                return jresp(payload={"offset": 0, "count": 25, "maxCount": 40,
                                      "jobPostings": [self._job(i) for i in range(25)]})
            return jresp(payload={"offset": 25, "count": 15, "maxCount": 40,
                                  "jobPostings": [self._job(i) for i in range(25, 40)]})

        rules = [
            ("GET", "/api/auth/csrf", jresp(payload={"csrfToken": "tok123"})),
            ("POST", "/api/geo/sobeys/jobposting/search", route),
            ("GET", "jobs.dayforcehcm.com", jresp(text="<html>landing</html>")),  # prime cookies
        ]
        jobs, fake = self.run_adapter(
            dayforce, dayforce.fetch_dayforce,
            {"url": "https://jobs.dayforcehcm.com/en-CA/sobeys/privateclientsite"}, rules)
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 40)
        self.assertEqual(jobs[0]["id"], "dayforce:sobeys:0")
        self.assertEqual(jobs[0]["location"], "Halifax, NS")
        # confirm the handshake actually happened: landing GET + csrf GET occurred
        gets = [c[1] for c in fake.calls if c[0] == "GET"]
        self.assertTrue(any("/api/auth/csrf" in u for u in gets))
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 2)


class TestICIMSCareersHome(AdapterTestCase):
    def _row(self, i):
        return {"data": {"req_id": f"R{i}", "title": f"Job {i}", "full_location": "Toronto, ON",
                         "posted_date": "2026-01-15", "apply_url": f"https://careers.acme.com/jobs/R{i}"}}

    def test_pages_and_stops(self):
        def route(url, **kw):
            page = kw["params"]["page"]
            if page == 1:
                return jresp(payload={"totalCount": 15, "jobs": [self._row(i) for i in range(10)]})
            return jresp(payload={"totalCount": 15, "jobs": [self._row(i) for i in range(10, 15)]})

        jobs, fake = self.run_adapter(
            icims, icims.fetch_icims,
            {"url": "https://careers.acme.com/"},  # non-.icims.com -> careers-home SPA
            [("GET", "careers.acme.com/api/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 15)
        self.assertEqual(jobs[0]["id"], "icims:careers.acme.com:R0")
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 2)


class TestSuccessFactorsModern(AdapterTestCase):
    """The modern Career Site Builder JSON path, incl. the past-the-end-page
    regression: a later page that drops `jobSearchResult` must end paging and
    keep the jobs already collected — not discard them and fall back to classic."""

    def _item(self, i):
        return {"response": {"id": i, "unifiedStandardTitle": f"Job {i}",
                             "jobLocationShort": "Vancouver, BC",
                             "unifiedStandardStart": "2026-01-01"}}

    def test_paging_past_end_keeps_jobs(self):
        def route(url, **kw):
            page = json.loads(kw["data"])["pageNumber"]  # modern path posts data=, not json=
            if page == 0:
                return jresp(payload={"totalJobs": 15,
                                      "jobSearchResult": [self._item(i) for i in range(10)]})
            if page == 1:
                return jresp(payload={"totalJobs": 15,
                                      "jobSearchResult": [self._item(i) for i in range(10, 15)]})
            # page 2 is past the end: 200 OK, dict WITHOUT jobSearchResult (Teck's behavior)
            return jresp(payload={"totalJobs": 15})

        jobs, _ = self.run_adapter(
            successfactors, successfactors.fetch_successfactors,
            {"name": "Acme", "url": "https://careers.acme.com/search/"},
            [("GET", "careers.acme.com/search", jresp(text="ok")),
             ("POST", "/services/recruiting/v1/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 15)  # NOT 0 (the bug), NOT discarded
        self.assertEqual(jobs[0]["id"], "sf:careers.acme.com:0")
        self.assertEqual(jobs[0]["location"], "Vancouver, BC")

    def test_page0_failure_falls_back_to_classic(self):
        # Page-0 non-OK means "not a modern site" -> None -> classic tile scrape.
        # Classic here returns an empty (but valid) tile page, so we get []
        # without an exception — proving the fallback path was taken.
        jobs, fake = self.run_adapter(
            successfactors, successfactors.fetch_successfactors,
            {"name": "Acme", "url": "https://careers.acme.com/search/"},
            [("GET", "careers.acme.com/search", jresp(text="ok")),
             ("POST", "/services/recruiting/v1/jobs", jresp(status=404, text="nope")),
             ("GET", "careers.acme.com/tile-search-results", jresp(text="<html></html>"))])
        self.assertEqual(jobs, [])
        self.assertTrue(any("tile-search-results" in c[1] for c in fake.calls))


class TestPhenom(AdapterTestCase):
    """HTML-config scraper: reads the `var phApp` object off the landing page for
    the widget endpoint, then pages the /widgets JSON API."""

    LANDING = ('<html><head><script>var phApp = {"widgetApiEndpoint": '
               '"https://careers.acme.com/api/widgets", "locale": "en_CA", '
               '"country": "canada", "pageId": "page1"};</script></head></html>')

    def _job(self, i):
        return {"jobId": f"J{i}", "title": "Software &amp; Data Intern",
                "cityStateCountry": "Toronto, ON, Canada",
                "postedDate": "2026-01-15", "applyUrl": f"https://careers.acme.com/job/J{i}"}

    def test_reads_config_and_pages(self):
        def widgets(url, **kw):
            frm = kw["json"]["from"]
            if frm == 0:
                jobs = [self._job(i) for i in range(100)]
                return jresp(payload={"refineSearch": {"totalHits": 130, "data": {"jobs": jobs}}})
            jobs = [self._job(i) for i in range(100, 130)]
            return jresp(payload={"refineSearch": {"totalHits": 130, "data": {"jobs": jobs}}})

        jobs, fake = self.run_adapter(
            phenom, phenom.fetch_phenom,
            {"url": "https://careers.acme.com/careers"},
            [("GET", "careers.acme.com/careers", jresp(text=self.LANDING)),
             ("POST", "careers.acme.com/api/widgets", widgets)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 130)
        self.assertEqual(jobs[0]["id"], "phenom:careers.acme.com:J0")
        self.assertEqual(jobs[0]["title"], "Software & Data Intern")  # html-unescaped
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 2)


class TestEightfold(AdapterTestCase):
    """PCSX site: pulls _csrf token + API domain from the landing HTML, then pages
    /api/pcsx/search (10 per page)."""

    LANDING = ('<html><head><meta name="_csrf" content="csrf-tok">'
               '<script>{"domain":"acme.com"}</script></head></html>')

    def _pos(self, i):
        return {"id": i, "name": f"Job {i}", "locations": ["Toronto, ON", "Remote"],
                "postedTs": 1704067200, "positionUrl": f"/careers/job/{i}"}

    def test_reads_csrf_domain_and_pages(self):
        def search(url, **kw):
            start = kw["params"]["start"]
            if start == 0:
                return jresp(payload={"data": {"count": 15,
                                               "positions": [self._pos(i) for i in range(10)]}})
            return jresp(payload={"data": {"count": 15,
                                           "positions": [self._pos(i) for i in range(10, 15)]}})

        jobs, fake = self.run_adapter(
            eightfold, eightfold.fetch_eightfold,
            {"url": "https://careers.acme.com/careers"},
            [("GET", "careers.acme.com/api/pcsx/search", search),
             ("GET", "careers.acme.com/careers", jresp(text=self.LANDING))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 15)
        self.assertEqual(jobs[0]["id"], "ef:acme.com:0")
        self.assertEqual(jobs[0]["location"], "Toronto, ON; Remote")
        self.assertEqual(jobs[0]["posted"], "2024-01-01")

    def test_missing_csrf_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_adapter(
                eightfold, eightfold.fetch_eightfold,
                {"url": "https://careers.acme.com/careers"},
                [("GET", "careers.acme.com/careers", jresp(text="<html>no token</html>"))])


class TestRadancy(AdapterTestCase):
    """Server-rendered HTML search page, paged with ?p=N. Covers both templates:
    old (data-title attr, location span inside the <a>) and new (title is the link
    text, location is a sibling span after the </a>)."""

    PAGE1 = (
        '<a data-job-id="J1" data-title="Software Intern &amp; Co-op" href="/job/J1">'
        '<span class="job-location">Toronto, ON</span></a>'
        '<a data-job-id="J2" href="/job/J2">Data Analyst</a>'
        '<span class="job-location">Montreal, QC</span>')

    def test_parses_both_templates_and_stops(self):
        def route(url, **kw):
            return jresp(text=self.PAGE1 if "p=1" in url else "<html>no jobs</html>")

        jobs, fake = self.run_adapter(
            radancy, radancy.fetch_radancy,
            {"url": "https://careers.acme.com/search-jobs?orgIds=123"},
            [("GET", "careers.acme.com/search-jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["id"], "radancy:careers.acme.com:J1")
        self.assertEqual(jobs[0]["title"], "Software Intern & Co-op")  # data-title, unescaped
        self.assertEqual(jobs[0]["location"], "Toronto, ON")
        self.assertEqual(jobs[1]["title"], "Data Analyst")            # link-text template
        self.assertEqual(jobs[1]["location"], "Montreal, QC")         # sibling span
        self.assertGreaterEqual(len([c for c in fake.calls if c[0] == "GET"]), 2)


class TestICIMSClassic(AdapterTestCase):
    """Classic *.icims.com portal: server-rendered job cards over a 'Page N of M'
    paginated iframe view."""

    def _card(self, jid):
        return (
            '<li class="iCIMS_JobCardItem">'
            f'<a href="https://careersen-acme.icims.com/jobs/{jid}/software-intern/job?in_iframe=1">'
            '<h3>Software Intern</h3></a>'
            '<span class="field-label">Job Locations</span> <span > Toronto, ON </span>'
            '<span>Posted Date</span> <span title="6/25/2026 2:33 PM">2 days ago</span>'
            '</li>')

    def test_pages_via_page_count(self):
        def route(url, **kw):
            pr = kw["params"]["pr"]
            if pr == 0:
                return jresp(text="<p>Page 1 of 2</p>" + self._card("12345"))
            return jresp(text=self._card("67890"))

        jobs, fake = self.run_adapter(
            icims, icims.fetch_icims,
            {"url": "https://careersen-acme.icims.com/jobs/search"},
            [("GET", "careersen-acme.icims.com/jobs/search", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)  # Page 1 of 2 -> two fetches
        self.assertEqual(jobs[0]["id"], "icims:acme:12345")  # careersen- prefix stripped
        self.assertEqual(jobs[0]["title"], "Software Intern")
        self.assertEqual(jobs[0]["location"], "Toronto, ON")
        self.assertEqual(jobs[0]["posted"], "2026-06-25")
        self.assertEqual(jobs[0]["url"], "https://careersen-acme.icims.com/jobs/12345/software-intern/job")
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 2)


class TestSuccessFactorsClassic(AdapterTestCase):
    """Classic RMK tile scrape — reached when the modern API page-0 POST fails."""

    TILES = (
        '<li class="job-tile job-id-12345" data-url="/job/Toronto-ON/12345/">'
        '<span class="section-title title">Software Intern</span>'
        '<span class="job-location">Toronto, ON</span></li>')

    def test_falls_back_and_parses_tiles(self):
        jobs, fake = self.run_adapter(
            successfactors, successfactors.fetch_successfactors,
            {"name": "Acme", "url": "https://careers.acme.com/search/"},
            [("GET", "careers.acme.com/search", jresp(text="ok")),          # modern warm-up
             ("POST", "/services/recruiting/v1/jobs", jresp(status=404)),   # not modern
             ("GET", "tile-search-results", jresp(text=self.TILES))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "sf:careers.acme.com:12345")
        self.assertEqual(jobs[0]["title"], "Software Intern")
        self.assertEqual(jobs[0]["location"], "Toronto, ON")
        self.assertTrue(any("tile-search-results" in c[1] for c in fake.calls))


if __name__ == "__main__":
    unittest.main()
