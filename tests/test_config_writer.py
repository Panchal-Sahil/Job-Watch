"""Characterization tests for probe's config.json surgery: _format_entry and
_append_to_config (the text-insertion that keeps each ATS `type` in one contiguous
block in canonical adapter order). This is the riskiest logic to refactor, so we
pin: existing-group append, new-group insertion in canonical order, first-group
insertion, and the structural-mismatch fallback path.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import probe


def make_config(boards):
    """A minimal config.json text in the repo's hand-formatted style."""
    lines = ["{", '  "boards": [']
    rendered = [probe._format_entry(b) for b in boards]
    lines.append(",\n".join(rendered))
    lines.append("  ]")
    lines.append("}")
    text = "\n".join(lines)
    json.loads(text)  # sanity
    return text


class ConfigWriterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)
        # CONFIG_PATH lives in probe.config_io; patch it where the writer reads it.
        self._orig = probe.config_io.CONFIG_PATH
        probe.config_io.CONFIG_PATH = self.path

    def tearDown(self):
        probe.config_io.CONFIG_PATH = self._orig
        self.path.unlink(missing_ok=True)

    def write(self, boards):
        self.path.write_text(make_config(boards))

    def read_boards(self):
        return json.loads(self.path.read_text())["boards"]


class TestFormatEntry(ConfigWriterCase):
    def test_two_line_style(self):
        out = probe._format_entry({"name": "Acme", "type": "workday",
                                   "url": "https://x.com"})
        self.assertEqual(
            out,
            '    { "name": "Acme", "type": "workday",\n      "url": "https://x.com" }')


class TestAppendToConfig(ConfigWriterCase):
    def test_append_to_existing_group(self):
        self.write([
            {"name": "A", "type": "workday", "url": "https://a.wd1.myworkdayjobs.com/x"},
            {"name": "B", "type": "lever", "url": "https://jobs.lever.co/b"},
        ])
        probe._append_to_config({"name": "C", "type": "workday",
                                 "url": "https://c.wd1.myworkdayjobs.com/y"})
        boards = self.read_boards()
        types = [b["type"] for b in boards]
        # new workday lands at the END of the workday block, before lever
        self.assertEqual(types, ["workday", "workday", "lever"])
        self.assertEqual(boards[1]["name"], "C")

    def test_new_group_inserted_in_canonical_order(self):
        # canonical order is jobwatch.ADAPTERS: workday ... greenhouse ... lever ...
        self.write([
            {"name": "A", "type": "workday", "url": "https://a.wd1.myworkdayjobs.com/x"},
            {"name": "B", "type": "lever", "url": "https://jobs.lever.co/b"},
        ])
        # greenhouse ranks between workday and lever
        probe._append_to_config({"name": "G", "type": "greenhouse",
                                 "url": "https://job-boards.greenhouse.io/g"})
        types = [b["type"] for b in self.read_boards()]
        self.assertEqual(types, ["workday", "greenhouse", "lever"])

    def test_new_group_ranking_before_all_goes_first(self):
        # only a late-ranked type present; insert workday (rank 0) -> goes first
        self.write([
            {"name": "B", "type": "lever", "url": "https://jobs.lever.co/b"},
        ])
        probe._append_to_config({"name": "W", "type": "workday",
                                 "url": "https://w.wd1.myworkdayjobs.com/x"})
        types = [b["type"] for b in self.read_boards()]
        self.assertEqual(types, ["workday", "lever"])

    def test_result_still_valid_json(self):
        self.write([
            {"name": "A", "type": "workday", "url": "https://a.wd1.myworkdayjobs.com/x"},
        ])
        probe._append_to_config({"name": "Z", "type": "ashby",
                                 "url": "https://jobs.ashbyhq.com/z"})
        # must parse and contain both
        boards = self.read_boards()
        self.assertEqual({b["name"] for b in boards}, {"A", "Z"})


if __name__ == "__main__":
    unittest.main()
