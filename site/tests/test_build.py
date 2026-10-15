"""End-to-end tests of site/build.py against the committed synthetic fixture."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import ALPHA, ALPHA_SLUG, BETA_SLUG, LEDGER, REPORTS, SITE, BuildCase, build, private_terms

REPORT_ALPHA = f"reports/{ALPHA_SLUG}/index.html"
REPORT_BETA = f"reports/{BETA_SLUG}/index.html"
PAGES = ["404.html", "index.html", REPORT_ALPHA, REPORT_BETA, "roles/index.html"]


def digest(out: Path) -> dict[str, str]:
    return {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob("*")) if p.is_file()}


class BuildOutput(BuildCase):
    def test_build_writes_the_three_page_types_and_404(self):
        out, files = self.build()
        self.assertEqual(sorted(files), PAGES)
        self.assertEqual(sorted(digest(out)), PAGES)
        for rel in PAGES:
            page = self.read(out, rel)
            self.assertTrue(page.startswith("<!doctype html>"), rel)
            self.assertIn("</html>", page, rel)
            self.assertNotIn("<script", page.lower(), rel)
            footer_match = re.search(r'<footer\b.*?</footer>', page, re.S)
            assert footer_match is not None, rel
            footer = footer_match.group()
            self.assertIn('Code: <a href="https://github.com/calvinnwq/staffbench/blob/main/LICENSE">MIT</a>.', footer, rel)
            self.assertIn('Tasks and results: <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.', footer, rel)
            self.assertIn('href="https://github.com/calvinnwq/staffbench"', footer, rel)

    def test_output_is_byte_identical_across_builds(self):
        a, _ = self.build("a")
        b, _ = self.build("b")
        self.assertEqual(digest(a), digest(b))

    def test_home_lists_every_report_with_derived_summary(self):
        out, _ = self.build()
        home = self.read(out, "index.html")
        self.assertIn(f'href="reports/{ALPHA_SLUG}/"', home)
        self.assertIn(f'href="reports/{BETA_SLUG}/"', home)
        self.assertIn("<b>Quill 1</b> leads at 67%, but 2 models are too close to tell apart", home)
        self.assertIn("1 task beats every model", home)
        self.assertIn("Results pending", home)
        self.assertIn("<em>Three</em> hats", home)

    def test_report_page_numbers_come_from_the_data(self):
        out, _ = self.build()
        page = self.read(out, REPORT_ALPHA)
        rows = re.findall(r'<span class="fr-name"><b>([^<]+)</b>.*?<span class="fr-val"><b>([^<]+)</b><small>([^<]+)</small>', page, re.S)
        self.assertEqual(rows, [("Quill 1", "66.7%", "33&ndash;94"), ("Lark 2", "55.6%", "33&ndash;78"),
                                ("Quill 1 Mini", "33.3%", "11&ndash;61"), ("Lark 2 Fast", "11.1%", "0&ndash;22")])
        self.assertIn("<b>2 models</b> are too close to tell apart for first place", page)
        self.assertIn("<b>1 of 6 tasks</b> were never passed by any model", page)
        self.assertIn("One task beats every model on every run.", page)
        self.assertIn("Three models are on it, from Lark 2 Fast, the fastest, to Quill 1, the highest scoring.", page)
        self.assertIn("<li><b>Wren 0</b> - Fixture model that never answered its test call</li>", page)
        self.assertIn('<span class="tag">fixture catch</span>', page)
        self.assertIn("Clearly behind the leader", page)

    def test_display_names_come_from_the_report_config(self):
        reports = self.reports_copy()
        cfg_path = reports / ALPHA_SLUG / "report.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["models"]["acme/quill-1"] = "Renamed Leader"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        out, _ = self.build(reports=reports)
        for rel in ("index.html", REPORT_ALPHA, "roles/index.html"):
            page = self.read(out, rel)
            self.assertIn("Renamed Leader", page, rel)
            self.assertNotRegex(page, r"Quill 1(?! Mini)", rel)

    def test_roles_page_has_every_role_and_task_with_strips_from_newest_results(self):
        out, _ = self.build()
        page = self.read(out, "roles/index.html")
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        for role in ledger["roles"]:
            self.assertIn(f'<section class="role" id="{role["id"]}"', page)
        for task in ledger["tasks"]:
            self.assertIn(f'<article class="task" id="{task["id"].lower()}">', page)
        self.assertEqual(page.count('class="res-src"'), len(ledger["tasks"]))
        self.assertIn(f'<a href="../reports/{ALPHA_SLUG}/">Report 01 &middot; Alpha fixture pool</a>', page)

    def test_roles_strips_follow_the_highest_numbered_report_with_results(self):
        out, _ = self.build(overrides={ALPHA_SLUG: ALPHA, BETA_SLUG: ALPHA})
        page = self.read(out, "roles/index.html")
        self.assertIn(f'<a href="../reports/{BETA_SLUG}/">Report 02 &middot; Beta fixture pool</a>', page)
        self.assertNotIn(f'href="../reports/{ALPHA_SLUG}/">Report 01', page)

    def test_roles_page_without_any_results_says_pending(self):
        out, _ = self.build(overrides={})
        page = self.read(out, "roles/index.html")
        self.assertNotIn('class="res-src"', page)
        self.assertIn("Results pending: no report has published runs yet.", page)

    def test_results_dir_is_read_when_no_override(self):
        results = self.tmp / "results"
        (results / ALPHA_SLUG).mkdir(parents=True)
        (results / ALPHA_SLUG / "leaderboard.json").write_bytes(ALPHA.read_bytes())
        out, _ = self.build(overrides={}, results_dir=results)
        self.assertIn('id="standings"', self.read(out, REPORT_ALPHA))


class ResultsPending(BuildCase):
    def test_report_without_results_renders_pending_page(self):
        out, _ = self.build()
        page = self.read(out, REPORT_BETA)
        self.assertIn("<h2>Results pending</h2>", page)
        self.assertIn("Publication pending", page)
        self.assertIn("run dates pending", page)
        for absent in ('id="standings"', 'class="forest"', 'class="matrix"', 'class="scatter"'):
            self.assertNotIn(absent, page)

    def test_repository_report_config_builds_as_pending(self):
        repo_reports = SITE.parent / "reports"
        out, files = self.build(reports=repo_reports, overrides={})
        rel = "reports/nous-portal-free-models-2026-10/index.html"
        self.assertIn(rel, files)
        page = self.read(out, rel)
        self.assertIn("Report 01", page)
        self.assertIn("Hermes Agent \u00d7 Nous Portal free models", page)
        published = json.loads((repo_reports / "nous-portal-free-models-2026-10/report.json").read_text())["published"]
        self.assertNotEqual(published, "pending")
        self.assertIn(f"Published {published}", page)
        self.assertNotIn("Publication pending", page)
        self.assertIn("<h2>Results pending</h2>", page)


class PreviewBanner(BuildCase):
    NOTICE = "Fixture preview: invented numbers for testing the banner."

    def test_banner_on_every_page_when_results_carry_preview_notice(self):
        override = self.alpha_copy(preview_notice=self.NOTICE)
        out, _ = self.build(overrides={ALPHA_SLUG: override})
        for rel in PAGES:
            self.assertIn(f'<p class="draft" role="note">{self.NOTICE}</p>', self.read(out, rel), rel)

    def test_no_banner_without_preview_notice(self):
        out, _ = self.build()
        for rel in PAGES:
            self.assertNotIn('class="draft"', self.read(out, rel), rel)


class Production(BuildCase):
    def test_meta_tags_and_favicon_on_every_page(self):
        out, _ = self.build()
        for rel in PAGES:
            page = self.read(out, rel)
            for tag in ('property="og:title"', 'property="og:description"', 'property="og:type"',
                        'name="twitter:card"', 'name="twitter:title"', 'name="description"',
                        '<link rel="icon" href="data:image/svg+xml,'):
                self.assertIn(tag, page, f"{rel}: {tag}")
            self.assertNotIn('rel="canonical"', page, rel)

    def test_canonical_links_follow_base_url(self):
        out, _ = self.build(base_url="https://staffbench.example.org/site")
        self.assertIn('<link rel="canonical" href="https://staffbench.example.org/site/">', self.read(out, "index.html"))
        self.assertIn('<link rel="canonical" href="https://staffbench.example.org/site/roles/">', self.read(out, "roles/index.html"))
        self.assertIn(f'<link rel="canonical" href="https://staffbench.example.org/site/reports/{ALPHA_SLUG}/">',
                      self.read(out, REPORT_ALPHA))
        self.assertIn('property="og:url" content="https://staffbench.example.org/site/roles/"', self.read(out, "roles/index.html"))
        self.assertIn('href="https://staffbench.example.org/site/roles/"', self.read(out, "404.html"))

    def test_base_url_must_be_https(self):
        with self.assertRaises(build.BuildError):
            self.build(base_url="http://staffbench.example.org/")

    def test_no_remote_assets(self):
        out, _ = self.build()
        for rel in PAGES:
            page = self.read(out, rel)
            self.assertNotRegex(page, r'(?:src|srcset)="(?:https?:)?//', rel)
            self.assertNotRegex(page, r"url\(\s*['\"]?(?:https?:)?//", rel)
            self.assertNotRegex(page, r"@import", rel)


class LinkCheck(BuildCase):
    def test_built_site_has_no_broken_links_or_anchors(self):
        out, _ = self.build()
        self.assertEqual(build.check_output(out, None), [])

    def test_check_flags_broken_links_missing_anchors_and_remote_assets(self):
        out, _ = self.build()
        page = out / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace(
            "</main>", '<a href="roles/#no-such-role">x</a><a href="missing/">y</a>'
            '<img src="https://cdn.example.org/a.png" alt=""><a href="https://example.org/">ok</a></main>'),
            encoding="utf-8")
        problems = build.check_output(out, None)
        self.assertIn("index.html: missing anchor roles/#no-such-role", problems)
        self.assertIn("index.html: broken link missing/", problems)
        self.assertIn("index.html: remote asset https://cdn.example.org/a.png", problems)
        self.assertEqual(len(problems), 3, problems)

    def test_build_fails_on_an_unresolved_link_or_anchor(self):
        original = build.render
        for planted, expected in (('<a href="roles/#nowhere">x</a>', "missing anchor roles/#nowhere"),
                                  ('<a href="reports/gone/">x</a>', "broken link reports/gone/")):
            def render_with_bad_link(*args, **kwargs):
                files = original(*args, **kwargs)
                files["index.html"] = files["index.html"].replace("</main>", planted + "</main>")
                return files

            with self.subTest(planted=planted), mock.patch.object(build, "render", render_with_bad_link):
                with self.assertRaisesRegex(build.BuildError, re.escape(expected)):
                    self.build()
                self.assertFalse((self.tmp / "dist").exists())


class LeakGate(BuildCase):
    def test_private_term_from_environment_fails_the_build(self):
        out = self.tmp / "dist"
        with private_terms(r"(?i)lark\s2\sfast"):
            with self.assertRaisesRegex(build.BuildError, "public-content check failed"):
                build.build(out, LEDGER, REPORTS, {ALPHA_SLUG: ALPHA}, None, self.no_results)
        self.assertFalse(out.exists())

    def test_built_in_rule_fails_the_build_without_private_terms(self):
        reports = self.reports_copy()
        cfg_path = reports / ALPHA_SLUG / "report.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["notes"].append(["Quill 1", "was submitted by someone" + "@" + "invented-lab.test."])
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaisesRegex(build.BuildError, "public-content check failed"):
            self.build(reports=reports)

    def test_failed_build_leaves_previous_output_untouched(self):
        out, _ = self.build()
        before = digest(out)
        with private_terms("Quill"):
            with self.assertRaises(build.BuildError):
                build.build(out, LEDGER, REPORTS, {ALPHA_SLUG: ALPHA}, None, self.no_results)
        self.assertEqual(digest(out), before)

    def test_clean_build_passes_with_unrelated_private_terms(self):
        out = self.tmp / "dist"
        with private_terms(r"(?i)\bnot-in-the-fixture\b"):
            build.build(out, LEDGER, REPORTS, {ALPHA_SLUG: ALPHA}, None, self.no_results)
        self.assertTrue((out / "index.html").is_file())


class Inputs(BuildCase):
    def test_unknown_override_slug_fails(self):
        with self.assertRaisesRegex(build.BuildError, "unknown reports"):
            self.build(overrides={"no-such-report": ALPHA})

    def test_missing_override_file_fails(self):
        with self.assertRaisesRegex(build.BuildError, "not found"):
            self.build(overrides={ALPHA_SLUG: self.tmp / "absent.json"})

    def test_results_must_name_every_model(self):
        reports = self.reports_copy()
        cfg_path = reports / ALPHA_SLUG / "report.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        del cfg["models"]["orbit/lark-2"]
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaisesRegex(build.BuildError, "no display name"):
            self.build(reports=reports)

    def test_refuses_to_replace_a_directory_it_did_not_make(self):
        out = self.tmp / "dist"
        out.mkdir()
        (out / "keep.txt").write_text("not ours", encoding="utf-8")
        with self.assertRaisesRegex(build.BuildError, "refusing to replace"):
            self.build()
        self.assertTrue((out / "keep.txt").exists())


class CommandLine(BuildCase):
    def run_cli(self, *args, env_terms=None):
        import os
        env = {k: v for k, v in os.environ.items() if k != "STAFFBENCH_PRIVATE_TERMS"}
        if env_terms:
            env["STAFFBENCH_PRIVATE_TERMS"] = env_terms
        return subprocess.run([sys.executable, "-S", str(SITE / "build.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_cli_builds_the_fixture(self):
        out = self.tmp / "dist"
        result = self.run_cli("--out", str(out), "--ledger", str(LEDGER), "--reports-dir", str(REPORTS),
                              "--results-override", f"{ALPHA_SLUG}={ALPHA}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wrote 5 pages", result.stdout)
        self.assertTrue((out / "roles" / "index.html").is_file())

    def test_cli_exits_non_zero_on_leak(self):
        terms = self.tmp / "terms.txt"
        terms.write_text("Quill\n", encoding="utf-8")
        result = self.run_cli("--out", str(self.tmp / "dist"), "--ledger", str(LEDGER), "--reports-dir", str(REPORTS),
                              "--results-override", f"{ALPHA_SLUG}={ALPHA}", env_terms=str(terms))
        self.assertEqual(result.returncode, 1)
        self.assertIn("public-content check failed", result.stderr)

    def test_cli_rejects_malformed_override(self):
        result = self.run_cli("--out", str(self.tmp / "dist"), "--ledger", str(LEDGER), "--reports-dir", str(REPORTS),
                              "--results-override", "no-equals-sign")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SLUG=PATH", result.stderr)


if __name__ == "__main__":
    unittest.main()
