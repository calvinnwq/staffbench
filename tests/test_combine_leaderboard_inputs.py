"""Combine replicate manifests without losing original or recovery attempts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/combine_leaderboard_inputs.py'


class CombineInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = []
        roster = {
            'schema_version': 'model-roster-v1', 'benchmark_id': 'staffbench',
            'benchmark_version': '0.6.0', 'snapshot_id': 'test-snapshot',
            'provider': 'test-provider', 'captured_at': '2026-01-01T00:00:00Z',
            'models': [{'model_id': 'test/model', 'requested_model_id': 'test/model',
                        'resolved_model_id': 'test/model', 'provider_requested': 'test-provider',
                        'provider_resolved': 'test-provider', 'availability': 'eligible'}],
        }
        for replicate, run_ids in [('r1', ['run-b', 'run-a']), ('r2', ['run-c']), ('r2-recovery', ['run-d'])]:
            directory = self.root / replicate
            directory.mkdir()
            (directory / 'roster.json').write_text(json.dumps(roster))
            runs = []
            for run_id in run_ids:
                path = directory / (run_id + '.json')
                path.write_text('{}')
                runs.append({'run_id': run_id, 'record_path': path.relative_to(self.root).as_posix()})
            manifest = {'schema_version': 'leaderboard-input-v1', 'benchmark_id': 'staffbench',
                        'benchmark_version': '0.6.0', 'snapshot_id': 'test-snapshot',
                        'roster_path': f'{replicate}/roster.json', 'runs': runs}
            path = directory / 'leaderboard-input.json'
            path.write_text(json.dumps(manifest))
            self.inputs.append(path)

    def run_combine(self, inputs=None, extra_args=()):
        return subprocess.run([sys.executable, '-S', str(SCRIPT), '--root', str(self.root),
                               '--inputs', *map(str, inputs or self.inputs), '--output', 'combined.json',
                               *extra_args],
                              capture_output=True, text=True)

    def test_opt_in_combines_snapshots_into_a_valid_deterministic_roster_union(self):
        from scripts.build_leaderboard import _load_input

        path = self.inputs[1]
        manifest = json.loads(path.read_text())
        manifest['snapshot_id'] = 'later-snapshot'
        path.write_text(json.dumps(manifest))
        roster_path = self.root / manifest['roster_path']
        roster = json.loads(roster_path.read_text())
        roster['snapshot_id'] = 'later-snapshot'
        roster['captured_at'] = '2026-01-02T00:00:00Z'
        roster['models'].append(dict(roster['models'][0], model_id='test/new',
                                     requested_model_id='test/new', resolved_model_id='test/new'))
        roster_path.write_text(json.dumps(roster))

        args = ['--combined-snapshot-id', 'combined-snapshot']
        result = self.run_combine(extra_args=args)
        self.assertEqual(result.returncode, 0, result.stderr)
        combined, union = _load_input(self.root / 'combined.json', self.root, 'staffbench', '0.6.0')
        self.assertEqual(combined['snapshot_id'], 'combined-snapshot')
        self.assertEqual(union['snapshot_id'], 'combined-snapshot')
        self.assertEqual(union['captured_at'], '2026-01-02T00:00:00Z')
        self.assertEqual([model['model_id'] for model in union['models']], ['test/model', 'test/new'])
        self.assertEqual(len(combined['runs']), 4)
        sources = json.loads((self.root / 'combined.sources.json').read_text())
        self.assertEqual(sources['snapshot_id'], 'combined-snapshot')
        self.assertEqual(sources['source_snapshot_ids'], ['later-snapshot', 'test-snapshot'])
        self.assertEqual(len(sources['inputs']), 3)
        outputs = {p.name: p.read_bytes() for p in self.root.glob('combined*.json')}
        self.assertEqual(self.run_combine(list(reversed(self.inputs)), args).returncode, 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.glob('combined*.json')}, outputs)

    def test_opt_in_refuses_conflicting_model_identity_without_writing_outputs(self):
        path = self.root / 'r2/roster.json'
        roster = json.loads(path.read_text())
        for changes in [{'requested_model_id': 'test/different-request'},
                        {'resolved_model_id': 'test/different-resolution',
                         'availability': 'excluded', 'exclusion_reason': 'unresolved identity'},
                        {'provider_resolved': 'different-provider'}]:
            with self.subTest(changes=changes):
                changed = dict(roster, models=[dict(roster['models'][0], **changes)])
                path.write_text(json.dumps(changed))
                result = self.run_combine(extra_args=['--combined-snapshot-id', 'combined-snapshot'])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('conflicting roster model', result.stderr)
                self.assertEqual(list(self.root.glob('combined*.json')), [])

    def test_combines_original_and_recovery_runs_byte_deterministically(self):
        result = self.run_combine()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = (self.root / 'combined.json').read_bytes()
        manifest = json.loads(output)
        self.assertEqual([run['run_id'] for run in manifest['runs']], ['run-a', 'run-b', 'run-c', 'run-d'])
        self.assertEqual(manifest['roster_path'], 'r1/roster.json')
        self.assertEqual(manifest['snapshot_id'], 'test-snapshot')
        expected = {'schema_version': 'leaderboard-input-v1', 'benchmark_id': 'staffbench',
                    'benchmark_version': '0.6.0', 'snapshot_id': 'test-snapshot',
                    'roster_path': 'r1/roster.json', 'runs': [
                        {'run_id': run_id, 'record_path': f'{directory}/{run_id}.json'}
                        for run_id, directory in [('run-a', 'r1'), ('run-b', 'r1'),
                                                  ('run-c', 'r2'), ('run-d', 'r2-recovery')]]}
        self.assertEqual(output, (json.dumps(expected, indent=2, sort_keys=True) + '\n').encode())
        self.assertEqual([p.name for p in self.root.glob('combined*.json')], ['combined.json'])
        self.assertEqual(self.run_combine(list(reversed(self.inputs))).returncode, 0)
        self.assertEqual((self.root / 'combined.json').read_bytes(), output)

    def test_opt_in_refuses_duplicate_requested_identity_across_models(self):
        path = self.root / 'r2/roster.json'
        roster = json.loads(path.read_text())
        roster['models'][0].update(model_id='test/new', resolved_model_id='test/new')
        path.write_text(json.dumps(roster))
        result = self.run_combine(extra_args=['--combined-snapshot-id', 'combined-snapshot'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate requested model ID', result.stderr)
        self.assertEqual(list(self.root.glob('combined*.json')), [])

    def test_refuses_mismatched_comparison_identity_without_writing_output(self):
        path = self.inputs[1]
        original = json.loads(path.read_text())
        for key in ('snapshot_id', 'benchmark_id', 'benchmark_version'):
            with self.subTest(field=key):
                manifest = dict(original, **{key: 'different'})
                path.write_text(json.dumps(manifest))
                result = self.run_combine()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertFalse((self.root / 'combined.json').exists())

    def test_opt_in_refuses_inconsistent_snapshot_metadata(self):
        path = self.root / 'r2/roster.json'
        original = json.loads(path.read_text())
        cases = [('provider', 'different-provider', 'combined-snapshot', 'provider'),
                 ('snapshot_id', 'different-snapshot', 'combined-snapshot', 'snapshot_id'),
                 ('snapshot_id', 'test-snapshot', 'test-snapshot', 'new snapshot ID')]
        for key, value, snapshot_id, message in cases:
            with self.subTest(field=key, snapshot_id=snapshot_id):
                path.write_text(json.dumps(dict(original, **{key: value})))
                result = self.run_combine(extra_args=['--combined-snapshot-id', snapshot_id])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(list(self.root.glob('combined*.json')), [])

    def test_opt_in_keeps_manifest_integrity_guards(self):
        path = self.inputs[1]
        original = path.read_text()
        cases = [('benchmark_id', 'different', 'benchmark_id'),
                 ('benchmark_version', 'different', 'benchmark_version'),
                 ('run_id', 'run-a', 'duplicate run ID'),
                 ('record_path', 'r1/run-a.json', 'duplicate record path')]
        for key, value, message in cases:
            with self.subTest(field=key):
                manifest = json.loads(original)
                if key in ('run_id', 'record_path'):
                    manifest['runs'][0][key] = value
                else:
                    manifest[key] = value
                path.write_text(json.dumps(manifest))
                result = self.run_combine(extra_args=['--combined-snapshot-id', 'combined-snapshot'])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(list(self.root.glob('combined*.json')), [])

    def test_refuses_duplicate_run_ids_in_different_replicates(self):
        path = self.inputs[1]
        manifest = json.loads(path.read_text())
        manifest['runs'][0]['run_id'] = 'run-a'
        path.write_text(json.dumps(manifest))
        result = self.run_combine()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate run ID', result.stderr)
        self.assertFalse((self.root / 'combined.json').exists())

    def test_opt_in_never_overwrites_source_evidence(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        for target in ['r1/leaderboard-input.json', 'r1/roster.json', 'r1/run-a.json']:
            with self.subTest(target=target):
                result = self.run_combine(extra_args=['--combined-snapshot-id', 'combined-snapshot',
                                                       '--output', target])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('overwrite source evidence', result.stderr)
                self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*.json')}, before)

    def test_refuses_changed_roster_in_a_replicate(self):
        path = self.root / 'r2/roster.json'
        roster = json.loads(path.read_text())
        roster['models'][0]['provider_requested'] = 'different-provider'
        path.write_text(json.dumps(roster))
        result = self.run_combine()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('roster', result.stderr)
        self.assertFalse((self.root / 'combined.json').exists())

    def test_refuses_shared_record_path_across_manifests(self):
        path = self.inputs[1]
        manifest = json.loads(path.read_text())
        manifest['runs'][0]['record_path'] = 'r1/run-a.json'
        path.write_text(json.dumps(manifest))
        result = self.run_combine()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate record path', result.stderr)
        self.assertFalse((self.root / 'combined.json').exists())


if __name__ == '__main__':
    unittest.main()
