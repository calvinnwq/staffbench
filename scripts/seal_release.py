"""Explicitly regenerate release pins after a reviewed benchmark version change.

This authoring tool never runs from a validator or model harness. Run the
validators and review the generated diff after sealing; hashes are not tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from evaluate_task import ORACLE_VERSION
    from release_lock import (
        LOCK_VERSION, SHARED_ARTIFACT_PATHS, _sha256, expected_manifest_paths,
        expected_release_artifact_paths,
    )
    from validate_benchmark import EXPECTED_BENCHMARK_VERSION, EXPECTED_TASK_IDS, _canonical_fingerprint
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_task import ORACLE_VERSION
    from scripts.release_lock import (
        LOCK_VERSION, SHARED_ARTIFACT_PATHS, _sha256, expected_manifest_paths,
        expected_release_artifact_paths,
    )
    from scripts.validate_benchmark import EXPECTED_BENCHMARK_VERSION, EXPECTED_TASK_IDS, _canonical_fingerprint


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pin(path: Path, name: str, fingerprint: str | None) -> None:
    if fingerprint is None:
        raise ValueError(f"cannot fingerprint {name}")
    source = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        rf'(?m)^{re.escape(name)} = "[0-9a-f_]+"$',
        f'{name} = "{fingerprint}"', source,
    )
    if count != 1:
        raise ValueError(f"expected one {name} pin in {path.name}")
    path.write_text(updated, encoding="utf-8")


def seal_release() -> None:
    ledger = json.loads((ROOT / "data/task-ledger.json").read_text())
    policy = json.loads((ROOT / "data/leaderboard-policy.json").read_text())
    if ledger["benchmark_version"] != EXPECTED_BENCHMARK_VERSION or policy["benchmark_version"] != EXPECTED_BENCHMARK_VERSION:
        raise ValueError("advance the ledger, policy and validator versions together before sealing")
    _pin(ROOT / "scripts/validate_benchmark.py", "EXPECTED_LEDGER_FINGERPRINT", _canonical_fingerprint(ledger))
    _pin(ROOT / "scripts/build_leaderboard.py", "EXPECTED_POLICY_FINGERPRINT", _canonical_fingerprint(policy))
    tasks = {}
    for task_id in sorted(EXPECTED_TASK_IDS):
        paths = expected_release_artifact_paths(task_id)
        package = ROOT / "fixtures" / task_id.lower()
        manifest_path = ROOT / paths["manifest"]
        manifest = json.loads(manifest_path.read_text())
        manifest["benchmark_version"] = EXPECTED_BENCHMARK_VERSION
        manifest["evaluator"]["version"] = ORACLE_VERSION
        for key, relative in expected_manifest_paths(task_id).items():
            if key == "release_gate":
                manifest[key] = relative
            else:
                manifest[key]["path"] = relative
                manifest[key]["sha256"] = _sha256(ROOT / paths[key])
        for control in manifest["controls"]:
            key = {"known-good-control": "known_good", "known-bad-control": "known_bad"}[control["condition"]]
            control["path"] = (ROOT / paths[key]).relative_to(package).as_posix()
            control["sha256"] = _sha256(ROOT / paths[key])
        _write_json(manifest_path, manifest)
        tasks[task_id] = {
            "fixture_id": manifest["fixture"]["id"],
            "fixture_version": manifest["fixture"]["version"],
            "artifacts": {key: {"path": path, "sha256": _sha256(ROOT / path)} for key, path in paths.items()},
        }
    lock = {
        "$schema": "../schemas/release-artifact-lock.schema.json",
        "benchmark_id": "staffbench", "benchmark_version": EXPECTED_BENCHMARK_VERSION,
        "lock_version": LOCK_VERSION,
        "shared": {key: {"path": path, "sha256": _sha256(ROOT / path)} for key, path in SHARED_ARTIFACT_PATHS.items()},
        "tasks": tasks,
    }
    lock_path = ROOT / "data/release-artifact-lock.json"
    _write_json(lock_path, lock)
    _pin(ROOT / "scripts/release_lock.py", "EXPECTED_RELEASE_LOCK_FINGERPRINT", hashlib.sha256(lock_path.read_bytes()).hexdigest())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True, help="explicitly repin reviewed release artifacts")
    parser.parse_args()
    seal_release()
    print(f"sealed benchmark v{EXPECTED_BENCHMARK_VERSION}: 18 manifests and release lock; run validators next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
