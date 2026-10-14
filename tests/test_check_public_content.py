import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_public_content.py"


def run(paths, terms=None):
    env = {k: v for k, v in os.environ.items() if k != "STAFFBENCH_PRIVATE_TERMS"}
    if terms:
        env["STAFFBENCH_PRIVATE_TERMS"] = terms
    return subprocess.run([sys.executable, "-S", str(SCRIPT), *paths], capture_output=True, text=True, env=env)


class CheckPublicContentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_clean_file_passes(self):
        result = run([self.write("ok.md", "Contact ops@example.com or 1+bot@users.noreply.github.com.\n")])
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_built_in_rules_fail(self):
        bad = "/Users/someone/x\nwrite to person@mail.test\n"  # public-content: allow
        result = run([self.write("bad.md", bad)])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("bad.md:"), 2)

    def test_diff_decorator_lines_are_not_emails(self):
        lines = ["+@contextlib.contextmanager", "-@functools.lru_cache", "+    @unittest.mock.patch"]
        result = run([self.write("change.diff", "\n".join(lines) + "\n")])
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_email_after_diff_marker_still_fails(self):
        lines = [
            "+contact person@mail.test",  # public-content: allow
            '+"person@mail.test"',  # public-content: allow
            "+mailto:person@mail.test",  # public-content: allow
            "+person@mail.test",  # public-content: allow
            "-person@mail.test",  # public-content: allow
        ]
        result = run([self.write("change.diff", "\n".join(lines) + "\n")])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("change.diff:"), 5, result.stdout)

    def test_private_terms_and_allow_marker(self):
        terms = self.write("terms.txt", "# comment\n(?i)secretproject\n")
        target = self.write("page.html", "about SecretProject\nsecretproject public-content: allow\n")
        result = run([target], terms)
        self.assertEqual(result.returncode, 1)
        self.assertIn("page.html:1: SecretProject", result.stdout)
        self.assertNotIn("page.html:2", result.stdout)


if __name__ == "__main__":
    unittest.main()
