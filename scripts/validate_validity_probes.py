"""Run the v0.6.0 validity gate without invoking any model.

This gate evaluates only checked-in controls, validity probes and, optionally,
a built leaderboard report.  It intentionally has no model-runner dependency.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

try:
    from evaluate_task import evaluate_task
    from leaderboard_aggregate import AggregateValidationError, validate_aggregate
    from release_lock import ReleaseLockError, load_release_lock, verify_task_release_artifacts
    from validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance
except ImportError:  # pragma: no cover - package-style import
    from scripts.evaluate_task import evaluate_task
    from scripts.leaderboard_aggregate import AggregateValidationError, validate_aggregate
    from scripts.release_lock import ReleaseLockError, load_release_lock, verify_task_release_artifacts
    from scripts.validate_benchmark import EXPECTED_TASK_IDS, validate_schema_instance


ROOT = Path(__file__).resolve().parents[1]
PROBES_PATH = ROOT / "data" / "validity-probes.json"
PROBES_SCHEMA_PATH = ROOT / "schemas" / "validity-probes.schema.json"
LEADERBOARD_OUTPUT_SCHEMA_PATH = ROOT / "schemas" / "leaderboard-output.schema.json"


class GateError(ValueError):
    """Raised when the validity gate cannot establish a required invariant."""


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"unable to read {label}: {type(exc).__name__}: {exc}") from exc


def _fixture_path(task_id: str) -> Path:
    filename = "request-packet.json" if task_id == "COS-01" else "input.json"
    return ROOT / "fixtures" / task_id.lower() / filename


def _get_path(value: Any, path: list[Any]) -> Any:
    current = value
    for part in path:
        if isinstance(current, dict) and isinstance(part, str) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            raise GateError(f"mutation path does not exist: {path!r}")
    return current


def _get_parent(value: Any, path: list[Any]) -> tuple[Any, Any]:
    if not path:
        raise GateError("mutation path must not be empty")
    return _get_path(value, path[:-1]), path[-1]


def _same_json(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _apply_mutation(candidate: Any, mutation: dict[str, Any]) -> None:
    operation = mutation.get("operation")
    path = mutation.get("path")
    if not isinstance(path, list) or not path:
        raise GateError("probe mutation path must be a non-empty array")
    parent, key = _get_parent(candidate, path)
    if operation == "replace":
        if "from" not in mutation or "to" not in mutation:
            raise GateError("replace mutation must declare from and to")
        if isinstance(parent, dict) and isinstance(key, str):
            current = parent.get(key)
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            current = parent[key]
        else:
            raise GateError(f"replace mutation has invalid parent/key: {path!r}")
        if not _same_json(current, mutation["from"]):
            raise GateError(f"replace mutation source mismatch at {path!r}: {current!r}")
        if isinstance(parent, dict) and isinstance(key, str):
            parent[key] = mutation["to"]
        elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
            parent[key] = mutation["to"]
        else:
            raise GateError(f"replace mutation has invalid parent/key: {path!r}")
        return
    if operation == "rename_key":
        target = _get_path(candidate, path)
        if not isinstance(target, dict):
            raise GateError("rename_key mutation must target an object")
        old_key = mutation.get("from")
        new_key = mutation.get("to")
        if not isinstance(old_key, str) or not isinstance(new_key, str) or not old_key or not new_key:
            raise GateError("rename_key mutation must declare non-empty string from/to")
        if old_key not in target:
            raise GateError(f"rename_key source is absent at {path!r}: {old_key!r}")
        if new_key in target:
            raise GateError(f"rename_key target already exists at {path!r}: {new_key!r}")
        target[new_key] = target.pop(old_key)
        return
    raise GateError(f"unsupported probe mutation operation: {operation!r}")


def _validate_probe_catalogue(probes: Any) -> list[str]:
    schema = _load_json(PROBES_SCHEMA_PATH, "validity probe schema")
    if not isinstance(schema, dict):
        return ["validity probe schema must be an object"]
    errors = validate_schema_instance(probes, schema)
    if errors:
        return [f"probe schema: {error}" for error in errors]
    if not isinstance(probes, dict) or probes.get("benchmark_version") != "0.6.0":
        return ["validity probes must belong to benchmark version 0.6.0"]
    return []


def _control_result(task_id: str, condition: str) -> dict[str, Any]:
    task_root = ROOT / "fixtures" / task_id.lower()
    fixture = _load_json(_fixture_path(task_id), f"{task_id} fixture")
    candidate = _load_json(
        task_root / "controls" / ("known-good.json" if condition == "known-good-control" else "known-bad.json"),
        f"{task_id} {condition}",
    )
    expected = "passed" if condition == "known-good-control" else "failed"
    result = evaluate_task(task_id, fixture, candidate, model_output=False)
    if result.get("status") != expected:
        raise GateError(f"{task_id} {condition} returned {result.get('status')!r}, expected {expected!r}")
    if result.get("status") == "blocked":
        raise GateError(f"{task_id} {condition} was blocked")
    return result


def validate_controls() -> dict[str, int]:
    good = 0
    bad = 0
    for task_id in sorted(EXPECTED_TASK_IDS):
        _control_result(task_id, "known-good-control")
        good += 1
        _control_result(task_id, "known-bad-control")
        bad += 1
    return {"known_good": good, "known_bad": bad, "total": good + bad}


def validate_probes(probes: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for probe in probes["probes"]:
        task_id = probe["task_id"]
        task_root = ROOT / "fixtures" / task_id.lower()
        fixture = _load_json(_fixture_path(task_id), f"{task_id} fixture")
        candidate = _load_json(task_root / "controls" / "known-good.json", f"{task_id} known-good control")
        mutated = copy.deepcopy(candidate)
        for mutation in probe["mutations"]:
            _apply_mutation(mutated, mutation)
        result = evaluate_task(task_id, fixture, mutated, model_output=False)
        expected_status = probe["expected_status"]
        if result.get("status") != expected_status:
            raise GateError(
                f"probe {probe['id']} returned {result.get('status')!r}, expected {expected_status!r}"
            )
        expected_check = probe["expected_check"]
        check = next(
            (item for item in result.get("automatic_checks", []) if item.get("id") == expected_check["id"]),
            None,
        )
        if not isinstance(check, dict) or check.get("status") != expected_check["status"]:
            raise GateError(
                f"probe {probe['id']} check {expected_check['id']!r} did not return "
                f"{expected_check['status']!r}"
            )
        expected_hard = set(probe.get("expected_hard_failure_ids", []))
        actual_hard = {
            item.get("id")
            for item in result.get("hard_failures", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if expected_hard and actual_hard != expected_hard:
            raise GateError(
                f"probe {probe['id']} hard failures {sorted(actual_hard)!r}, "
                f"expected {sorted(expected_hard)!r}"
            )
        results.append({"id": probe["id"], "status": result["status"], "check": check["status"]})
    return {"total": len(results), "results": results}


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Check a built leaderboard against its output schema and aggregate identities."""
    schema = _load_json(LEADERBOARD_OUTPUT_SCHEMA_PATH, "leaderboard output schema")
    schema_errors = validate_schema_instance(report, schema)
    if schema_errors:
        raise GateError("leaderboard violates its schema: " + "; ".join(schema_errors[:4]))
    try:
        validate_aggregate(report["aggregate"])
    except AggregateValidationError as exc:
        raise GateError(str(exc)) from exc
    aggregate = report["aggregate"]
    return {"attempted_runs": aggregate["attempted_runs"], "comparable_runs": aggregate["comparable_resolved_runs"]}


