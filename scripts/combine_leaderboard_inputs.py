#!/usr/bin/env python3
"""Combine replicate/recovery manifests whose paths share one evidence root."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

try:
    from build_leaderboard import LeaderboardInputError, _load_input, _load_json, _write_json
except ImportError:  # pragma: no cover - package-style import
    from scripts.build_leaderboard import LeaderboardInputError, _load_input, _load_json, _write_json

ROOT = Path(__file__).resolve().parents[1]


def combine_inputs(root: Path, input_paths: list[Path]) -> dict:
    """Keep all attempts, with deterministic ordering and a canonical roster path."""
    if not input_paths:
        raise LeaderboardInputError('at least one input manifest is required')
    manifests = []
    for path in input_paths:
        path = path if path.is_absolute() else root / path
        manifest = _load_json(path, 'leaderboard input manifest')
        if manifests:
            for key in ('benchmark_id', 'benchmark_version', 'snapshot_id'):
                if manifest.get(key) != manifests[0][0][key]:
                    raise LeaderboardInputError(f'mismatched {key} across input manifests')
        manifest, roster = _load_input(path, root, manifest['benchmark_id'], manifest['benchmark_version'])
        if manifests and roster != manifests[0][1]:
            raise LeaderboardInputError('mismatched roster across input manifests')
        manifests.append((manifest, roster))
    first, _ = manifests[0]
    output = {key: first[key] for key in ('schema_version', 'benchmark_id', 'benchmark_version', 'snapshot_id')}
    output['roster_path'] = min(manifest['roster_path'] for manifest, _ in manifests)
    output['runs'] = sorted((run for manifest, _ in manifests for run in manifest['runs']),
                            key=lambda run: (run['run_id'], run['record_path']))
    seen = set()
    seen_paths = set()
    for run in output['runs']:
        if run['run_id'] in seen:
            raise LeaderboardInputError(f"duplicate run ID {run['run_id']!r}")
        if run['record_path'] in seen_paths:
            raise LeaderboardInputError(f"duplicate record path {run['record_path']!r}")
        seen.add(run['run_id'])
        seen_paths.add(run['record_path'])
    timestamps = [manifest['generated_at'] for manifest, _ in manifests if 'generated_at' in manifest]
    if timestamps:
        output['generated_at'] = max(timestamps)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='root for all manifest evidence paths')
    parser.add_argument('--inputs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = combine_inputs(args.root.resolve(), args.inputs)
        _write_json(args.output if args.output.is_absolute() else args.root / args.output, output)
    except (LeaderboardInputError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'combine failed: {exc}', file=sys.stderr)
        return 2
    print(f"Combined {len(args.inputs)} manifests; {len(output['runs'])} runs.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
