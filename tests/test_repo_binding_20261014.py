"""Initial repository binding: synthetic projects and disposable Git repositories only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import bus_repo_binding as M
except ImportError:
    M = None
import bus_project_store as PS


class RepoBindingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(M, 'local preview/apply repository binding entrypoint is missing')
        self.tmp = tempfile.TemporaryDirectory(prefix='repo-binding-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.projects = self.root / 'projects'
        self.record = self.projects / 'demo' / 'project.json'
        self.record.parent.mkdir(parents=True)
        self.original = {'slug': 'demo', 'name': 'demo', 'repo_dir': '', 'description': 'keep', 'teams': []}
        self.record.write_text(json.dumps(self.original), encoding='utf-8')
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@localhost')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', str(self.root / 'no-hooks'))
        (self.repo / 'a.txt').write_text('fixture', encoding='utf-8')
        self.git('add', 'a.txt')
        self.git('commit', '-m', 'fixture')

    def git(self, *args):
        r = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def preview(self):
        return M.preview(str(self.projects), 'demo', str(self.repo))

    def apply(self, plan):
        return M.apply(str(self.projects), 'demo', str(self.repo), expected=plan['plan_id'],
                       confirm_project='demo', actor='fixture-operator', reason='explicit fixture selection')

    def test_preview_is_read_only_and_does_not_create_mutex(self):
        before = {p.relative_to(self.projects): p.read_bytes() for p in self.projects.rglob('*') if p.is_file()}
        plan = self.preview()
        self.assertEqual(len(plan['plan_id']), 64)
        self.assertEqual(plan['action'], 'bind')
        self.assertEqual(before, {p.relative_to(self.projects): p.read_bytes() for p in self.projects.rglob('*') if p.is_file()})

    def test_apply_preserves_fields_and_records_atomic_receipt(self):
        plan = self.preview()
        result = self.apply(plan)
        self.assertTrue(result['changed'])
        saved = json.loads(self.record.read_text())
        self.assertEqual(saved['description'], 'keep')
        self.assertEqual(saved['repo_dir'], M.canonical(str(self.repo)))
        self.assertEqual(saved['repo_binding_receipt']['plan_id'], plan['plan_id'])
        self.assertEqual(saved['repo_binding_receipt']['previous_repo_dir'], '')
        self.assertEqual(saved['repo_binding_receipt']['actor'], 'fixture-operator')

    def test_lost_response_retry_is_idempotent(self):
        plan = self.preview(); self.apply(plan); before = self.record.read_bytes()
        self.assertFalse(self.apply(plan)['changed'])
        self.assertEqual(before, self.record.read_bytes())

    def test_changed_project_rejects_stale_plan_without_overwrite(self):
        plan = self.preview()
        new = dict(self.original, description='user changed this')
        self.record.write_text(json.dumps(new), encoding='utf-8'); before = self.record.read_bytes()
        with self.assertRaises(M.Conflict): self.apply(plan)
        self.assertEqual(before, self.record.read_bytes())

    def test_new_repository_commit_invalidates_plan(self):
        plan = self.preview(); self.git('commit', '--allow-empty', '-m', 'changed')
        with self.assertRaises(M.Conflict): self.apply(plan)
        self.assertEqual(json.loads(self.record.read_text())['repo_dir'], '')

    def test_explicit_confirmation_and_evidence_required(self):
        plan = self.preview(); before = self.record.read_bytes()
        for field, value in [('expected', ''), ('confirm_project', 'other'), ('actor', ''), ('reason', '')]:
            kw = dict(expected=plan['plan_id'], confirm_project='demo', actor='operator', reason='owner selection')
            kw[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                M.apply(str(self.projects), 'demo', str(self.repo), **kw)
        self.assertEqual(before, self.record.read_bytes())

    def test_refuses_rebinding_without_silently_changing_existing_repository(self):
        self.record.write_text(json.dumps(dict(self.original, repo_dir=str(self.root/'other'))), encoding='utf-8')
        before = self.record.read_bytes()
        with self.assertRaises(ValueError): self.preview()
        self.assertEqual(before, self.record.read_bytes())

    def test_existing_same_binding_is_read_only_noop(self):
        self.record.write_text(json.dumps(dict(self.original, repo_dir=str(self.repo))), encoding='utf-8')
        plan = self.preview(); before = self.record.read_bytes()
        self.assertEqual(plan['action'], 'already_bound')
        self.assertFalse(self.apply(plan)['changed'])
        self.assertEqual(before, self.record.read_bytes())

    def test_invalid_or_missing_project_never_created(self):
        for slug in ('../outside', 'absent'):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                M.preview(str(self.projects), slug, str(self.repo))
        self.assertFalse((self.projects/'absent').exists())

    def test_subdirectory_relative_nonrepo_and_unborn_are_rejected(self):
        sub = self.repo/'sub'; sub.mkdir()
        empty = self.root/'empty'; empty.mkdir()
        subprocess.run(['git','-C',str(empty),'init'], capture_output=True, check=True, timeout=20)
        for value in (str(sub), '.', str(self.root), str(empty)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                M.preview(str(self.projects), 'demo', value)

    def test_worktree_git_file_is_supported(self):
        linked=self.root/'linked'
        self.git('worktree','add','--detach',str(linked),'HEAD')
        self.assertTrue((linked/'.git').is_file())
        plan=M.preview(str(self.projects),'demo',str(linked))
        self.assertEqual(plan['repository']['root'], M.canonical(str(linked)))

    def test_poisoned_git_environment_cannot_redirect_validation(self):
        with mock.patch.dict(os.environ, {'GIT_DIR': str(self.root/'absent'), 'GIT_WORK_TREE': str(self.root), 'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':'core.bare', 'GIT_CONFIG_VALUE_0':'true'}):
            self.assertEqual(self.preview()['repository']['root'], M.canonical(str(self.repo)))

    def test_write_failure_keeps_original_record(self):
        plan=self.preview();before=self.record.read_bytes()
        with mock.patch.object(M,'atomic_write_json',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):self.apply(plan)
        self.assertEqual(before,self.record.read_bytes())

    def test_concurrent_same_apply_commits_once(self):
        plan=self.preview(); barrier=threading.Barrier(2);results=[];errors=[]
        def worker():
            try:barrier.wait(timeout=5);results.append(self.apply(plan)['changed'])
            except Exception as exc:errors.append(repr(exc))
        jobs=[threading.Thread(target=worker) for _ in range(2)]
        for job in jobs:job.start()
        for job in jobs:job.join(15)
        self.assertFalse(any(job.is_alive() for job in jobs))
        self.assertEqual(errors,[]);self.assertEqual(sorted(results),[False,True])

    def test_normal_project_patch_still_cannot_set_repo_dir(self):
        import bus_projects as P
        with mock.patch.object(P,'PROJECTS',str(self.projects)):
            with self.assertRaisesRegex(ValueError,'Unknown project fields'):
                P.update_project('demo', {'repo_dir':str(self.repo)})

    def test_cli_dry_run_and_explicit_apply(self):
        args=[sys.executable,'-B','-X','utf8',str(ROOT/'scripts/bind_project_repo.py'),
              '--projects-dir',str(self.projects),'--project','demo','--repo',str(self.repo)]
        r=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=25)
        self.assertEqual(r.returncode,0,r.stderr);plan=json.loads(r.stdout)
        self.assertEqual(json.loads(self.record.read_text())['repo_dir'],'')
        r=subprocess.run(args+['--apply','--expected',plan['plan_id'],'--confirm-project','demo',
                               '--actor','tester','--reason','owner selected fixture'],capture_output=True,text=True,encoding='utf-8',timeout=25)
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(r.stdout)['changed'])


if __name__ == '__main__': unittest.main(verbosity=2)
