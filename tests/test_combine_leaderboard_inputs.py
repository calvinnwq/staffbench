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
            'benchmark_version': '0.5.0', 'snapshot_id': 'test-snapshot',
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
                        'benchmark_version': '0.5.0', 'snapshot_id': 'test-snapshot',
                        'roster_path': f'{replicate}/roster.json', 'runs': runs}
            path = directory / 'leaderboard-input.json'
            path.write_text(json.dumps(manifest))
            self.inputs.append(path)

    def run_combine(self, inputs=None):
        return subprocess.run([sys.executable, '-S', str(SCRIPT), '--root', str(self.root),
                               '--inputs', *map(str, inputs or self.inputs), '--output', 'combined.json'],
                              capture_output=True, text=True)

    def test_combines_original_and_recovery_runs_byte_deterministically(self):
        result = self.run_combine()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = (self.root / 'combined.json').read_bytes()
        manifest = json.loads(output)
        self.assertEqual([run['run_id'] for run in manifest['runs']], ['run-a', 'run-b', 'run-c', 'run-d'])
        self.assertEqual(manifest['roster_path'], 'r1/roster.json')
        self.assertEqual(manifest['snapshot_id'], 'test-snapshot')
        self.assertEqual(self.run_combine(list(reversed(self.inputs))).returncode, 0)
        self.assertEqual((self.root / 'combined.json').read_bytes(), output)

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

    def test_refuses_duplicate_run_ids_in_different_replicates(self):
        path = self.inputs[1]
        manifest = json.loads(path.read_text())
        manifest['runs'][0]['run_id'] = 'run-a'
        path.write_text(json.dumps(manifest))
        result = self.run_combine()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate run ID', result.stderr)
        self.assertFalse((self.root / 'combined.json').exists())

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