def validate_trusted_release() -> dict[str, Any]:
    """Establish the sealed release boundary before executing any control."""
    try:
        lock = load_release_lock()
        bindings = {
            task_id: verify_task_release_artifacts(task_id, "local-replay")
            for task_id in sorted(EXPECTED_TASK_IDS)
        }
    except (KeyError, OSError, ReleaseLockError, TypeError, UnicodeError) as exc:
        raise GateError(f"trusted release could not be verified: {exc}") from exc
    return {"lock_version": lock["lock_version"], "tasks": len(bindings)}


def run_gate(report: Path | None) -> dict[str, Any]:
    release = validate_trusted_release()
    probes = _load_json(PROBES_PATH, "validity probe catalogue")
    schema_errors = _validate_probe_catalogue(probes)
    if schema_errors:
        raise GateError("; ".join(schema_errors))
    if not isinstance(probes, dict):
        raise GateError("validity probe catalogue must be an object")
    controls = validate_controls()
    probe_results = validate_probes(probes)
    report_result = validate_report(_load_json(report, "leaderboard report")) if report else None
    return {
        "gate": "zero-model-call-validity-v05",
        "benchmark_version": "0.6.0",
        "model_calls_issued": 0,
        "trusted_release": release,
        "controls": controls,
        "probes": probe_results,
        "report": report_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_gate(args.report)
    except (GateError, OSError, ValueError) as exc:
        print(f"validity gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
