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

import contextlib
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import (ashby, avature, bamboohr, dayforce, eightfold, gem, greenhouse,
                      icims, jazzhr, jobvite, lever, oracle, phenom, radancy,
                      ripplematch, rippling, smartrecruiters, successfactors,
                      ukg, workable, workday, zohorecruit)
from tests import fakehttp
from tests.fakehttp import FakeRequests, jresp

REQUIRED_KEYS = {"id", "title", "location", "posted", "url", "company"}


class AdapterTestCase(unittest.TestCase):
    """Base: patch an adapter module's `requests`, no-op sleep, assert the contract."""

    def run_adapter(self, module, fetch, board, rules, default=None):
        fake = FakeRequests(rules, default=default)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("time.sleep", lambda *a, **k: None))
            # Adapters reach the network through adapters.common's shared `HTTP`
            # session, or a per-board one from `new_session()`; which of the two a
            # module imports depends on whether it needs its own cookie jar, and
            # successfactors still imports `requests` itself for RequestException.
            # Patch whichever names this module actually has, all onto one fake.
            for name, value in (("HTTP", fake), ("new_session", fake.Session),
                                ("requests", fake)):
                if hasattr(module, name):
                    stack.enter_context(mock.patch.object(module, name, value))
            jobs = fetch(board)
        return jobs, fake

    def assert_contract(self, jobs):
        self.assertTrue(jobs, "adapter returned no jobs")
        for j in jobs:
            self.assertEqual(set(j), REQUIRED_KEYS,
                             f"job dict keys {set(j)} != contract {REQUIRED_KEYS}")
            self.assertTrue(j["id"], "job id must be non-empty (it's the dedup key)")


