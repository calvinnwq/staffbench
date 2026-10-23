"""Latest-report ordering and a dated report's scoring update note."""
import json

from helpers import ALPHA_SLUG, BETA_SLUG, BuildCase, build


class ReportUpdates(BuildCase):
    def updated_reports(self, update):
        reports = self.reports_copy()
        path = reports / ALPHA_SLUG / "report.json"
        report = json.loads(path.read_text())
        report["superseded_by"] = update
        path.write_text(json.dumps(report))
        return reports

    def test_home_leads_with_the_highest_numbered_report(self):
        out, _ = self.build()
        page = self.read(out, "index.html")
        self.assertLess(page.index(f'href="reports/{BETA_SLUG}/"'),
                        page.index(f'href="reports/{ALPHA_SLUG}/"'))
        self.assertIn("different benchmark versions are not directly comparable", page)

    def test_update_note_links_to_the_new_report_only_on_the_old_report(self):
        reports = self.updated_reports({"slug": BETA_SLUG, "note": "Old scoring did not show the required answer format."})
        out, _ = self.build(reports=reports)
        page = self.read(out, f"reports/{ALPHA_SLUG}/index.html")
        self.assertIn('role="note"', page)
        self.assertIn("Old scoring did not show the required answer format.", page)
        self.assertIn(f'<a href="../{BETA_SLUG}/">See Report 02 for updated results.</a>', page)
        self.assertLess(page.index("Old scoring"), page.index('<h1>'))
        self.assertNotIn("Old scoring", self.read(out, f"reports/{BETA_SLUG}/index.html"))
        self.assertEqual(build.check_output(out, None), [])

    def test_update_note_is_escaped(self):
        reports = self.updated_reports({"slug": BETA_SLUG, "note": "Scored <earlier> & replaced."})
        out, _ = self.build(reports=reports)
        page = self.read(out, f"reports/{ALPHA_SLUG}/index.html")
        self.assertIn("Scored &lt;earlier&gt; &amp; replaced.", page)
        self.assertNotIn("Scored <earlier>", page)

    def test_invalid_or_non_newer_update_target_fails_before_writing(self):
        reports = self.updated_reports({"slug": BETA_SLUG, "note": "Old scoring."})
        path = reports / ALPHA_SLUG / "report.json"
        report = json.loads(path.read_text())
        for value in (None, "missing", {}, {"slug": "unknown", "note": "Old scoring."},
                      {"slug": ALPHA_SLUG, "note": "Old scoring."},
                      {"slug": BETA_SLUG, "note": ""}, {"slug": BETA_SLUG, "note": 3}):
            with self.subTest(value=value):
                report["superseded_by"] = value
                path.write_text(json.dumps(report))
                with self.assertRaisesRegex(build.BuildError, "superseded_by"):
                    self.build(reports=reports)
                self.assertFalse((self.tmp / "dist").exists())

    def test_report_without_update_renders_no_pointer(self):
        out, _ = self.build()
        self.assertNotIn("for updated results", self.read(out, f"reports/{ALPHA_SLUG}/index.html"))
