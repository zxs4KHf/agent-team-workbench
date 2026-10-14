"""Real Git integration: source-only submission -> pinned snapshot -> fresh public repo.
All files, indexes, histories and outputs are disposable fixtures; no service access.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import bus_submission_scope as scope
import public_snapshot as snapshots
import export_public as exporter


class SubmissionExportPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-submission-export-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git(self.repo, 'init', '-b', 'main')
        self.git(self.repo, 'config', 'user.name', 'Pipeline fixture')
        self.git(self.repo, 'config', 'user.email', 'fixture@localhost')
        self.git(self.repo, 'config', 'commit.gpgsign', 'false')
        self.git(self.repo, 'config', 'core.hooksPath', str(self.root / 'no-hooks'))
        self.put('.gitignore', '__pycache__/\nconsole/\n')
        self.put('.gitattributes', '*.bat text eol=crlf\n')
        self.put('start.bat', '@echo off\necho fixture\n')
        self.put('app.py', 'VALUE = 1\n')
        self.put('scripts/export_public.json', json.dumps({'exclude': [], 'version': 'fixture'}))
        self.git(self.repo, 'add', '.gitignore', '.gitattributes', 'start.bat', 'app.py', 'scripts/export_public.json')
        self.git(self.repo, 'commit', '-m', 'fixture baseline')
        self.base = self.git(self.repo, 'rev-parse', 'HEAD').strip()

    def put(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def git(self, repo, *args):
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                                env=snapshots.git_env(), timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        return result.stdout.decode('utf-8')

    def commit_changes(self):
        self.assertTrue(scope.stage_source_changes(self.repo, self.base))
        self.git(self.repo, 'commit', '-m', 'source-only fixture change')
        return self.git(self.repo, 'rev-parse', 'HEAD').strip()

    def test_submission_snapshot_and_new_history_preserve_exact_source(self):
        self.put('src/示例 module.py', 'VALUE = 2\n')
        self.git(self.repo, 'add', 'src/示例 module.py')
        sha = self.commit_changes()
        self.put('app.py', 'VALUE = 999\n')
        self.put('scripts/staged.py', 'NOT_YET_SUBMITTED = True\n')
        self.git(self.repo, 'add', 'scripts/staged.py')
        self.put('console/local.json', '{"synthetic": true}')
        snapshot = self.root / 'snapshot'
        result = snapshots.materialize(self.repo, sha, snapshot)
        self.assertEqual(result['commit'], sha)
        self.assertEqual((snapshot / 'app.py').read_text(), 'VALUE = 1\n')
        self.assertNotIn('scripts/staged.py', result['files'])
        self.assertNotIn('console/local.json', result['files'])
        self.assertEqual(exporter.scan(list(result['files']), str(snapshot), exporter.BUILTIN), [])
        output = self.root / 'public'
        exporter.export(str(output), list(result['files']), str(snapshot), 'Fixture <fixture@example.com>', 'fixture')
        self.assertEqual(self.git(output, 'rev-list', '--count', 'HEAD').strip(), '1')
        # Compare committed bytes, not a text helper's Windows newline assumption.
        # Every exported blob and mode must match the fixed source tree exactly.
        for name, info in result['files'].items():
            source_oid = self.git(self.repo, 'rev-parse', sha + ':' + name).strip()
            public_oid = self.git(output, 'rev-parse', 'HEAD:' + name).strip()
            self.assertEqual(public_oid, source_oid, name)
            mode = self.git(output, 'ls-tree', 'HEAD', '--', name).split()[0]
            self.assertEqual(mode, info['mode'], name)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD').strip(), sha)
        content = (output / 'start.bat').read_bytes()
        self.assertEqual(content.count(b'\n'), content.count(b'\r\n'))
        self.assertIn('scripts/staged.py', self.git(self.repo, 'diff', '--cached', '--name-only'))

    def test_rejected_index_can_be_classified_then_exported(self):
        self.put('capture.backup', 'synthetic note')
        self.git(self.repo, 'add', 'capture.backup')
        before = self.git(self.repo, 'diff', '--cached', '--binary')
        with self.assertRaises(ValueError):
            scope.stage_source_changes(self.repo, self.base)
        self.assertEqual(self.git(self.repo, 'diff', '--cached', '--binary'), before)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD').strip(), self.base)
        (self.repo / 'docs').mkdir()
        self.git(self.repo, 'mv', 'capture.backup', 'docs/fixture-note.md')
        sha = self.commit_changes()
        result = snapshots.materialize(self.repo, sha, self.root / 'snapshot')
        self.assertIn('docs/fixture-note.md', result['files'])
        self.assertNotIn('capture.backup', result['files'])
        self.assertEqual((self.root / 'snapshot/docs/fixture-note.md').read_text(), 'synthetic note')

    def test_legacy_runtime_in_baseline_is_retained_locally_but_excluded(self):
        self.put('feedback/fixture.json', '{}')
        self.put('profiles/fixture.json', '{}')
        self.git(self.repo, 'add', 'feedback/fixture.json', 'profiles/fixture.json')
        self.git(self.repo, 'commit', '-m', 'simulated historical mistake')
        self.base = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.put('app.py', 'VALUE = 2\n')
        sha = self.commit_changes()
        result = snapshots.materialize(self.repo, sha, self.root / 'snapshot')
        for name in ['feedback/fixture.json', 'profiles/fixture.json']:
            self.assertNotIn(name, result['files'])
            self.assertEqual((self.repo / name).read_text(), '{}')

    def test_export_with_foreign_git_environment_preserves_source_index(self):
        self.put('app.py', 'VALUE = 2\n')
        sha = self.commit_changes()
        before = (self.repo / '.git/index').read_bytes()
        with mock.patch.dict(os.environ, {'GIT_DIR': str(self.repo / '.git'),
                                        'GIT_WORK_TREE': str(self.repo),
                                        'GIT_INDEX_FILE': str(self.repo / '.git/index')}):
            snapshot = self.root / 'snapshot'
            result = snapshots.materialize(self.repo, sha, snapshot)
            exporter.export(str(self.root / 'public'), list(result['files']), str(snapshot),
                            'Fixture <fixture@example.com>', 'fixture')
        self.assertEqual((self.repo / '.git/index').read_bytes(), before)
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD').strip(), sha)
        self.assertEqual((self.repo / 'app.py').read_text(), 'VALUE = 2\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