class TestGem(AdapterTestCase):
    def test_normalizes(self):
        payload = [{"data": {
            "oatsExternalJobPostings": {"jobPostings": [{
                "id": "int-1", "extId": "ext-1", "title": "Software Intern",
                "locations": [{"name": "Toronto, ON"}],
            }]},
            "jobBoardExternal": {"teamDisplayName": "Acme Corp"},
        }}]
        jobs, _ = self.run_adapter(
            gem, gem.fetch_gem,
            {"url": "https://jobs.gem.com/acme"},
            [("POST", "jobs.gem.com/api/public/graphql/batch",
              jresp(payload=payload))])
        self.assert_contract(jobs)
        j = jobs[0]
        self.assertEqual(j["id"], "gem:acme:ext-1")
        self.assertEqual(j["title"], "Software Intern")
        self.assertEqual(j["location"], "Toronto, ON")
        self.assertEqual(j["company"], "Acme Corp")
        self.assertEqual(j["url"], "https://jobs.gem.com/acme/ext-1")

    def test_multi_location(self):
        payload = [{"data": {
            "oatsExternalJobPostings": {"jobPostings": [{
                "id": "int-2", "extId": "ext-2", "title": "Designer",
                "locations": [{"name": "NYC"}, {"name": "Remote"}],
            }]},
            "jobBoardExternal": {"teamDisplayName": "Acme"},
        }}]
        jobs, _ = self.run_adapter(
            gem, gem.fetch_gem,
            {"url": "https://jobs.gem.com/acme"},
            [("POST", "jobs.gem.com/api/public/graphql/batch",
              jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["location"], "NYC ; Remote")

    def test_fallback_company(self):
        payload = [{"data": {
            "oatsExternalJobPostings": {"jobPostings": [{
                "id": "int-3", "extId": "ext-3", "title": "PM",
                "locations": [],
            }]},
            "jobBoardExternal": None,
        }}]
        jobs, _ = self.run_adapter(
            gem, gem.fetch_gem,
            {"url": "https://jobs.gem.com/acme"},
            [("POST", "jobs.gem.com/api/public/graphql/batch",
              jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["company"], "acme")
        self.assertEqual(jobs[0]["location"], "")


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

    def test_all_locations(self):
        payload = [{
            "id": "ml-1", "text": "ML Engineer",
            "categories": {
                "location": "Toronto, ON",
                "allLocations": ["Toronto, ON", "Pittsburgh, PA",
                                 "San Francisco, CA"],
            },
            "createdAt": 1720000000000,
            "hostedUrl": "https://jobs.lever.co/acme/ml-1",
        }]
        jobs, _ = self.run_adapter(
            lever, lever.fetch_lever,
            {"url": "https://jobs.lever.co/acme"},
            [("GET", "api.lever.co/v0/postings/acme",
              jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(
            jobs[0]["location"],
            "Toronto, ON ; Pittsburgh, PA ; San Francisco, CA")


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

    def test_secondary_locations(self):
        payload = {"jobs": [{
            "id": "abc", "title": "Intern - SWE", "location": "San Francisco",
            "secondaryLocations": [
                {"location": "Toronto"},
                {"location": "Remote (US)"},
            ],
            "publishedDate": "2026-06-01T00:00:00Z",
            "jobUrl": "https://jobs.ashbyhq.com/acme/abc",
        }]}
        jobs, _ = self.run_adapter(
            ashby, ashby.fetch_ashby,
            {"url": "https://jobs.ashbyhq.com/acme"},
            [("GET", "api.ashbyhq.com/posting-api/job-board/acme", jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["location"], "San Francisco ; Toronto ; Remote (US)")


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


class TestRippleMatch(AdapterTestCase):
    def _job(self, jid, company, role="Engineer"):
        return {"id": jid, "companyName": company, "roleName": role,
                "locations": ["Santa Clara, CA, USA"], "locationType": "Hybrid",
                "postedDate": "2026-06-04T14:13:03.357272",
                "applyUrl": f"https://app.ripplematch.com/v2/public/job/{jid}"}

    def test_pages_filters_by_company_and_stops(self):
        def route(url, **kw):
            page = kw["params"]["page"]
            if page == 1:
                # A leaked other-company row (server filter is a name *search*)
                # must be dropped; has_next drives the second page.
                jobs = [self._job("a1", "Palo Alto Networks"),
                        self._job("x9", "Palo Alto Software")]
                return jresp(payload={"jobs": jobs, "pagination": {
                    "page": 1, "per_page": 20, "total_items": 3, "has_next": True}})
            jobs = [self._job("a2", "Palo Alto Networks")]
            return jresp(payload={"jobs": jobs, "pagination": {
                "page": 2, "per_page": 20, "total_items": 3, "has_next": False}})

        jobs, fake = self.run_adapter(
            ripplematch, ripplematch.fetch_ripplematch,
            {"name": "Palo Alto", "company": "Palo Alto Networks",
             "url": "https://app.ripplematch.com/v2/public/company/palo-alto-networks"},
            [("GET", "app.ripplematch.com/api/public/jobs/unified", route)])
        self.assert_contract(jobs)
        self.assertEqual([j["id"] for j in jobs],
                         ["ripplematch:a1", "ripplematch:a2"])  # x9 filtered out
        self.assertEqual(jobs[0]["url"],
                         "https://app.ripplematch.com/v2/public/job/a1")
        self.assertEqual(jobs[0]["posted"], "2026-06-04")
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 2)

    def test_no_company_raises(self):
        with self.assertRaises(ValueError):
            ripplematch.fetch_ripplematch({"url": "https://app.ripplematch.com/x"})

    def test_refuses_unnarrowed_universe(self):
        # If the company filter silently stops applying, total_items balloons —
        # the adapter must bail loudly, not page millions of rows.
        big = jresp(payload={"jobs": [], "pagination": {
            "page": 1, "total_items": 2262689, "has_next": True}})
        with self.assertRaises(ValueError):
            self.run_adapter(
                ripplematch, ripplematch.fetch_ripplematch,
                {"company": "Acme", "url": "https://app.ripplematch.com/x"},
                [("GET", "app.ripplematch.com/api/public/jobs/unified", big)])


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

    def test_vanity_company_resolved_from_page(self):
        """On a vanity domain the path segment is not the company id, so it is
        resolved from the page and the API is queried with THAT id."""
        page = ('<html><script>widget({"company_code": "Dexterra",'
                '"job_title": "true"})</script></html>')
        posting = {"id": "j1", "name": "Cleaner",
                   "location": {"city": "Calgary", "region": "AB", "country": "CA"},
                   "releasedDate": "2026-02-01T00:00:00Z"}
        jobs, fake = self.run_adapter(
            smartrecruiters, smartrecruiters.fetch_smartrecruiters,
            {"name": "Dexterra Group", "url": "https://dexterra.com/en-ca/careers/find-a-job/"},
            [("GET", "dexterra.com/en-ca/careers/find-a-job/", jresp(text=page)),
             ("GET", "api.smartrecruiters.com/v1/companies/Dexterra/postings",
              jresp(payload={"totalFound": 1, "content": [posting]}))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "smartrecruiters:Dexterra:j1")
        # "en-ca" must never be queried — that's the bug this guards.
        self.assertFalse([u for m, u, _ in fake.calls if "companies/en-ca/" in u])

    def test_on_ats_host_needs_no_page_lookup(self):
        """careers.smartrecruiters.com/<company> is authoritative — don't fetch the page."""
        posting = {"id": "j2", "name": "Engineer",
                   "location": {"city": "Ottawa", "region": "ON", "country": "CA"},
                   "releasedDate": "2026-02-01T00:00:00Z"}
        jobs, fake = self.run_adapter(
            smartrecruiters, smartrecruiters.fetch_smartrecruiters,
            {"name": "General Dynamics", "url": "https://careers.smartrecruiters.com/GDMSI/"},
            [("GET", "api.smartrecruiters.com/v1/companies/GDMSI/postings",
              jresp(payload={"totalFound": 1, "content": [posting]}))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "smartrecruiters:GDMSI:j2")
        self.assertTrue(all("api.smartrecruiters.com" in u for m, u, _ in fake.calls),
                        "on-ATS board must not fetch the careers page")

    def test_stale_pinned_company_self_heals(self):
        """A wrong/stale pinned id returns 200 + zero postings rather than raising,
        so an empty first page re-resolves once from the page and retries."""
        page = '<a href="https://jobs.smartrecruiters.com/EgisGroup/744000141166055">Job</a>'
        posting = {"id": "j3", "name": "Architect",
                   "location": {"city": "Paris", "region": "", "country": "FR"},
                   "releasedDate": "2026-02-02T00:00:00Z"}
        jobs, fake = self.run_adapter(
            smartrecruiters, smartrecruiters.fetch_smartrecruiters,
            {"name": "Egis", "company": "jobs", "url": "https://jobs.egis-group.com/jobs"},
            [("GET", "api.smartrecruiters.com/v1/companies/jobs/postings",
              jresp(payload={"totalFound": 0, "content": []})),
             ("GET", "jobs.egis-group.com/jobs", jresp(text=page)),
             ("GET", "api.smartrecruiters.com/v1/companies/EgisGroup/postings",
              jresp(payload={"totalFound": 1, "content": [posting]}))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "smartrecruiters:EgisGroup:j3")

    def test_extract_smartrecruiters_company(self):
        ex = smartrecruiters.extract_smartrecruiters_company
        self.assertEqual(ex('x widget({"company_code": "Assent", "n": 1})'), "Assent")
        self.assertEqual(ex('<a href="//jobs.smartrecruiters.com/Dexterra/744000140521671">'),
                         "Dexterra")
        self.assertEqual(ex('smartrecruiters.com/my-applications/EgisGroup?dcr_ci=EgisGroup'),
                         "EgisGroup")
        # An asset path must not be mistaken for a company id.
        self.assertIsNone(
            ex('<script src="https://static.smartrecruiters.com/job-widget/1.6.2/'
               'script/smart_widget.js"></script>'))
        self.assertIsNone(ex("<html>nothing here</html>"))


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

    def test_vanity_host_resolved_from_page(self):
        """A vanity domain can't serve the API; the real *.oraclecloud.com host is
        resolved up front from the page HTML, and the API is hit on THAT host."""
        page = ('<html>...var apiHost="eeho.fa.us2.oraclecloud.com";'
                '<a href="//eeho.fa.us2.oraclecloud.com/hcmUI">...</html>')
        reqs = [{"Id": 7, "Title": "Intern", "PrimaryLocation": "Austin, TX",
                 "PostedDate": "2026-02-01"}]
        jobs, fake = self.run_adapter(
            oracle, oracle.fetch_oracle,
            {"name": "Oracle", "url": "https://careers.oracle.com/en/sites/jobsearch/jobs"},
            [("GET", "careers.oracle.com/hcmRestApi", jresp(text="<html>404</html>")),
             ("GET", "eeho.fa.us2.oraclecloud.com/hcmRestApi",
              jresp(payload={"items": [{"TotalJobsCount": 1, "requisitionList": reqs}]})),
             ("GET", "careers.oracle.com/en/sites/jobsearch/jobs", jresp(text=page))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "oracle:eeho.fa.us2.oraclecloud.com:7")
        # Proactive resolve means the vanity /hcmRestApi is never even called.
        api_hosts = [u for m, u, _ in fake.calls if "/hcmRestApi" in u]
        self.assertTrue(api_hosts and all("oraclecloud.com" in u for u in api_hosts),
                        "API must be hit on the resolved oraclecloud host")

    def test_stale_pinned_pod_self_heals(self):
        """A pinned pod that stops serving JSON (re-pointed tenant) is re-resolved
        once from the vanity page, then the retry succeeds on the fresh host."""
        page = '<a href="//new.fa.us2.oraclecloud.com/hcmUI">go</a>'
        reqs = [{"Id": 3, "Title": "Intern", "PrimaryLocation": "Reston, VA",
                 "PostedDate": "2026-02-02"}]
        jobs, fake = self.run_adapter(
            oracle, oracle.fetch_oracle,
            {"name": "Akamai", "host": "old.fa.us2.oraclecloud.com",
             "url": "https://jobs.akamai.com/en/sites/CX_1"},
            [("GET", "old.fa.us2.oraclecloud.com/hcmRestApi", jresp(text="<html>gone</html>")),
             ("GET", "new.fa.us2.oraclecloud.com/hcmRestApi",
              jresp(payload={"items": [{"TotalJobsCount": 1, "requisitionList": reqs}]})),
             ("GET", "jobs.akamai.com/en/sites/CX_1", jresp(text=page))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "oracle:new.fa.us2.oraclecloud.com:3")

    def test_extract_oracle_host(self):
        self.assertEqual(
            oracle.extract_oracle_host('x //fa-extu-saasfaprod1.fa.ocs.oraclecloud.com/y'),
            "fa-extu-saasfaprod1.fa.ocs.oraclecloud.com")
        self.assertIsNone(oracle.extract_oracle_host("<html>no host here</html>"))

    def test_secondary_locations(self):
        reqs = [{"Id": 1, "Title": "SDE", "PostedDate": "2026-03-01",
                 "PrimaryLocation": "United States",
                 "secondaryLocations": [
                     {"Name": "Toronto, ON, Canada"},
                     {"Name": "Vancouver, BC, Canada"},
                 ]}]
        jobs, _ = self.run_adapter(
            oracle, oracle.fetch_oracle,
            {"url": "https://a.fa.us2.oraclecloud.com"
                    "/hcmUI/CandidateExperience/en/sites/CX_1/jobs"},
            [("GET", "a.fa.us2.oraclecloud.com/hcmRestApi",
              jresp(payload={"items": [{"TotalJobsCount": 1,
                                        "requisitionList": reqs}]}))])
        self.assert_contract(jobs)
        self.assertEqual(
            jobs[0]["location"],
            "United States ; Toronto, ON, Canada"
            " ; Vancouver, BC, Canada")


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
        self.assertEqual(jobs[0]["url"], "https://acme.wd5.myworkdayjobs.com/External_Careers/job/0")
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 2)

    def test_fans_out_remaining_pages_in_offset_order(self):
        """Past page 0 the offsets are all computable from `total`, so they are
        issued in parallel — but the returned list must still read in offset order,
        and every page must be requested exactly once."""
        def route(url, **kw):
            offset = kw["json"]["offset"]
            return jresp(payload={"total": 100,
                                  "jobPostings": [self._job(i) for i
                                                  in range(offset, offset + 20)]})

        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers"},
            [("POST", "/wday/cxs/acme/External_Careers/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual([j["id"] for j in jobs], [f"acme:R{i}" for i in range(100)])
        self.assertEqual(sorted(c[2]["json"]["offset"] for c in fake.calls),
                         [0, 20, 40, 60, 80])

    def test_retries_429_instead_of_losing_the_board(self):
        """Workday throttles by pod, and a 429 used to cost the whole board. It is a
        "wait", not a failure — back off and retry."""
        seen = []

        def route(url, **kw):
            seen.append(kw["json"]["offset"])
            if len(seen) == 1:
                return jresp(status=429)
            return jresp(payload={"total": 1, "jobPostings": [self._job(0)]})

        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers"},
            [("POST", "/wday/cxs/acme/External_Careers/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(seen, [0, 0])  # same offset re-requested, nothing consumed

    def test_429_gives_up_eventually(self):
        """A board that only ever 429s must surface the error, not retry forever."""
        with self.assertRaises(RuntimeError):  # FakeResponse.raise_for_status
            self.run_adapter(
                workday, workday.fetch_workday,
                {"url": "https://acme.wd5.myworkdayjobs.com/en-US/Careers"},
                [("POST", "/wday/cxs/", jresp(status=429))])

    def test_retry_after_header_sets_the_wait(self):
        """A numeric Retry-After is what the server asked for, so honour it over the
        default backoff — but a junk value must fall back, not crash."""
        waits = []

        def run(retry_after):
            waits.clear()
            responses = [jresp(status=429, headers={"Retry-After": retry_after}),
                         jresp(payload={"total": 1, "jobPostings": [self._job(0)]})]
            fake = FakeRequests([("POST", "/wday/cxs/", lambda u, **kw: responses.pop(0))])
            with mock.patch.object(workday, "HTTP", fake), \
                 mock.patch("time.sleep", lambda s: waits.append(s)):
                workday.fetch_workday(
                    {"url": "https://acme.wd5.myworkdayjobs.com/en-US/Careers"})

        run("7")
        self.assertEqual(waits, [7.0])
        run("Wed, 21 Oct 2026 07:28:00 GMT")  # HTTP-date form — not parsed
        self.assertEqual(waits, [workday._BACKOFF])

    def test_pod_is_the_pool_key(self):
        """Two tenants on one pod share a rate limit, so they must share a pool —
        and the tenant-less myworkdaysite.com form must not key on its own pod label."""
        self.assertEqual(workday._pod("acme.wd5.myworkdayjobs.com"),
                         workday._pod("other.wd5.myworkdayjobs.com"))
        self.assertNotEqual(workday._pod("acme.wd5.myworkdayjobs.com"),
                            workday._pod("acme.wd3.myworkdayjobs.com"))
        self.assertEqual(workday._pod("wd3.myworkdaysite.com"), "wd3.myworkdaysite.com")

    def test_page_failure_fails_the_board(self):
        """A failure on any page must surface, not silently return a partial board —
        the fan-out has already fetched the later pages by the time it is raised."""
        def route(url, **kw):
            if kw["json"]["offset"] == 40:
                return jresp(status=500)
            return jresp(payload={"total": 100, "jobPostings": [self._job(0)]})

        with self.assertRaises(RuntimeError):  # FakeResponse.raise_for_status
            self.run_adapter(
                workday, workday.fetch_workday,
                {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers"},
                [("POST", "/wday/cxs/acme/External_Careers/jobs", route)])

    def test_empty_first_page_issues_no_fan_out(self):
        """An empty page 0 means nothing to page through, whatever `total` claims."""
        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers"},
            [("POST", "/wday/cxs/acme/External_Careers/jobs",
              jresp(payload={"total": 100, "jobPostings": []}))])
        self.assertEqual(jobs, [])
        self.assertEqual(len(fake.calls), 1)

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

    def _multi_loc_rules(self):
        list_payload = {"total": 1, "jobPostings": [self._job(0, loc="3 Locations")]}
        detail_payload = {"jobPostingInfo": {"location": "Toronto",
                                             "additionalLocations": ["Montreal", "Vancouver"]}}
        return [("POST", "/wday/cxs/acme/External_Careers/jobs", jresp(payload=list_payload)),
                ("GET", "/wday/cxs/acme/External_Careers/job/0", jresp(payload=detail_payload))]

    def test_title_prefilter_skips_detail_fetch(self):
        """Resolving "N Locations" costs a request per posting. A title that can't
        pass jobwatch's filter is dropped whatever its location turns out to be, so
        the adapter must not spend that request — it keeps the placeholder instead."""
        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers",
             "resolve_multi_location": True, "title_ok": lambda t: False},
            self._multi_loc_rules())
        self.assertEqual(jobs[0]["location"], "3 Locations")  # left unresolved
        self.assertEqual([c for c in fake.calls if c[0] == "GET"], [],
                         "no detail fetch may happen for a title that can't match")

    def test_title_prefilter_true_still_resolves(self):
        """The mirror of the above — guards against the prefilter wired backwards."""
        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://acme.wd5.myworkdayjobs.com/en-US/External_Careers",
             "resolve_multi_location": True, "title_ok": lambda t: True},
            self._multi_loc_rules())
        self.assertEqual(jobs[0]["location"], "Toronto, Montreal, Vancouver")
        self.assertEqual(len([c for c in fake.calls if c[0] == "GET"]), 1)

    def test_myworkdaysite_tenant_derivation(self):
        payload = {"total": 1, "jobPostings": [self._job(0)]}
        jobs, _ = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://wd3.myworkdaysite.com/en-US/recruiting/gflenv/Careers"},
            [("POST", "wd3.myworkdaysite.com/wday/cxs/gflenv/Careers/jobs", jresp(payload=payload))])
        self.assertEqual(jobs[0]["id"], "gflenv:R0")  # tenant from recruiting/<tenant>

    def test_underscore_tenant_retried_after_422(self):
        """Tenant "vhr_genband" is served from vhr-genband.wd1… — the hyphenated name
        the hostname yields is rejected, so the adapter retries with underscores."""
        payload = {"total": 1, "jobPostings": [self._job(0)]}
        jobs, fake = self.run_adapter(
            workday, workday.fetch_workday,
            {"url": "https://vhr-genband.wd1.myworkdayjobs.com/en-US/ribboncareers"},
            [("POST", "/wday/cxs/vhr_genband/ribboncareers/jobs", jresp(payload=payload)),
             ("POST", "/wday/cxs/vhr-genband/ribboncareers/jobs", jresp(status=422))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["id"], "vhr_genband:R0")  # id keyed on real tenant
        self.assertEqual([c[1].split("/wday")[1] for c in fake.calls],
                         ["/cxs/vhr-genband/ribboncareers/jobs",
                          "/cxs/vhr_genband/ribboncareers/jobs"])

    def test_underscore_retry_is_one_shot(self):
        """A tenant that 422s under both spellings must surface the error, not loop."""
        with self.assertRaises(RuntimeError):  # FakeResponse.raise_for_status
            self.run_adapter(
                workday, workday.fetch_workday,
                {"url": "https://a-b.wd5.myworkdayjobs.com/en-US/Careers"},
                [("POST", "/wday/cxs/", jresp(status=422))])

    def test_pinned_tenant_is_not_rewritten(self):
        """An explicit tenant is literal — a hyphen in it is the real name."""
        payload = {"total": 1, "jobPostings": [self._job(0)]}
        # The underscore spelling would succeed — so a retry would mask the error.
        with self.assertRaises(RuntimeError):
            self.run_adapter(
                workday, workday.fetch_workday,
                {"url": "https://acme.wd5.myworkdayjobs.com/en-US/Careers",
                 "tenant": "acme-emea"},
                [("POST", "/wday/cxs/acme-emea/Careers/jobs", jresp(status=422)),
                 ("POST", "/wday/cxs/acme_emea/Careers/jobs", jresp(payload=payload))])

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

    def test_transport_failure_past_page0_keeps_jobs(self):
        """Regression: a connection/read failure that outlived the session's retries
        used to propagate out of the paging POST and cost the whole board — the path
        that lost CPKC and CapGemini in a run where both fetched fine alone. It is
        now treated like the past-the-end case: keep what has already been collected."""
        def route(url, **kw):
            page = json.loads(kw["data"])["pageNumber"]
            if page == 0:
                return jresp(payload={"totalJobs": 20,
                                      "jobSearchResult": [self._item(i) for i in range(10)]})
            raise fakehttp.RequestException("Read timed out. (read timeout=20)")

        jobs, _ = self.run_adapter(
            successfactors, successfactors.fetch_successfactors,
            {"name": "Acme", "url": "https://careers.acme.com/search/"},
            [("GET", "careers.acme.com/search", jresp(text="ok")),
             ("POST", "/services/recruiting/v1/jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 10)

    def test_transport_failure_on_page0_falls_back_to_classic(self):
        """Same failure on page 0 can't distinguish "not a modern site" from "the
        network blipped", so it takes the existing fallback rather than raising."""
        def route(url, **kw):
            raise fakehttp.RequestException("Connection reset by peer")

        jobs, fake = self.run_adapter(
            successfactors, successfactors.fetch_successfactors,
            {"name": "Acme", "url": "https://careers.acme.com/search/"},
            [("GET", "careers.acme.com/search", jresp(text="ok")),
             ("POST", "/services/recruiting/v1/jobs", route),
             ("GET", "careers.acme.com/tile-search-results", jresp(text="<html></html>"))])
        self.assertEqual(jobs, [])
        self.assertTrue(any("tile-search-results" in c[1] for c in fake.calls))

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
    """Server-rendered HTML search page, paged with ?p=N. Covers all three
    templates: (A) data-title attr + location span inside the <a>; (B) title is the
    link text, location is a sibling span after the </a>; (C) a job-title heading +
    a result-location span inside the <a> (differently-named location class)."""

    PAGE1 = (
        '<a data-job-id="J1" data-title="Software Intern &amp; Co-op" href="/job/J1">'
        '<span class="job-location">Toronto, ON</span></a>'
        '<a data-job-id="J2" href="/job/J2">Data Analyst</a>'
        '<span class="job-location">Montreal, QC</span>'
        '<a data-job-id="J3" href="/job/J3">'
        '<h2 class="section29__search-results-job-title">Named Account Manager</h2>'
        '<span class="section29__result-location">Calgary, AB</span>'
        '<span class="section29__result-category"><span>Sales</span></span></a>'
        '<a data-job-id="J4" href="/job/J4">Associate Analyst</a>'
        '<div class="bottom-info-wrapper"><ul><li class="search-results-list__job-info '
        'job-location">Toronto, Ontario</li></ul></div>')

    def test_parses_both_templates_and_stops(self):
        def route(url, **kw):
            return jresp(text=self.PAGE1 if "p=1" in url else "<html>no jobs</html>")

        jobs, fake = self.run_adapter(
            radancy, radancy.fetch_radancy,
            {"url": "https://careers.acme.com/search-jobs?orgIds=123"},
            [("GET", "careers.acme.com/search-jobs", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[0]["id"], "radancy:careers.acme.com:J1")
        self.assertEqual(jobs[0]["title"], "Software Intern & Co-op")  # data-title, unescaped
        self.assertEqual(jobs[0]["location"], "Toronto, ON")
        self.assertEqual(jobs[1]["title"], "Data Analyst")            # link-text template
        self.assertEqual(jobs[1]["location"], "Montreal, QC")         # sibling span
        # Template C: heading title + result-location span — no bleed of the
        # location/category text into the title, location parsed from result-location.
        self.assertEqual(jobs[2]["title"], "Named Account Manager")
        self.assertEqual(jobs[2]["location"], "Calgary, AB")
        # Template D: link-text title + location in a sibling <li> (not <span>),
        # sitting >300 chars after </a> — closes on any tag, bounded by next anchor.
        self.assertEqual(jobs[3]["title"], "Associate Analyst")
        self.assertEqual(jobs[3]["location"], "Toronto, Ontario")
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


class TestJazzHR(AdapterTestCase):
    """HTML-scraped JazzHR board at <tenant>.applytojob.com/apply/jobs."""

    PAGE = (
        '<tr id="row_job_20260710175648_AAABBBCCC" class="resumator_even_row">'
        '<td><a class="job_title_link" href="/apply/jobs/details/hKQA66W5Ad?&">'
        'Software Intern &amp; Co-op</a>'
        '<br /><span class="resumator_department">Engineering</span></td>'
        '<td>\n\t\t\t\t\tToronto, ON, Canada\t\t\t\t\t</td>'
        '</tr>'
        '<tr id="row_job_20260601120000_DDDEEEFFF" class="resumator_odd_row">'
        '<td><a class="job_title_link" href="/apply/jobs/details/XcUOPF2PGf?&">'
        'Data Co-op</a>'
        '<br /><span class="resumator_department">Analytics</span></td>'
        '<td>\n\t\t\t\t\tRemote\t\t\t\t\t</td>'
        '</tr>'
    )

    def test_normalizes_and_parses_date(self):
        jobs, _ = self.run_adapter(
            jazzhr, jazzhr.fetch_jazzhr,
            {"name": "Acme", "url": "https://acme.applytojob.com/apply"},
            [("GET", "acme.applytojob.com/apply/jobs", jresp(text=self.PAGE))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["id"], "jazzhr:acme:hKQA66W5Ad")
        self.assertEqual(j["title"], "Software Intern & Co-op")
        self.assertEqual(j["location"], "Toronto, ON, Canada")
        self.assertEqual(j["posted"], "2026-07-10")
        self.assertEqual(j["url"],
                         "https://acme.applytojob.com/apply/jobs/details/hKQA66W5Ad")
        self.assertEqual(j["company"], "Acme")
        self.assertEqual(jobs[1]["id"], "jazzhr:acme:XcUOPF2PGf")
        self.assertEqual(jobs[1]["location"], "Remote")
        self.assertEqual(jobs[1]["posted"], "2026-06-01")


class TestAvature(AdapterTestCase):
    """HTML-scraped Avature portal — article template (city/state/country and
    plain-text location variants) and table template, with offset pagination."""

    # Article template: page 1 uses nested city/state/country spans;
    # page 2 uses plain-text location and a different ID span name.
    ART_P1 = (
        '<article class="article article--result 1" id="article--1">'
        '<h3 class="article__header__text__title title--h3">'
        '<a class="link" href="https://jobs.acme.com/en_US/careers/JobDetail/12345">'
        'Software Intern &amp; Co-op'
        '</a></h3>'
        '<span class="list-item-location">'
        '<span class="list-item-jobCity">Toronto</span>'
        '<span class="separator">, </span>'
        '<span class="list-item-jobState">Ontario</span>'
        '<span class="separator">, </span>'
        '<span class="list-item-jobCountry">Canada</span>'
        '</span>'
        '<span class="list-item-jobId">Job ID: 12345</span>'
        '</article>'
        '<li class="list-controls__pagination__item paginationNextLink">'
        '<a href="https://jobs.acme.com/en_US/careers/SearchJobs/'
        '?folderRecordsPerPage=6&amp;folderOffset=6">Next &gt;&gt;</a>'
        '</li>'
    )

    ART_P2 = (
        '<article class="article article--result article--non-toggle" id="article--1">'
        '<h3 class="article__header__text__title title--04">'
        '<a class="link link_result" href="https://jobs.acme.com/en_US/careers/JobDetail/67890">'
        'Data Intern'
        '</a></h3>'
        '<span class="list-item-location">Remote, Canada</span>'
        '<span class="list-item-id">Role ID 67890</span>'
        '</article>'
    )

    # Table template: two rows with data-map="job-detail-link".
    TABLE_PAGE = (
        '<tr>'
        '<th scope="row">'
        '<a class="link fw--500" data-map="job-detail-link" '
        'href="https://careers.bank.com/en_CA/careers/JobDetail/Finance-Intern/11111">'
        'Finance Intern'
        '</a>'
        '</th>'
        '<td>\n    Montreal, Quebec\n</td>'
        '</tr>'
        '<tr>'
        '<th scope="row">'
        '<a class="link fw--500" data-map="job-detail-link" '
        'href="https://careers.bank.com/en_CA/careers/JobDetail/Data-Analyst/22222">'
        'Data Analyst Intern'
        '</a>'
        '</th>'
        '<td>Toronto, Ontario</td>'
        '</tr>'
    )

    def test_article_template_paginates(self):
        def route(url, **kw):
            return jresp(text=self.ART_P1 if "folderOffset" not in url else self.ART_P2)

        jobs, fake = self.run_adapter(
            avature, avature.fetch_avature,
            {"name": "Acme", "url": "https://jobs.acme.com/en_US/careers/SearchJobs/"},
            [("GET", "jobs.acme.com", route)])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)

        j0 = jobs[0]
        self.assertEqual(j0["id"], "avature:jobs.acme.com:12345")
        self.assertEqual(j0["title"], "Software Intern & Co-op")
        self.assertEqual(j0["location"], "Toronto, Ontario, Canada")
        self.assertEqual(j0["posted"], "")
        self.assertEqual(j0["url"], "https://jobs.acme.com/en_US/careers/JobDetail/12345")
        self.assertEqual(j0["company"], "Acme")

        j1 = jobs[1]
        self.assertEqual(j1["id"], "avature:jobs.acme.com:67890")
        self.assertEqual(j1["title"], "Data Intern")
        self.assertEqual(j1["location"], "Remote, Canada")
        self.assertEqual(j1["url"], "https://jobs.acme.com/en_US/careers/JobDetail/67890")
        self.assertGreaterEqual(
            len([c for c in fake.calls if c[0] == "GET"]), 2,
            "should have fetched at least 2 pages")

    def test_table_template(self):
        jobs, _ = self.run_adapter(
            avature, avature.fetch_avature,
            {"name": "Bank", "url": "https://careers.bank.com/en_CA/careers/searchjobs/"},
            [("GET", "careers.bank.com", jresp(text=self.TABLE_PAGE))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["id"], "avature:careers.bank.com:11111")
        self.assertEqual(jobs[0]["title"], "Finance Intern")
        self.assertEqual(jobs[0]["location"], "Montreal, Quebec")
        self.assertEqual(jobs[0]["company"], "Bank")
        self.assertEqual(jobs[1]["id"], "avature:careers.bank.com:22222")
        self.assertEqual(jobs[1]["location"], "Toronto, Ontario")


class TestJobvite(AdapterTestCase):
    """HTML-scraped Jobvite board at jobs.jobvite.com/<slug>."""

    PAGE = (
        '<table class="jv-job-list"><thead><tr>'
        '<th scope="col" class="jv-cws-sr-only">Job listing</th>'
        '<th scope="col" class="jv-cws-sr-only">Job location</th>'
        '</tr></thead><tbody>'
        '<tr>'
        '<td class="jv-job-list-name">'
        '<a href="/acme/job/oLwqAfw1">Software Intern &amp; Co-op</a>'
        '</td>'
        '<td class="jv-job-list-location">'
        '\n        \n            Toronto,\n            ON\n        \n'
        '</td>'
        '</tr>'
        '<tr>'
        '<td class="jv-job-list-name">'
        '<a href="/acme/job/oUvqAfw9">Data Analyst</a>'
        '</td>'
        '<td class="jv-job-list-location">'
        '\n        \n            Vancouver,\n            British Columbia\n        \n'
        '</td>'
        '</tr>'
        '</tbody></table>'
    )

    def test_normalizes(self):
        jobs, _ = self.run_adapter(
            jobvite, jobvite.fetch_jobvite,
            {"name": "Acme", "url": "https://jobs.jobvite.com/acme"},
            [("GET", "jobs.jobvite.com/acme", jresp(text=self.PAGE))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["id"], "jobvite:acme:oLwqAfw1")
        self.assertEqual(j["title"], "Software Intern & Co-op")
        self.assertEqual(j["location"], "Toronto, ON")
        self.assertEqual(j["posted"], "")
        self.assertEqual(j["url"],
                         "https://jobs.jobvite.com/acme/job/oLwqAfw1")
        self.assertEqual(j["company"], "Acme")
        self.assertEqual(jobs[1]["id"], "jobvite:acme:oUvqAfw9")
        self.assertEqual(jobs[1]["title"], "Data Analyst")
        self.assertEqual(jobs[1]["location"], "Vancouver, British Columbia")

    def test_empty_board(self):
        jobs, _ = self.run_adapter(
            jobvite, jobvite.fetch_jobvite,
            {"name": "Empty", "url": "https://jobs.jobvite.com/empty"},
            [("GET", "jobs.jobvite.com/empty",
              jresp(text='<div class="jv-wrapper">No jobs</div>'))])
        self.assertEqual(jobs, [])


class TestWorkable(AdapterTestCase):
    WIDGET_PAYLOAD = {
        "name": "Acme Corp",
        "jobs": [
            {
                "title": "  Software Intern  ",
                "shortcode": "ABC123DEF4",
                "city": "Toronto",
                "state": "Ontario",
                "country": "Canada",
                "published_on": "2026-07-01",
                "url": "https://apply.workable.com/j/ABC123DEF4",
                "shortlink": "https://apply.workable.com/j/ABC123DEF4",
            },
            {
                "title": "Data Analyst",
                "shortcode": "XYZ789GH01",
                "city": "Vancouver",
                "state": "British Columbia",
                "country": "Canada",
                "published_on": "2026-06-15",
                "url": "https://apply.workable.com/j/XYZ789GH01",
                "shortlink": "https://apply.workable.com/j/XYZ789GH01",
            },
        ],
    }

    def test_normalizes(self):
        jobs, _ = self.run_adapter(
            workable, workable.fetch_workable,
            {"name": "Acme", "url": "https://apply.workable.com/acme-corp"},
            [("GET", "apply.workable.com/api/v1/widget/accounts/acme-corp",
              jresp(payload=self.WIDGET_PAYLOAD))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["id"], "workable:acme-corp:ABC123DEF4")
        self.assertEqual(j["title"], "Software Intern")
        self.assertEqual(j["location"], "Toronto, Ontario, Canada")
        self.assertEqual(j["posted"], "2026-07-01")
        self.assertEqual(j["url"], "https://apply.workable.com/j/ABC123DEF4")
        self.assertEqual(j["company"], "Acme")

    def test_company_from_api(self):
        jobs, _ = self.run_adapter(
            workable, workable.fetch_workable,
            {"url": "https://apply.workable.com/acme-corp"},
            [("GET", "apply.workable.com/api/v1/widget/accounts/acme-corp",
              jresp(payload=self.WIDGET_PAYLOAD))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["company"], "Acme Corp")

    def test_empty_board(self):
        jobs, _ = self.run_adapter(
            workable, workable.fetch_workable,
            {"name": "Empty", "url": "https://apply.workable.com/empty"},
            [("GET", "apply.workable.com/api/v1/widget/accounts/empty",
              jresp(payload={"name": "Empty", "jobs": []}))])
        self.assertEqual(jobs, [])

    def test_missing_location_fields(self):
        payload = {"name": "X", "jobs": [{
            "title": "Remote Role", "shortcode": "REM001",
            "city": "", "state": "", "country": "",
            "published_on": "", "url": "https://apply.workable.com/j/REM001",
        }]}
        jobs, _ = self.run_adapter(
            workable, workable.fetch_workable,
            {"url": "https://apply.workable.com/x"},
            [("GET", "apply.workable.com/api/v1/widget/accounts/x",
              jresp(payload=payload))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["location"], "")
        self.assertEqual(jobs[0]["posted"], "")


class TestZohoRecruit(AdapterTestCase):
    def _page_html(self, jobs_json, meta_json=None):
        import html as _html
        jobs_esc = _html.escape(json.dumps(jobs_json), quote=True)
        meta_esc = ""
        if meta_json is not None:
            meta_esc = '<input type="hidden" id="meta" value="' + _html.escape(json.dumps(meta_json), quote=True) + '">'
        return (
            '<html><body>'
            '<input type="hidden" id="jobs" value="' + jobs_esc + '">'
            + meta_esc +
            '</body></html>'
        )

    def test_normalizes(self):
        jobs_data = [
            {"id": "123456", "Posting_Title": "  ML Intern  ",
             "City": "Toronto", "State": "Ontario", "Country": "Canada",
             "Date_Opened": "2026-07-01", "Remote_Job": False},
            {"id": "789012", "Posting_Title": "Data Analyst",
             "City": "Vancouver", "State": "BC", "Country": "Canada",
             "Date_Opened": "2026-06-15", "Remote_Job": False},
        ]
        meta_data = {"org_info": {"company_name": "Acme Corp"}, "page_name": "Careers"}
        html_text = self._page_html(jobs_data, meta_data)
        jobs, _ = self.run_adapter(
            zohorecruit, zohorecruit.fetch_zohorecruit,
            {"url": "https://acme.zohorecruit.com/jobs/Careers"},
            [("GET", "acme.zohorecruit.com/jobs/Careers", jresp(text=html_text))])
        self.assert_contract(jobs)
        self.assertEqual(len(jobs), 2)
        j = jobs[0]
        self.assertEqual(j["id"], "zohorecruit:123456")
        self.assertEqual(j["title"], "ML Intern")
        self.assertEqual(j["location"], "Toronto, Ontario, Canada")
        self.assertEqual(j["posted"], "2026-07-01")
        self.assertIn("123456", j["url"])
        self.assertEqual(j["company"], "Acme Corp")

    def test_company_from_board_name(self):
        jobs_data = [{"id": "1", "Posting_Title": "Dev", "City": "", "State": "", "Country": ""}]
        html_text = self._page_html(jobs_data)
        jobs, _ = self.run_adapter(
            zohorecruit, zohorecruit.fetch_zohorecruit,
            {"name": "My Company", "url": "https://x.zohorecruit.com/jobs/Careers"},
            [("GET", "x.zohorecruit.com/jobs/Careers", jresp(text=html_text))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["company"], "My Company")

    def test_empty_board(self):
        html_text = self._page_html([])
        jobs, _ = self.run_adapter(
            zohorecruit, zohorecruit.fetch_zohorecruit,
            {"name": "Empty", "url": "https://empty.zohorecruit.com/jobs/Careers"},
            [("GET", "empty.zohorecruit.com/jobs/Careers", jresp(text=html_text))])
        self.assertEqual(jobs, [])

    def test_missing_fields(self):
        jobs_data = [{"id": "99", "Job_Opening_Name": "Remote Role",
                      "City": "", "State": "", "Country": ""}]
        meta_data = {"org_info": {"company_name": "X"}}
        html_text = self._page_html(jobs_data, meta_data)
        jobs, _ = self.run_adapter(
            zohorecruit, zohorecruit.fetch_zohorecruit,
            {"url": "https://x.zohorecruit.ca/jobs/Careers"},
            [("GET", "x.zohorecruit.ca/jobs/Careers", jresp(text=html_text))])
        self.assert_contract(jobs)
        self.assertEqual(jobs[0]["title"], "Remote Role")
        self.assertEqual(jobs[0]["location"], "")
        self.assertEqual(jobs[0]["posted"], "")

    def test_no_jobs_input_returns_empty(self):
        html_text = "<html><body>No jobs here</body></html>"
        jobs, _ = self.run_adapter(
            zohorecruit, zohorecruit.fetch_zohorecruit,
            {"name": "X", "url": "https://x.zohorecruit.com/jobs/Careers"},
            [("GET", "x.zohorecruit.com/jobs/Careers", jresp(text=html_text))])
        self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()
