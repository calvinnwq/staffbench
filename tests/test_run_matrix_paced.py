"""Paced matrix driver tests: a fake agent command, a fake rate-limit probe, and a fake clock. No network."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from scripts import run_matrix_paced as paced


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".model-evidence"

FAKE_AGENT = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
value = lambda flag: args[args.index(flag) + 1]
run_id = Path(value("--usage-file")).parent.name
Path(value("--usage-file")).write_text(json.dumps({"model": value("--model"), "provider": value("--provider")}), encoding="utf-8")
fail_once, fail_always = os.environ.get("FAIL_ONCE", "-"), os.environ.get("FAIL_ALWAYS", "-")
if run_id.endswith(fail_always) or (run_id.endswith(fail_once) and "retry" not in run_id):
    sys.exit(3)
print("{}")
"""

FAKE_PROBE = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
script = Path(os.environ["PROBE_SCRIPT"])
responses = json.loads(script.read_text(encoding="utf-8"))
response = responses.pop(0) if len(responses) > 1 else responses[0]
script.write_text(json.dumps(responses), encoding="utf-8")
with open(os.environ["PROBE_CALLS"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"model": sys.argv[-1], "home": os.environ.get("STAFFBENCH_HERMES_HOME")}) + "\\n")
print("probe diagnostics on stdout")
print(json.dumps(response))
"""


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds

    def clock(self) -> float:
        return self.now


def _roster() -> dict:
    return {
        "schema_version": "model-roster-v1",
        "benchmark_id": "staffbench",
        "benchmark_version": "0.6.0",
        "snapshot_id": "test-roster",
        "provider": "test-provider",
        "captured_at": "2026-10-14T00:00:00Z",
        "models": [
            {
                "model_id": "test/model-a",
                "requested_model_id": "test/model-a",
                "resolved_model_id": "test/model-a",
                "provider_requested": "test-provider",
                "provider_resolved": "test-provider",
                "availability": "eligible",
            },
            {
                "model_id": "test/gone",
                "requested_model_id": "test/gone",
                "resolved_model_id": "unresolved",
                "provider_requested": "test-provider",
                "provider_resolved": "unresolved",
                "availability": "excluded",
                "exclusion_reason": "not available",
            },
        ],
    }


def _first_cells(count: int):
    original = paced.build_matrix_plan

    def plan(*args, **kwargs):
        return original(*args, **kwargs)[:count]

    return mock.patch.object(paced, "build_matrix_plan", side_effect=plan)


class PacedMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        EVIDENCE.mkdir(exist_ok=True)
        self.work = EVIDENCE / f"test-paced-{uuid.uuid4().hex[:10]}"
        self.work.mkdir()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.tools = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tools, True)
        self.agent = self.tools / "agent.py"
        self.agent.write_text(FAKE_AGENT, encoding="utf-8")
        self.agent.chmod(0o755)
        self.probe = self.tools / "probe.py"
        self.probe.write_text(FAKE_PROBE, encoding="utf-8")
        self.probe.chmod(0o755)
        self.hermes_home = self.tools / "secret-hermes-home"
        self.hermes_home.mkdir()
        self.roster = self.work / "roster.json"
        self.roster.write_text(json.dumps(_roster()), encoding="utf-8")
        self.prefix = self.work / "run"
        self.time = FakeTime()

    def drive(self, probe_responses: list[dict] | None, env: dict[str, str] | None = None, **kwargs) -> dict:
        environment = {"PROBE_SCRIPT": str(self.tools / "responses.json"), "PROBE_CALLS": str(self.tools / "calls.jsonl")}
        environment.update(env or {})
        if probe_responses is not None:
            Path(environment["PROBE_SCRIPT"]).write_text(json.dumps(probe_responses), encoding="utf-8")
        options = {
            "root": ROOT,
            "roster_path": self.roster,
            "output_prefix": self.prefix,
            "snapshot_id": "paced-test",
            "replicates": ["r1"],
            "gap_seconds": 20,
            "agent_command": f"{sys.executable} {self.agent}",
            "probe_command": f"{sys.executable} {self.probe}" if probe_responses is not None else None,
            "hermes_home": self.hermes_home,
            "sleep": self.time.sleep,
            "clock": self.time.clock,
        }
        options.update(kwargs)
        with mock.patch.dict(os.environ, environment), mock.patch.object(paced, "_run_release_gate") as gate:
            self.gate = gate
            return paced.run_paced(**options)

    def probe_calls(self) -> list[dict]:
        path = self.tools / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def run_dirs(self, root: Path) -> list[str]:
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    def test_gap_rate_limit_wait_and_recovery_rerun_keep_the_failed_original(self) -> None:
        probes = [{"status": 429, "retry_after": 120}, {"status": 200}]
        with _first_cells(3):
            summary = self.drive(probes, {"FAIL_ONCE": "fin-01"}, max_retries=2)

        original, recovery = self.work / "run-r1", self.work / "run-r1-recovery"
        self.assertFalse(summary["incomplete"])
        self.gate.assert_called_once_with()
        # 429 wait (retry_after 120 + 30 s margin), then a fixed gap before every later launch, retries included.
        self.assertEqual(self.time.sleeps, [150, 20, 20, 20])
        self.assertEqual(len(self.probe_calls()), 5)
        self.assertTrue(all(call["model"] == "test/model-a" for call in self.probe_calls()))
        self.assertEqual(self.probe_calls()[0]["home"], str(self.hermes_home))

        self.assertEqual(len(self.run_dirs(original)), 3)
        failed = next(name for name in self.run_dirs(original) if name.endswith("fin-01"))
        failed_record = json.loads((original / failed / "run-record.json").read_text(encoding="utf-8"))
        self.assertEqual(failed_record["execution_status"], "failed")
        self.assertEqual(self.run_dirs(recovery), [failed.replace("-r1-", "-r1-retry1-")])

        manifest = json.loads((original / "leaderboard-input.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["runs"]), 3)
        self.assertEqual(manifest["roster_path"], (original / "roster.json").relative_to(ROOT).as_posix())
        recovery_manifest = json.loads((recovery / "leaderboard-input.json").read_text(encoding="utf-8"))
        self.assertEqual([run["run_id"] for run in recovery_manifest["runs"]], self.run_dirs(recovery))

        self.assertEqual([cell["attempt"] for cell in summary["cells"]], [0, 0, 0, 1])
        written = [Path(f"{self.prefix}-driver-summary.json"), Path(f"{self.prefix}-driver.log")]
        written += [original / "leaderboard-input.json", recovery / "leaderboard-input.json"]
        for path in written:
            with self.subTest(file=path.name):
                self.assertNotIn(str(self.hermes_home), path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(written[0].read_text(encoding="utf-8")), summary)

    def test_recovery_reruns_are_bounded(self) -> None:
        with _first_cells(1):
            summary = self.drive([{"status": 200}], {"FAIL_ALWAYS": "cos-01"}, max_retries=2)

        self.assertEqual([cell["attempt"] for cell in summary["cells"]], [0, 1, 2])
        self.assertEqual(len(self.run_dirs(self.work / "run-r1")), 1)
        self.assertEqual(len(self.run_dirs(self.work / "run-r1-recovery")), 2)
        self.assertTrue(all(cell["execution_status"] == "failed" for cell in summary["cells"]))

    def test_probe_errors_back_off_and_budget_top_up_uses_ratelimit_headers(self) -> None:
        probes = [
            {"status": None, "error": "ConnectionError"},
            {"status": 200, "headers": {"RateLimit": "\"fairshare\";r=10;t=30", "RateLimit-Policy": "\"fairshare\";q=100;w=60"}},
        ]
        with _first_cells(1):
            self.drive(probes, probe_error_wait_seconds=45)
        self.assertEqual(self.time.sleeps, [45, 29])

    def test_deadline_stops_cleanly_and_records_an_incomplete_run(self) -> None:
        with _first_cells(2):
            summary = self.drive([{"status": 429, "retry_after": 3000}], deadline_hours=0.5)

        self.assertTrue(summary["incomplete"])
        self.assertEqual(summary["cells"], [])
        self.assertEqual(self.time.sleeps, [])
        self.assertTrue(json.loads(Path(f"{self.prefix}-driver-summary.json").read_text(encoding="utf-8"))["incomplete"])

    def test_without_a_probe_only_the_fixed_gap_paces_cells(self) -> None:
        with _first_cells(2):
            summary = self.drive(None, gap_seconds=5)
        self.assertEqual(self.time.sleeps, [5])
        self.assertEqual(len(summary["cells"]), 2)

    def test_dry_run_plans_without_gate_probe_or_output(self) -> None:
        summary = self.drive([{"status": 200}], replicates=["r1", "r2"], dry_run=True)
        self.gate.assert_not_called()
        self.assertEqual(summary["planned_cells"], 36)
        self.assertEqual(self.probe_calls(), [])
        self.assertFalse((self.work / "run-r1").exists())

    def test_refuses_non_empty_roots_and_a_missing_hermes_home(self) -> None:
        (self.work / "run-r1-recovery").mkdir()
        (self.work / "run-r1-recovery" / "stale.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(paced.MatrixInputError, "not empty"):
            self.drive([{"status": 200}])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STAFFBENCH_HERMES_HOME", None)
            with self.assertRaisesRegex(paced.MatrixInputError, "STAFFBENCH_HERMES_HOME"):
                paced.run_paced(root=ROOT, roster_path=self.roster, output_prefix=self.work / "other",
                                snapshot_id="paced-test", sleep=self.time.sleep, clock=self.time.clock)


if __name__ == "__main__":
    unittest.main()
