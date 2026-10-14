"""Tests for publishing a built leaderboard under results/<slug>/."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_leaderboard import _build_synthetic_input, _run_builder


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_results.py"
STRIPPED = {"run_id", "record_path", "raw_output_reference"}


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in _keys(value)}
    if isinstance(node, list):
        return {key for value in node for key in _keys(value)}
    return set()


def _built_leaderboard(root: Path) -> Path:
    _build_synthetic_input(root, passed_tasks_by_model={"model-a:free": {"ALPHA-01"}})
    result = _run_builder(root)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return root / "leaderboard.json"


def _publish(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key != "STAFFBENCH_PRIVATE_TERMS"}
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=ROOT, check=False, capture_output=True, text=True, env=environment
    )


class PublishResultsTests(unittest.TestCase):
    def test_published_copy_drops_run_identity_and_the_input_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _built_leaderboard(root)
            built = json.loads(source.read_text(encoding="utf-8"))
            self.assertTrue(STRIPPED <= _keys(built))

            result = _publish(str(source), "--slug", "01-test-pool", "--results-root", str(root / "results"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            published_path = root / "results" / "01-test-pool" / "leaderboard.json"
            published = json.loads(published_path.read_text(encoding="utf-8"))
            self.assertNotIn("input", published)
            self.assertFalse(STRIPPED & _keys(published))
            expected = {key: value for key, value in built.items() if key != "input"}
            self.assertEqual(published["aggregate"], expected["aggregate"])
            self.assertEqual(published["roles"], expected["roles"])
            self.assertEqual(set(published), set(expected))
            first_run = published["models"][0]["task_cells"][0]["runs"][0]
            self.assertIn("status", first_run)
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")), built)

    def test_leak_check_hit_fails_and_removes_the_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _built_leaderboard(root)
            built = json.loads(source.read_text(encoding="utf-8"))
            built["publication"]["reason"] = "Reviewed by Zebulon Quartermaine before release."
            source.write_text(json.dumps(built), encoding="utf-8")
            terms = root / "terms.txt"
            terms.write_text("# test pattern\nZebulon Quartermaine\n", encoding="utf-8")

            result = _publish(
                str(source), "--slug", "02-leaky", "--results-root", str(root / "results"),
                env={"STAFFBENCH_PRIVATE_TERMS": str(terms)},
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Zebulon Quartermaine", result.stdout)
            self.assertFalse((root / "results" / "02-leaky" / "leaderboard.json").exists())

    def test_rejects_unsafe_slugs_and_existing_output_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _built_leaderboard(root)
            for slug in ("../escape", "Has Space", "", "a/b"):
                with self.subTest(slug=slug):
                    result = _publish(str(source), "--slug", slug, "--results-root", str(root / "results"))
                    self.assertEqual(result.returncode, 2)
            args = (str(source), "--slug", "03-pool", "--results-root", str(root / "results"))
            self.assertEqual(_publish(*args).returncode, 0)
            again = _publish(*args)
            self.assertEqual(again.returncode, 2)
            self.assertIn("--force", again.stderr)
            self.assertEqual(_publish(*args, "--force").returncode, 0)

    def test_rejects_a_document_that_is_not_a_built_leaderboard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "leaderboard.json"
            source.write_text(json.dumps({"benchmark_id": "staffbench"}), encoding="utf-8")
            result = _publish(str(source), "--slug", "04-bad", "--results-root", str(root / "results"))
            self.assertEqual(result.returncode, 2)
            self.assertFalse((root / "results" / "04-bad").exists())


if __name__ == "__main__":
    unittest.main()
