"""Characterization tests for probe()'s detection pipeline, with HTTP mocked.

Pins each of the four detection paths (host match, embedded HTML signature, guessed
slug, recognized-but-unsupported) plus the no-match case, and the helper edge cases
(slug from path, generic-word stoplist). Also asserts every type probe can
detect has an adapter registered.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe
from adapters import ADAPTERS
from tests.fakehttp import FakeSession, page, api


def run_probe(url, rules, default=None):
    """Run probe(url) with requests.Session swapped for a scripted FakeSession."""
    sess = FakeSession(rules, default=default)
    with mock.patch.object(probe.requests, "Session", return_value=sess):
        return probe.probe(url)


class TestHostMatch(unittest.TestCase):
    def test_workday_host_high_confidence(self):
        res = run_probe("https://acme.wd1.myworkdayjobs.com/en-US/External",
                        [("myworkdayjobs.com", page("<html>jobs</html>"))])
        self.assertEqual(res.type, "workday")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "workday")

    def test_successfactors_host_high_confidence(self):
        # SAP-hosted RMK career sites live on *.sapsf.com — a host match.
        res = run_probe("https://career17.sapsf.com/career?company=acme&site=xyz",
                        [("career17.sapsf.com", page("<html>jobs</html>"))])
        self.assertEqual(res.type, "successfactors")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "successfactors")

    def test_ripplematch_host_pins_company_name_from_title(self):
        # app.ripplematch.com is a host match; the adapter filters its API by the
        # exact display name, so probe must pin it (from <title>) into `company`.
        # Slug ('pan') deliberately differs from the display name so this only
        # passes if the <title> regex fires — the slug-titleize fallback would
        # yield "Pan", not the exact name the jobs API needs.
        title = ("<title>Jobs, Internships &amp; Careers - Palo Alto Networks "
                 "| RippleMatch</title>")
        res = run_probe(
            "https://app.ripplematch.com/v2/public/company/pan",
            [("app.ripplematch.com", page(f"<html><head>{title}</head></html>"))])
        self.assertEqual(res.type, "ripplematch")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["company"], "Palo Alto Networks")

    def test_jobvite_host_high_confidence(self):
        html = '<table class="jv-job-list"><tr><td class="jv-job-list-name">' \
               '<a href="/acme/job/oX1">Eng</a></td></tr></table>'
        res = run_probe("https://jobs.jobvite.com/acme",
                        [("jobs.jobvite.com", page(html))])
        self.assertEqual(res.type, "jobvite")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "jobvite")

    def test_gem_host_high_confidence(self):
        html = '<html><title>Acme Careers</title></html>'
        res = run_probe("https://jobs.gem.com/acme",
                        [("jobs.gem.com", page(html))])
        self.assertEqual(res.type, "gem")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "gem")

    def test_workable_host_high_confidence(self):
        html = '<html><title>Acme - Careers</title></html>'
        res = run_probe("https://apply.workable.com/acme",
                        [("apply.workable.com", page(html))])
        self.assertEqual(res.type, "workable")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "workable")

    def test_zohorecruit_host_high_confidence(self):
        html = '<html><input id="jobs" value="[]"></html>'
        res = run_probe("https://acme.zohorecruit.com/jobs/Careers",
                        [("acme.zohorecruit.com", page(html))])
        self.assertEqual(res.type, "zohorecruit")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.config["type"], "zohorecruit")

    def test_zohorecruit_ca_host(self):
        html = '<html><input id="jobs" value="[]"></html>'
        res = run_probe("https://acme.zohorecruit.ca/jobs/Careers",
                        [("acme.zohorecruit.ca", page(html))])
        self.assertEqual(res.type, "zohorecruit")
        self.assertEqual(res.confidence, "high")

    def test_yello_host_high_confidence(self):
        res = run_probe("https://eyglobal.yello.co/job_boards/c1riT--B2O",
                        [("eyglobal.yello.co", page("<html>jobs</html>"))])
        self.assertEqual(res.type, "yello")
        self.assertEqual(res.confidence, "high")

    def test_greenhouse_host_reads_slug_from_path(self):
        res = run_probe(
            "https://job-boards.greenhouse.io/acmeco",
            [("job-boards.greenhouse.io/acmeco", page("<html></html>")),
             ("boards-api.greenhouse.io/v1/boards/acmeco/jobs", api({"jobs": [1, 2, 3]})),
             ("boards-api.greenhouse.io/v1/boards/acmeco", api({"name": "AcmeCo"}))])
        self.assertEqual(res.type, "greenhouse")
        self.assertEqual(res.slug, "acmeco")
        self.assertEqual(res.job_count, 3)


class TestEmbeddedSignature(unittest.TestCase):
    def test_whitelabeled_greenhouse_confirmed(self):
        html = '<a href="https://job-boards.greenhouse.io/hiddenco">Jobs</a>'
        res = run_probe(
            "https://careers.example-corp.com",
            [("careers.example-corp.com", page(html)),
             ("boards-api.greenhouse.io/v1/boards/hiddenco/jobs", api({"jobs": [1]})),
             ("boards-api.greenhouse.io/v1/boards/hiddenco", api({"name": "HiddenCo"}))])
        self.assertEqual(res.type, "greenhouse")
        self.assertEqual(res.confidence, "medium")
        self.assertEqual(res.slug, "hiddenco")

    def test_greenhouse_signature_but_empty_board_keeps_looking(self):
        # "greenhouse gas" ESG copy with no real board -> not a greenhouse hit
        html = "We reduce greenhouse.io/embed emissions"  # signature-ish, no jobs
        res = run_probe(
            "https://careers.example-corp.com",
            [("careers.example-corp.com", page(html))],
            default=api({}, status=404))  # all confirmer calls fail
        self.assertNotEqual(res.type, "greenhouse")

    def test_successfactors_signature_supported(self):
        # White-labeled RMK/CSB career sites embed successfactors.com CDN refs. SF is
        # a supported type (has an adapter) detected by HTML signature, not by an
        # active API confirmer — so it must resolve to type 'successfactors', not the
        # recognized-but-unsupported path it used to fall into.
        html = '<script src="https://performancemanager8.successfactors.com/x.js"></script>'
        res = run_probe("https://careers.deloitte.ca/search/",
                        [("careers.deloitte.ca", page(html))],
                        default=api({}, status=404))
        self.assertEqual(res.type, "successfactors")
        self.assertIsNone(res.other)
        self.assertEqual(res.config["type"], "successfactors")
        self.assertIn("successfactors", ADAPTERS)

    def test_phenom_signature_no_confirmer(self):
        html = "<script>var phApp = {};</script>"
        res = run_probe("https://jobs.example-corp.com/careers",
                        [("jobs.example-corp.com", page(html))])
        self.assertEqual(res.type, "phenom")
        self.assertEqual(res.confidence, "medium")

    def test_oracle_vanity_pins_real_api_host(self):
        # A vanity CE domain embeds the real *.oraclecloud.com host — probe must pin
        # it into the entry's `host` so the board can actually fetch (the vanity host
        # can't serve /hcmRestApi).
        html = '<a href="//eeho.fa.us2.oraclecloud.com/hcmUI">apply</a>'
        res = run_probe("https://careers.oracle.com/en/sites/jobsearch/jobs",
                        [("careers.oracle.com", page(html))])
        self.assertEqual(res.type, "oracle")
        self.assertEqual(res.config["host"], "eeho.fa.us2.oraclecloud.com")

    def test_oracle_on_ats_host_needs_no_pin(self):
        # Already on the real oraclecloud pod — no `host` override needed.
        html = "<html>/hcmUI/CandidateExperience</html>"
        res = run_probe(
            "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
            [("acme.fa.us2.oraclecloud.com", page(html))])
        self.assertEqual(res.type, "oracle")
        self.assertNotIn("host", res.config)

    def test_smartrecruiters_vanity_pins_real_company(self):
        # A vanity careers page's first path segment is meaningless ("company") —
        # probe must pin the company id embedded in the page instead, or the board
        # is added silently broken (the API answers 200 + zero jobs for a bad id).
        html = ('<script src="https://static.smartrecruiters.com/job-widget/1.6.2/'
                'script/smart_widget.js"></script>'
                '<script>widget({"company_code": "Assent"})</script>')
        res = run_probe("https://www.assent.com/company/careers/search/",
                        [("www.assent.com", page(html))])
        self.assertEqual(res.type, "smartrecruiters")
        self.assertEqual(res.config["company"], "Assent")

    def test_jobvite_signature_via_powered_by_footer(self):
        html = '<a class="jv-powered-by" href="https://www.jobvite.com">' \
               '<span>Powered by Jobvite</span></a>'
        res = run_probe("https://careers.acme.com/jobs",
                        [("careers.acme.com", page(html))])
        self.assertEqual(res.type, "jobvite")
        self.assertEqual(res.confidence, "medium")

    def test_smartrecruiters_on_ats_host_uses_path_segment(self):
        # On careers.smartrecruiters.com the first path segment IS the company id.
        html = "<html>smartrecruiters.com</html>"
        res = run_probe("https://careers.smartrecruiters.com/AECOM2",
                        [("careers.smartrecruiters.com", page(html))])
        self.assertEqual(res.type, "smartrecruiters")
        self.assertEqual(res.config["company"], "AECOM2")


class TestGuessedSlug(unittest.TestCase):
    def test_guess_from_domain_label(self):
        # no signature in page; host label 'glider' guessed against lever -> non-empty
        res = run_probe(
            "https://careers.glider.com/openings",
            [("careers.glider.com", page("<html><title>Careers</title></html>")),
             ("api.lever.co/v0/postings/glider", api([{"id": 1}, {"id": 2}]))],
            default=api({}, status=404))
        self.assertEqual(res.type, "lever")
        self.assertEqual(res.confidence, "low")
        self.assertEqual(res.slug, "glider")

    def test_empty_guessed_board_is_no_evidence(self):
        res = run_probe(
            "https://careers.glider.com/openings",
            [("careers.glider.com", page("<html></html>")),
             ("api.lever.co/v0/postings/glider", api([]))],  # empty -> rejected
            default=api({}, status=404))
        self.assertIsNone(res.type)


class TestUnsupportedAndUnknown(unittest.TestCase):
    def test_recognized_unsupported(self):
        html = '<script src="https://breezy.hr/widget.js"></script>'
        res = run_probe("https://careers.example-corp.com",
                        [("careers.example-corp.com", page(html))],
                        default=api({}, status=404))
        self.assertIsNone(res.type)
        self.assertEqual(res.other, "Breezy HR")

    def test_no_signature_unknown(self):
        res = run_probe("https://careers.nondescript.com",
                        [("careers.nondescript.com", page("<html>nothing</html>"))],
                        default=api({}, status=404))
        self.assertIsNone(res.type)
        self.assertIsNone(res.other)


class TestConfigEntry(unittest.TestCase):
    """The full config entry probe emits, key order included (_format_entry and
    config.json's 2-line style depend on it). One case per per-type branch."""

    def entry(self, url, rules, default=None):
        return list(run_probe(url, rules, default=default).config.items())

    def test_icims_name_strips_careers_prefix_from_host(self):
        url = "https://careers-acme.icims.com/jobs/search"
        self.assertEqual(self.entry(url, [("icims.com", page("<html></html>"))]),
                         [("name", "Acme"), ("type", "icims"), ("url", url)])

    def test_dayforce_name_from_first_path_segment_after_locale(self):
        url = "https://jobs.dayforcehcm.com/en-US/acme-corp/CANDIDATEPORTAL"
        self.assertEqual(self.entry(url, [("dayforcehcm.com", page("<html></html>"))]),
                         [("name", "Acme Corp"), ("type", "dayforce"), ("url", url)])

    def test_jobvite_on_ats_name_from_first_path_segment(self):
        url = "https://jobs.jobvite.com/acme/jobs"
        self.assertEqual(self.entry(url, [("jobs.jobvite.com", page("<html></html>"))]),
                         [("name", "Acme"), ("type", "jobvite"), ("url", url)])

    def test_jobvite_off_ats_name_from_host_labels(self):
        # Type-specific name rules only apply on the ATS host — here the path
        # segment is "jobs", which must not become the name.
        url = "https://careers.acme.com/jobs"
        html = '<span>Powered by Jobvite</span>'
        self.assertEqual(self.entry(url, [("careers.acme.com", page(html))]),
                         [("name", "Acme"), ("type", "jobvite"), ("url", url)])

    def test_gem_name_from_first_path_segment(self):
        url = "https://jobs.gem.com/acme-co"
        self.assertEqual(self.entry(url, [("jobs.gem.com", page("<html></html>"))]),
                         [("name", "Acme Co"), ("type", "gem"), ("url", url)])

    def test_workday_on_ats_default_name_from_first_host_label(self):
        url = "https://acme.wd1.myworkdayjobs.com/en-US/External"
        self.assertEqual(self.entry(url, [("myworkdayjobs.com", page("<html></html>"))]),
                         [("name", "Acme"), ("type", "workday"), ("url", url)])

    def test_greenhouse_on_ats_uses_api_name_and_pins_no_token(self):
        url = "https://job-boards.greenhouse.io/acmeco"
        rules = [("job-boards.greenhouse.io/acmeco", page("<html></html>")),
                 ("boards-api.greenhouse.io/v1/boards/acmeco/jobs", api({"jobs": [1]})),
                 ("boards-api.greenhouse.io/v1/boards/acmeco", api({"name": "AcmeCo"}))]
        self.assertEqual(self.entry(url, rules),
                         [("name", "AcmeCo"), ("type", "greenhouse"), ("url", url)])

    def test_greenhouse_on_ats_name_falls_back_to_slug(self):
        url = "https://job-boards.greenhouse.io/acme-co"
        rules = [("job-boards.greenhouse.io/acme-co", page("<html></html>")),
                 ("boards-api.greenhouse.io/v1/boards/acme-co/jobs", api({"jobs": [1]}))]
        self.assertEqual(self.entry(url, rules, default=api({}, status=404)),
                         [("name", "Acme Co"), ("type", "greenhouse"), ("url", url)])

    def test_greenhouse_whitelabel_pins_token(self):
        url = "https://careers.example-corp.com"
        rules = [("careers.example-corp.com",
                  page('<a href="https://job-boards.greenhouse.io/hiddenco">Jobs</a>')),
                 ("boards-api.greenhouse.io/v1/boards/hiddenco/jobs", api({"jobs": [1]})),
                 ("boards-api.greenhouse.io/v1/boards/hiddenco", api({"name": "HiddenCo"}))]
        self.assertEqual(self.entry(url, rules),
                         [("name", "HiddenCo"), ("type", "greenhouse"),
                          ("token", "hiddenco"), ("url", url)])

    def test_lever_whitelabel_pins_company(self):
        url = "https://careers.example-corp.com"
        rules = [("careers.example-corp.com",
                  page('<a href="https://jobs.lever.co/hiddenco">Jobs</a>')),
                 ("api.lever.co/v0/postings/hiddenco", api([{"id": 1}]))]
        self.assertEqual(self.entry(url, rules),
                         [("name", "Example Corp"), ("type", "lever"),
                          ("company", "hiddenco"), ("url", url)])

    def test_ashby_whitelabel_pins_board(self):
        url = "https://careers.example-corp.com"
        rules = [("careers.example-corp.com",
                  page('<a href="https://jobs.ashbyhq.com/hiddenco">Jobs</a>')),
                 ("api.ashbyhq.com/posting-api/job-board/hiddenco", api({"jobs": [1]}))]
        self.assertEqual(self.entry(url, rules),
                         [("name", "Example Corp"), ("type", "ashby"),
                          ("board", "hiddenco"), ("url", url)])

    def test_guessed_lever_slug_is_pinned(self):
        url = "https://careers.glider.com/openings"
        rules = [("careers.glider.com", page("<html><title>Careers</title></html>")),
                 ("api.lever.co/v0/postings/glider", api([{"id": 1}]))]
        self.assertEqual(self.entry(url, rules, default=api({}, status=404)),
                         [("name", "Glider"), ("type", "lever"),
                          ("company", "glider"), ("url", url)])

    def test_ripplematch_pins_title_name_as_name_and_company(self):
        url = "https://app.ripplematch.com/v2/public/company/pan"
        title = "<title>Careers - Palo Alto Networks | RippleMatch</title>"
        self.assertEqual(self.entry(url, [("app.ripplematch.com", page(title))]),
                         [("name", "Palo Alto Networks"), ("type", "ripplematch"),
                          ("company", "Palo Alto Networks"), ("url", url)])

    def test_ripplematch_without_title_falls_back_to_path_slug(self):
        url = "https://app.ripplematch.com/v2/public/company/palo-alto"
        self.assertEqual(self.entry(url, [("app.ripplematch.com", page("<html></html>"))]),
                         [("name", "Palo Alto"), ("type", "ripplematch"),
                          ("company", "Palo Alto"), ("url", url)])

    def test_smartrecruiters_on_ats_pins_path_company(self):
        # Name is the company id verbatim, not the host label "Careers".
        url = "https://careers.smartrecruiters.com/AECOM2"
        self.assertEqual(
            self.entry(url, [("careers.smartrecruiters.com", page("<html></html>"))]),
            [("name", "AECOM2"), ("type", "smartrecruiters"),
             ("company", "AECOM2"), ("url", url)])

    def test_smartrecruiters_jobs_host_keeps_camelcase_id_as_name(self):
        url = "https://jobs.smartrecruiters.com/WesternDigital"
        self.assertEqual(
            self.entry(url, [("jobs.smartrecruiters.com", page("<html></html>"))]),
            [("name", "WesternDigital"), ("type", "smartrecruiters"),
             ("company", "WesternDigital"), ("url", url)])

    def test_smartrecruiters_vanity_pins_embedded_company(self):
        url = "https://www.assent.com/company/careers/search/"
        html = ('<script src="https://static.smartrecruiters.com/w.js"></script>'
                '<script>widget({"company_code": "Assent"})</script>')
        self.assertEqual(self.entry(url, [("www.assent.com", page(html))]),
                         [("name", "Assent"), ("type", "smartrecruiters"),
                          ("company", "Assent"), ("url", url)])

    def test_smartrecruiters_vanity_without_company_pins_nothing(self):
        url = "https://www.assent.com/careers/"
        html = '<script src="https://static.smartrecruiters.com/w.js"></script>'
        self.assertEqual(self.entry(url, [("www.assent.com", page(html))]),
                         [("name", "Assent"), ("type", "smartrecruiters"), ("url", url)])

    def test_oracle_vanity_pins_real_host(self):
        url = "https://careers.oracle.com/en/sites/jobsearch/jobs"
        html = '<a href="//eeho.fa.us2.oraclecloud.com/hcmUI">apply</a>'
        self.assertEqual(self.entry(url, [("careers.oracle.com", page(html))]),
                         [("name", "Oracle"), ("type", "oracle"),
                          ("host", "eeho.fa.us2.oraclecloud.com"), ("url", url)])

    def test_oracle_on_ats_pins_no_host(self):
        url = "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        self.assertEqual(
            self.entry(url, [("oraclecloud.com", page("/hcmUI/CandidateExperience"))]),
            [("name", "Acme"), ("type", "oracle"), ("url", url)])


class TestHelpers(unittest.TestCase):
    def test_path_slug_drops_locale(self):
        self.assertEqual(probe._path_slug("https://x.com/en-US/MyBoard"), "MyBoard")

    def test_generic_words_are_never_guessed_as_slugs(self):
        # Host labels and <title> are all stoplist words -> no slug-guess API calls.
        url = "https://www.careers.com/jobs"
        sess = FakeSession([("www.careers.com", page("<title>Jobs</title>"))],
                           default=api({}, status=404))
        with mock.patch.object(probe.requests, "Session", return_value=sess):
            res = probe.probe(url)
        self.assertIsNone(res.type)
        self.assertEqual(sess.calls, [url])


class TestInvariants(unittest.TestCase):
    def test_every_detectable_type_has_an_adapter(self):
        detectable = {t for t, *_ in probe.HOST_RULES + probe.HTML_SIGNATURES}
        self.assertLessEqual(detectable, set(ADAPTERS))


if __name__ == "__main__":
    unittest.main()
