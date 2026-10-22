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


def combine_inputs(root: Path, input_paths: list[Path], *,
                   combined_snapshot_id: str | None = None, output_path: Path | None = None) -> dict:
    """Keep all attempts; opt-in snapshot unions also write roster/provenance sidecars."""
    if not input_paths:
        raise LeaderboardInputError('at least one input manifest is required')
    manifests = []
    sources = []
    source_paths = set()
    for path in input_paths:
        path = path if path.is_absolute() else root / path
        manifest = _load_json(path, 'leaderboard input manifest')
        if manifests:
            keys = ('benchmark_id', 'benchmark_version')
            if combined_snapshot_id is None:
                keys += ('snapshot_id',)
            for key in keys:
                if manifest.get(key) != manifests[0][0][key]:
                    raise LeaderboardInputError(f'mismatched {key} across input manifests')
        manifest, roster = _load_input(path, root, manifest['benchmark_id'], manifest['benchmark_version'])
        if combined_snapshot_id is None and manifests and roster != manifests[0][1]:
            raise LeaderboardInputError('mismatched roster across input manifests')
        if combined_snapshot_id is not None:
            if roster['snapshot_id'] != manifest['snapshot_id']:
                raise LeaderboardInputError('roster snapshot_id does not match its input manifest')
            if manifests and roster['provider'] != manifests[0][1]['provider']:
                raise LeaderboardInputError('mismatched roster provider across input manifests')
        manifests.append((manifest, roster))
        if combined_snapshot_id is not None:
            sources.append({'snapshot_id': manifest['snapshot_id'],
                            'input_path': path.resolve().relative_to(root.resolve()).as_posix()})
            source_paths.update([path.resolve(), (root / manifest['roster_path']).resolve()])
            source_paths.update((root / run['record_path']).resolve() for run in manifest['runs'])
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
    if combined_snapshot_id is not None:
        if not combined_snapshot_id.strip() or output_path is None:
            raise LeaderboardInputError('combined snapshot needs a non-empty ID and output path')
        if combined_snapshot_id in {manifest['snapshot_id'] for manifest, _ in manifests}:
            raise LeaderboardInputError('combined snapshot needs a new snapshot ID')
        output_path = (root / output_path).resolve()
        roster_path = output_path.with_suffix('.roster.json')
        sources_path = output_path.with_suffix('.sources.json')
        if source_paths.intersection((output_path, roster_path, sources_path)):
            raise LeaderboardInputError('combined outputs would overwrite source evidence')
        output['snapshot_id'] = combined_snapshot_id
        output['roster_path'] = roster_path.relative_to(root.resolve()).as_posix()
        models = {}
        for _, roster in manifests:
            for model in roster['models']:
                model_id = model['model_id']
                if model_id in models and model != models[model_id]:
                    raise LeaderboardInputError(f'conflicting roster model {model_id!r}')
                models[model_id] = model
        requested = [model['requested_model_id'] for model in models.values()]
        if len(requested) != len(set(requested)):
            raise LeaderboardInputError('combined roster contains duplicate requested model ID')
        roster = {key: manifests[0][1][key] for key in
                  ('schema_version', 'benchmark_id', 'benchmark_version', 'provider')}
        roster.update(snapshot_id=combined_snapshot_id,
                      captured_at=max(item['captured_at'] for _, item in manifests),
                      models=[models[key] for key in sorted(models)])
        _write_json(roster_path, roster)
        _write_json(sources_path, {
            'snapshot_id': combined_snapshot_id,
            'source_snapshot_ids': sorted({item['snapshot_id'] for item in sources}),
            'inputs': sorted(sources, key=lambda item: (item['snapshot_id'], item['input_path'])),
        })
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='root for all manifest evidence paths')
    parser.add_argument('--inputs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--combined-snapshot-id', help='opt in to a roster union under a new snapshot ID; '
                        'write .roster.json and .sources.json beside the output')
    args = parser.parse_args(argv)
    try:
        output = combine_inputs(args.root.resolve(), args.inputs,
                                combined_snapshot_id=args.combined_snapshot_id, output_path=args.output)
        _write_json(args.output if args.output.is_absolute() else args.root / args.output, output)
    except (LeaderboardInputError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'combine failed: {exc}', file=sys.stderr)
        return 2
    print(f"Combined {len(args.inputs)} manifests; {len(output['runs'])} runs.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
