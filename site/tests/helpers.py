"""Shared setup for the site tests: paths to the synthetic fixture and a build wrapper."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
FIXTURE = HERE / "fixture"
LEDGER = FIXTURE / "task-ledger.json"
REPORTS = FIXTURE / "reports"
ALPHA = FIXTURE / "leaderboard-alpha.json"
ALPHA_SLUG = "alpha-pool-2026-01"
BETA_SLUG = "beta-pool-2026-02"

if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

import build  # noqa: E402


@contextlib.contextmanager
def private_terms(*patterns: str):
    """Point STAFFBENCH_PRIVATE_TERMS at a temporary file holding the given patterns (none: unset)."""
    saved = os.environ.pop("STAFFBENCH_PRIVATE_TERMS", None)
    tmp = None
    try:
        if patterns:
            fd, tmp = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("# test patterns\n" + "\n".join(patterns) + "\n")
            os.environ["STAFFBENCH_PRIVATE_TERMS"] = tmp
        yield
    finally:
        os.environ.pop("STAFFBENCH_PRIVATE_TERMS", None)
        if saved is not None:
            os.environ["STAFFBENCH_PRIVATE_TERMS"] = saved
        if tmp:
            os.unlink(tmp)


class BuildCase(unittest.TestCase):
    """A scratch directory per test and a fixture build helper."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.no_results = self.tmp / "no-results"
        self.no_results.mkdir()

    def build(self, name="dist", *, ledger=LEDGER, reports=REPORTS, overrides=None, base_url=None, results_dir=None):
        out = self.tmp / name
        if overrides is None:
            overrides = {ALPHA_SLUG: ALPHA}
        with private_terms():
            files = build.build(out, ledger, reports, overrides, base_url, results_dir or self.no_results)
        return out, files

    def alpha_copy(self, **changes) -> Path:
        """A copy of the alpha results with top-level fields changed (None deletes a field)."""
        data = json.loads(ALPHA.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path = self.tmp / f"alpha-{len(list(self.tmp.glob('alpha-*.json')))}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def reports_copy(self) -> Path:
        dest = self.tmp / "reports"
        shutil.copytree(REPORTS, dest)
        return dest

    @staticmethod
    def read(out: Path, rel: str) -> str:
        return (out / rel).read_text(encoding="utf-8")
