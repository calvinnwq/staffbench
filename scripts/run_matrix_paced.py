#!/usr/bin/env python3
"""Paced, rate-limit-aware matrix driver: one roster x the task ledger x N replicates.

Every cell runs through the sealed single-cell runner with exactly the arguments
run_leaderboard_matrix.py passes (same prompt, fixture, adapter, reasoning and timeout).
Only scheduling differs:

* a fixed gap between cell launches;
* an optional rate-limit probe before every launch; on HTTP 429 the driver sleeps for the
  probe's retry_after plus a margin, and on a fair-share budget below half it waits for top-up;
* bounded recovery reruns of failed or unresolved cells, with new run IDs in a separate
  ``<prefix>-<replicate>-recovery`` root. Original failed cells are always kept.

The probe is any command that takes the model ID as its last argument and prints one JSON
object on its last stdout line: ``{"status": 200 | 429 | null, "retry_after": seconds,
"headers": {...}, "error": "..."}``. It receives the Hermes home in STAFFBENCH_HERMES_HOME.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, TextIO

try:
    from run_leaderboard_matrix import (
        INPUT_SCHEMA,
        ROSTER_SCHEMA,
        MatrixInputError,
        _load_json,
        _relative_path,
        _run_id,
        _run_release_gate,
        _task_input_paths,
        _validate_cell_record,
        _validate_schema,
        _write_json,
        build_input_manifest,
        build_matrix_plan,
        cell_command,
        resolve_hermes_home,
    )
except ImportError:  # pragma: no cover - package-style import
    from scripts.run_leaderboard_matrix import (
        INPUT_SCHEMA,
        ROSTER_SCHEMA,
        MatrixInputError,
        _load_json,
        _relative_path,
        _run_id,
        _run_release_gate,
        _task_input_paths,
        _validate_cell_record,
        _validate_schema,
        _write_json,
        build_input_manifest,
        build_matrix_plan,
        cell_command,
        resolve_hermes_home,
    )


ROOT = Path(__file__).resolve().parents[1]
RATE_LIMIT_MARGIN_SECONDS = 30
DEFAULT_RETRY_AFTER_SECONDS = 300
TOP_UP_MARGIN_SECONDS = 5


class Deadline(Exception):
    """The next wait would cross the run deadline; stop cleanly and keep what ran."""


def _header_params(value: Any) -> dict[str, float]:
    """Parse structured rate-limit header params such as '"fairshare";r=10;t=30'."""
    params: dict[str, float] = {}
    for part in str(value or "").split(";"):
        key, separator, raw = part.strip().partition("=")
        if separator:
            try:
                params[key.strip()] = float(raw.strip())
            except ValueError:
                continue
    return params


def top_up_seconds(headers: Any) -> float:
    """Seconds to wait until a fair-share token bucket holds at least half its quota."""
    if not isinstance(headers, dict):
        return 0.0
    lowered = {str(key).lower(): value for key, value in headers.items()}
    remaining = _header_params(lowered.get("ratelimit")).get("r")
    policy = _header_params(lowered.get("ratelimit-policy"))
    quota, window = policy.get("q"), policy.get("w")
    if remaining is None or not quota or window is None or remaining >= quota / 2:
        return 0.0
    return (quota / 2 - remaining) / quota * window + TOP_UP_MARGIN_SECONDS


def _run_probe(probe_command: str, model_id: str, hermes_home: Path | None) -> dict[str, Any]:
    env = dict(os.environ)
    if hermes_home is not None:
        env["STAFFBENCH_HERMES_HOME"] = str(hermes_home)
    try:
        result = subprocess.run(
            [*shlex.split(probe_command), model_id], capture_output=True, text=True, check=False, env=env, timeout=300
        )
        probe = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"status": None, "error": f"probe failed ({type(exc).__name__})"}
    return probe if isinstance(probe, dict) else {"status": None, "error": "probe output is not an object"}


def run_paced(
    *,
    root: Path,
    roster_path: Path,
    output_prefix: Path,
    snapshot_id: str,
    replicates: list[str] | tuple[str, ...] = ("r1", "r2", "r3"),
    reasoning: str = "medium",
    timeout_seconds: int = 600,
    gap_seconds: float = 20,
    max_retries: int = 2,
    max_sleep_seconds: float = 3600,
    probe_error_wait_seconds: float = 120,
    deadline_hours: float = 14,
    probe_command: str | None = None,
    agent_command: str | None = None,
    hermes_home: Path | str | None = None,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log_stream: TextIO | None = None,
) -> dict[str, Any]:
    """Run every planned cell with pacing and bounded recovery; return the driver summary."""
    if timeout_seconds <= 0 or gap_seconds < 0 or max_retries < 0:
        raise MatrixInputError("timeout must be positive; gap and retries must not be negative")
    root = root.resolve()
    prefix = _relative_path(root, output_prefix, "output prefix")
    ledger = _load_json(root / "data" / "task-ledger.json", "benchmark ledger")
    roster_file = _relative_path(root, roster_path, "roster path")
    roster = _load_json(roster_file, "model roster")
    _validate_schema(roster, ROSTER_SCHEMA, "model roster")
    task_inputs = _task_input_paths(root, ledger)

    roots: dict[tuple[str, str], Path] = {}
    for replicate in replicates:
        for suffix in ("", "-recovery"):
            path = Path(f"{prefix}-{replicate}{suffix}")
            if path.exists() and any(path.iterdir()):
                raise MatrixInputError(f"output root is not empty: {path.relative_to(root).as_posix()}")
            roots[(replicate, suffix)] = path
    plans = {
        replicate: build_matrix_plan(
            roster,
            ledger,
            snapshot_id,
            roots[(replicate, "")].relative_to(root),
            task_inputs=task_inputs,
            replicate_id=replicate,
        )
        for replicate in replicates
    }
    if dry_run:
        return {
            "dry_run": True,
            "planned_cells": sum(len(plan) for plan in plans.values()),
            "replicates": {replicate: len(plan) for replicate, plan in plans.items()},
        }

    home = None if agent_command is not None and hermes_home is None else resolve_hermes_home(hermes_home)
    _run_release_gate()
    deadline = clock() + deadline_hours * 3600
    log_path = Path(f"{prefix}-driver.log")

    def log(event: str, **data: Any) -> None:
        line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **data})
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if log_stream is not None:
            print(line, file=log_stream, flush=True)

    def wait(seconds: float, why: str) -> None:
        if clock() + seconds > deadline:
            raise Deadline(why)
        log("sleep", seconds=round(seconds, 3), why=why)
        sleep(seconds)

    def wait_for_clearance(model_id: str) -> None:
        assert probe_command is not None
        while True:
            probe = _run_probe(probe_command, model_id, home)
            log("probe", model_id=model_id, **{key: probe.get(key) for key in ("status", "retry_after", "error")})
            if probe.get("status") == 200:
                top_up = top_up_seconds(probe.get("headers"))
                if top_up > 0:
                    wait(top_up, "topping up fair-share budget")
                return
            if probe.get("status") == 429:
                try:
                    retry_after = float(probe.get("retry_after") or DEFAULT_RETRY_AFTER_SECONDS)
                except (TypeError, ValueError):
                    retry_after = DEFAULT_RETRY_AFTER_SECONDS
                wait(min(retry_after, max_sleep_seconds) + RATE_LIMIT_MARGIN_SECONDS, "rate-limited")
            else:
                wait(probe_error_wait_seconds, "probe error")

    def run_cell(cell: dict[str, str], output_root: Path) -> tuple[dict[str, Any] | None, int]:
        command = cell_command(
            root,
            cell,
            output_root,
            reasoning=reasoning,
            timeout_seconds=timeout_seconds,
            hermes_home=home,
            agent_command=agent_command,
        )
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        record_file = output_root / cell["run_id"] / "run-record.json"
        if not record_file.is_file():
            return None, result.returncode
        record = _load_json(record_file, f"cell {cell['run_id']} run record")
        _validate_cell_record(record, cell)
        return record, result.returncode

    completed: dict[Path, list[dict[str, str]]] = {path: [] for path in roots.values()}
    summary: dict[str, Any] = {"snapshot_id": snapshot_id, "replicates": list(replicates), "cells": [], "incomplete": False}
    log("start", replicates=list(replicates), gap_seconds=gap_seconds, timeout_seconds=timeout_seconds,
        max_retries=max_retries, reasoning=reasoning, probe=probe_command is not None)
    launched = False
    try:
        for replicate in replicates:
            original, recovery = roots[(replicate, "")], roots[(replicate, "-recovery")]
            for path in (original, recovery):
                path.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(roster_file, path / "roster.json")
            for cell in plans[replicate]:
                current, output_root, attempt = cell, original, 0
                while True:
                    if launched:
                        wait(gap_seconds, "pacing")
                    if probe_command is not None:
                        wait_for_clearance(cell["model_id"])
                    launched = True
                    record, returncode = run_cell(current, output_root)
                    if record is not None:
                        record_path = output_root / current["run_id"] / "run-record.json"
                        completed[output_root].append(
                            {"run_id": current["run_id"], "record_path": record_path.relative_to(root).as_posix()}
                        )
                    entry = {
                        "run_id": current["run_id"],
                        "task_id": cell["task_id"],
                        "model_id": cell["model_id"],
                        "attempt": attempt,
                        "returncode": returncode,
                        "execution_status": record and record.get("execution_status"),
                        "resolution_status": record and record.get("resolution_status"),
                        "status": record and record.get("status"),
                        "latency_ms": record and record.get("latency_ms"),
                    }
                    summary["cells"].append(entry)
                    log("cell", **entry)
                    failed = (
                        record is None
                        or record.get("execution_status") != "completed"
                        or record.get("resolution_status") != "resolved"
                    )
                    if not failed or attempt >= max_retries:
                        break
                    attempt += 1
                    output_root = recovery
                    current = dict(
                        cell,
                        output_root=recovery.relative_to(root).as_posix(),
                        run_id=_run_id(snapshot_id, cell["model_id"], cell["task_id"], f"{replicate}-retry{attempt}"),
                    )
    except Deadline as exc:
        summary["incomplete"] = True
        log("deadline", why=str(exc))
    finally:
        for output_root, runs in completed.items():
            if not runs:
                continue
            manifest = build_input_manifest(
                benchmark_id=ledger["benchmark_id"],
                benchmark_version=ledger["benchmark_version"],
                snapshot_id=snapshot_id,
                roster_path=(output_root / "roster.json").relative_to(root).as_posix(),
                completed_cells=runs,
            )
            _validate_schema(manifest, INPUT_SCHEMA, "leaderboard input manifest")
            _write_json(output_root / "leaderboard-input.json", manifest)
        _write_json(Path(f"{prefix}-driver-summary.json"), summary)
        log("end", incomplete=summary["incomplete"], attempts=len(summary["cells"]))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paced, rate-limit-aware StaffBench matrix driver.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True,
                        help="repository-relative; roots are <prefix>-<replicate>[-recovery]")
    parser.add_argument("--replicates", nargs="+", default=["r1", "r2", "r3"])
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--gap-seconds", type=float, default=20)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-sleep-seconds", type=float, default=3600)
    parser.add_argument("--probe-error-wait-seconds", type=float, default=120)
    parser.add_argument("--deadline-hours", type=float, default=14)
    parser.add_argument("--probe-command", help="rate-limit probe; receives the model ID as its last argument")
    parser.add_argument("--hermes-home", type=Path, help="Hermes home with provider credentials (default: $STAFFBENCH_HERMES_HOME)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run_paced(
            root=args.root,
            roster_path=args.roster,
            output_prefix=args.output_prefix if args.output_prefix.is_absolute() else args.root / args.output_prefix,
            snapshot_id=args.snapshot_id,
            replicates=args.replicates,
            reasoning=args.reasoning,
            timeout_seconds=args.timeout_seconds,
            gap_seconds=args.gap_seconds,
            max_retries=args.max_retries,
            max_sleep_seconds=args.max_sleep_seconds,
            probe_error_wait_seconds=args.probe_error_wait_seconds,
            deadline_hours=args.deadline_hours,
            probe_command=args.probe_command,
            hermes_home=args.hermes_home,
            dry_run=args.dry_run,
            log_stream=sys.stderr,
        )
    except (MatrixInputError, OSError, ValueError) as exc:
        print(f"paced matrix failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: value for key, value in summary.items() if key != "cells"}, sort_keys=True))
    return 1 if summary.get("incomplete") else 0


if __name__ == "__main__":
    raise SystemExit(main())
