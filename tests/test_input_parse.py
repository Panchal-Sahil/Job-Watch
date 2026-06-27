"""Characterization tests for probe's input parsing: _split_named, _extract_entries.

These pin the current behavior of how name/URL pairs are pulled out of positional
args and --file content (Markdown, prose, tab/comma/2-space pinned names) before the
refactor, so the package split can't silently change parsing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe


class TestSplitNamed(unittest.TestCase):
    def test_comma_space_pins_name(self):
        self.assertEqual(probe._split_named("Acme, https://careers.acme.com"),
                         ("Acme", "https://careers.acme.com"))

    def test_tab_pins_name(self):
        self.assertEqual(probe._split_named("Acme\thttps://careers.acme.com"),
                         ("Acme", "https://careers.acme.com"))

    def test_two_or_more_spaces_pins_name(self):
        self.assertEqual(probe._split_named("Acme   https://careers.acme.com"),
                         ("Acme", "https://careers.acme.com"))

    def test_single_space_does_not_pin(self):
        # prose like "apply at https://..." must NOT be read as a pinned name
        self.assertEqual(probe._split_named("apply at https://careers.acme.com"),
                         (None, None))

    def test_strips_markdown_bullet_and_quotes(self):
        self.assertEqual(probe._split_named('- "Acme Corp", https://acme.com/jobs'),
                         ("Acme Corp", "https://acme.com/jobs"))

    def test_name_that_is_a_url_is_rejected(self):
        self.assertEqual(probe._split_named("https://a.com, https://b.com"),
                         (None, None))

    def test_trailing_punctuation_trimmed_from_url(self):
        name, url = probe._split_named("Acme, https://acme.com/jobs.")
        self.assertEqual((name, url), ("Acme", "https://acme.com/jobs"))


class TestExtractEntries(unittest.TestCase):
    def test_plain_url_no_name(self):
        self.assertEqual(probe._extract_entries("https://acme.com/careers"),
                         [(None, "https://acme.com/careers")])

    def test_markdown_link_pulls_url_name_none(self):
        # [Name](url) markdown — current behavior guesses name later, so name is None
        self.assertEqual(probe._extract_entries("- [Acme](https://acme.com/jobs)"),
                         [(None, "https://acme.com/jobs")])

    def test_duplicate_urls_deduped_order_preserved(self):
        text = "https://a.com\nhttps://b.com\nhttps://a.com"
        self.assertEqual(probe._extract_entries(text),
                         [(None, "https://a.com"), (None, "https://b.com")])

    def test_heading_and_comment_lines_ignored(self):
        text = "# Heading\n<!-- https://hidden.com -->\nhttps://real.com"
        self.assertEqual(probe._extract_entries(text),
                         [(None, "https://real.com")])

    def test_pinned_name_line_preserved(self):
        self.assertEqual(probe._extract_entries("Acme, https://acme.com"),
                         [("Acme", "https://acme.com")])

    def test_multiple_bare_urls_on_one_line(self):
        self.assertEqual(probe._extract_entries("see https://a.com and https://b.com"),
                         [(None, "https://a.com"), (None, "https://b.com")])


if __name__ == "__main__":
    unittest.main()
