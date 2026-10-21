"""Dated availability labels and report-config validation."""
from __future__ import annotations

import json
import re

from helpers import ALPHA_SLUG, BuildCase, build

REPORT = f"reports/{ALPHA_SLUG}/index.html"
TAG = '<span class="tag tag-off">not free as of <span class="tag-date">21 Oct 2026</span></span>'


class NotFreeAsOf(BuildCase):
    def configured_reports(self, value):
        reports = self.reports_copy()
        path = reports / ALPHA_SLUG / "report.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["not_free_as_of"] = value
        path.write_text(json.dumps(config), encoding="utf-8")
        return reports

    def test_labels_follow_marked_models_in_results_and_leader_sentences(self):
        reports = self.configured_reports({"Quill 1": "21 Oct 2026"})
        out, _ = self.build(reports=reports)
        report = self.read(out, REPORT)
        for context in (
            r'<span class="fr-name">.*?</span>\s*<span class="fr-track"',
            r'<th scope="row">Quill 1.*?</th>',
            r'<dt>Best observed</dt>.*?</dd>',
            r'<p>Up is better.*?</p>',
            r'<p class="pverdict">.*?</p>',
        ):
            with self.subTest(context=context):
                matches = re.findall(context, report, re.S)
                self.assertTrue(any('<b>Quill 1</b> ' + TAG in m or 'Quill 1 ' + TAG in m
                                    for m in matches), matches)
        self.assertIn('<span class="tag">fixture catch</span>', report)
        home = self.read(out, "index.html")
        self.assertIn('<b>Quill 1</b> ' + TAG + ' leads at', home)
        roles = self.read(out, "roles/index.html")
        self.assertIn('Best: <b>Quill 1</b> ' + TAG, roles)
        for page in (report, home, roles):
            labels = re.findall(r'(?:<b>Quill 1</b>|Quill 1) ' + re.escape(TAG), page)
            self.assertTrue(labels)
            self.assertEqual(len(labels), page.count(TAG))

    def test_absent_key_renders_no_availability_labels(self):
        out, files = self.build()
        for rel in files:
            self.assertNotIn('class="tag tag-off"', self.read(out, rel), rel)
            self.assertNotIn('not free as of', self.read(out, rel), rel)

    def test_unknown_display_name_fails_before_writing_output(self):
        reports = self.configured_reports({"Not a model": "21 Oct 2026"})
        with self.assertRaisesRegex(build.BuildError, r"not_free_as_of.*unknown.*Not a model"):
            self.build(reports=reports)
        self.assertFalse((self.tmp / "dist").exists())

    def test_malformed_or_impossible_dates_fail_before_writing_output(self):
        reports = self.configured_reports({})
        path = reports / ALPHA_SLUG / "report.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        for value in ("2026-10-21", "31 Feb 2026", "29 Feb 2025", "21 October 2026", "pending", 20261021, None):
            with self.subTest(value=value):
                config["not_free_as_of"] = {"Quill 1": value}
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(build.BuildError, r"not_free_as_of.*Quill 1.*date.*21 Oct 2026"):
                    self.build(reports=reports)
                self.assertFalse((self.tmp / "dist").exists())

    def test_non_mapping_is_rejected(self):
        reports = self.configured_reports(["Quill 1"])
        with self.assertRaisesRegex(build.BuildError, r"not_free_as_of.*object"):
            self.build(reports=reports)

    def test_valid_calendar_dates_and_empty_mapping_are_accepted(self):
        reports = self.configured_reports({"Quill 1": "29 Feb 2024", "Lark 2": "1 Jan 2026"})
        out, _ = self.build(reports=reports)
        report = self.read(out, REPORT)
        self.assertIn('not free as of <span class="tag-date">29 Feb 2024</span>', report)
        self.assertIn('not free as of <span class="tag-date">1 Jan 2026</span>', report)
        path = reports / ALPHA_SLUG / "report.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["not_free_as_of"] = {}
        path.write_text(json.dumps(config), encoding="utf-8")
        out, _ = self.build("empty", reports=reports)
        self.assertNotIn('class="tag tag-off"', self.read(out, REPORT))
