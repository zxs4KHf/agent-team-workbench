"""Goal-aware review uses existing build records and real disposable Git repositories."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_build as BB
import bus_build_safety as S
import bus_core as B
try:
    import bus_review_context as R
except ImportError:
    R = None


class ReviewMethods(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(R, 'Native Humanize-inspired review context is missing')
        self.temp = tempfile.TemporaryDirectory(prefix='review-methods-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'repo'; self.repo.mkdir()
        self.store = self.root/'records'
        for obj, key, value in [(BB,'BUILD_DIR',str(self.store)),(B,'ROOT',str(self.root)),
                                (B,'OUTBOX',str(self.root/'outbox'))]:
            p = mock.patch.object(obj,key,value);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(BB,'run_gates',return_value={'ok':True,'items':[]});p.start();self.addCleanup(p.stop)
        p=mock.patch.object(BB,'policy',side_effect=lambda key,default=None:default);p.start();self.addCleanup(p.stop)
        self.git('init','-b','main');self.git('config','user.name','fixture');self.git('config','user.email','fixture@localhost')
        self.git('config','commit.gpgsign','false');self.git('config','core.hooksPath',str(self.root/'absent-hooks'))
        (self.repo/'.gitignore').write_text('.wt/\n__pycache__/\n')
        (self.repo/'app.py').write_text('VALUE=1\n')
        self.git('add','.gitignore','app.py');self.git('commit','-m','baseline')
        self.base=self.git('rev-parse','HEAD')
        self.brief=R.freeze_brief('Return the greeting in Chinese; do not change authentication.',
                                 [{'path':'docs/plan.md','sha256':'a'*64,'content':'SHOULD_NOT_BE_COPIED'}],
                                 project='fixture',parent_task='')
        self.rec=BB.open_worktrees('fixture-build',['builder'],str(self.repo),review_brief=self.brief)
        self.worker=Path(self.rec['teammates']['builder']['path'])
        (self.worker/'app.py').write_text('VALUE=2\n')
        self.submitted=BB.commit_work('fixture-build','builder','greeting change')
        self.commit=self.submitted['commit']

    def git(self,*args,cwd=None):
        p=subprocess.run(['git','-C',str(cwd or self.repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);return p.stdout.strip()

    def packet(self):return BB.review_pack('fixture-build','builder')

    def test_real_dispatch_freezes_goal_in_existing_build_record(self):
        import bus_projects as P
        project_dir=self.root/'projects'/'fixture';project_dir.mkdir(parents=True)
        (project_dir/'project.json').write_text(json.dumps({'slug':'fixture','name':'fixture','repo_dir':str(self.repo)}))
        patches=[mock.patch.object(P,'PROJECTS',str(self.root/'projects')),
                 mock.patch.object(B,'INBOX',str(self.root/'inbox')),mock.patch.object(B,'PEERS',str(self.root/'peers')),
                 mock.patch.object(B,'LOG',str(self.root/'log')),mock.patch.object(B,'WORKSPACE',str(self.repo)),
                 mock.patch.object(B,'peer_state',return_value={'online':True,'listening':True}),
                 mock.patch.object(B,'role_line_for',return_value=''),mock.patch.object(B,'resolve_context',return_value=[])]
        import contextlib
        with contextlib.ExitStack() as stack:
            for item in patches:stack.enter_context(item)
            result=B.dispatch('caller',['worker2'],'build','Implement the exact requested greeting',[],
                              repo_dir=str(self.repo),project='fixture',substitute=False)
        record=BB.record(result['task_id'])
        self.assertEqual(record['review_brief']['question'],'Implement the exact requested greeting')
        self.assertEqual(record['review_brief']['project'],'fixture')
        self.assertIn('worker2',record['teammates'])
        task=json.loads((self.root/'inbox'/'worker2'/(result['task_id']+'.json')).read_text())
        self.assertEqual(task['question'],record['review_brief']['question'])

    def test_bad_brief_refused_before_creating_worktree(self):
        before=self.git('branch','--list');bad=copy.deepcopy(self.brief);bad['question']='drift'
        with self.assertRaises(ValueError):BB.open_worktrees('reject',['worker3'],str(self.repo),review_brief=bad)
        self.assertEqual(self.git('branch','--list'),before)
        self.assertFalse((self.repo/'.wt/reject').exists())

    def test_builder_receives_goal_guidance_and_discussion_does_not(self):
        task={'id':'demo','from':'leader','to':'builder','mode':'build','deadline':'later',
              'question':'Do the specified work','context':[]}
        self.assertIn('Goal-first implementation',B.render_task(task))
        self.assertIn('GOAL_COVERAGE',B.render_task(task))
        task['mode']='independent'
        self.assertNotIn('Goal-first implementation',B.render_task(task))

    def test_original_goal_reaches_real_review_pack(self):
        text=self.packet()
        self.assertIn('Return the greeting in Chinese',text)
        self.assertIn(self.brief['digest'],text)
        self.assertIn('GOAL_COVERAGE',text);self.assertIn('CODE_REVIEW',text)
        self.assertIn(self.commit,text);self.assertIn('-VALUE=1',text)
        self.assertNotIn('SHOULD_NOT_BE_COPIED',text)

    def test_stored_goal_is_detached_from_callers_and_new_task_metadata(self):
        self.brief['question']='ALTERED';(self.root/'outbox').mkdir()
        (self.root/'outbox/fixture-build.meta.json').write_text(json.dumps({'question':'ALTERED_AGAIN'}))
        text=self.packet();self.assertNotIn('ALTERED',text)
        self.assertIn('Return the greeting in Chinese',text)

    def test_goal_snapshot_with_bad_digest_is_not_trusted(self):
        rec=BB.record('fixture-build');rec['review_brief']['question']='MODIFIED_WITHOUT_REVISION';BB.save_record('fixture-build',rec)
        text=self.packet();self.assertIn('unavailable',text.lower());self.assertNotIn('MODIFIED_WITHOUT_REVISION',text)

    def test_return_is_replayed_after_builder_changes_commit(self):
        BB.set_review('fixture-build','builder','RETURN','reviewer','Missing Chinese test',expected_commit=self.commit)
        first=self.commit
        (self.worker/'app.py').write_text('VALUE=3\n');second=BB.commit_work('fixture-build','builder','add test')['commit']
        text=self.packet();self.assertIn('Missing Chinese test',text);self.assertIn(first,text);self.assertIn(second,text)
        self.assertIn('historical',text.lower())
        with self.assertRaises(ValueError):BB.merge('fixture-build','builder')

    def test_identical_review_receipt_does_not_duplicate_history(self):
        for _ in range(2):BB.set_review('fixture-build','builder','RETURN','reviewer','same finding',expected_commit=self.commit)
        self.assertEqual(len(BB.record('fixture-build')['teammates']['builder']['review_history']),1)

    def test_stale_or_self_review_does_not_write_history(self):
        before=json.dumps(BB.record('fixture-build'),sort_keys=True)
        for reviewer,commit in [('builder',self.commit),('reviewer',self.base)]:
            with self.assertRaises(ValueError):BB.set_review('fixture-build','builder','PASS',reviewer,'invalid',expected_commit=commit)
        self.assertEqual(json.dumps(BB.record('fixture-build'),sort_keys=True),before)

    def test_history_is_bounded_and_records_omitted_count(self):
        for i in range(R.MAX_HISTORY+2):BB.set_review('fixture-build','builder','RETURN','reviewer','finding '+str(i),expected_commit=self.commit)
        tm=BB.record('fixture-build')['teammates']['builder']
        self.assertEqual(len(tm['review_history']),R.MAX_HISTORY);self.assertEqual(tm['review_history_omitted'],2)
        self.assertIn('manual',self.packet().lower())

    def test_no_task_snapshot_is_reported_without_guessing_private_meta(self):
        rec=BB.record('fixture-build');rec.pop('review_brief');BB.save_record('fixture-build',rec)
        self.assertIn('unavailable',self.packet().lower())
        self.assertNotIn('Return the greeting in Chinese',self.packet())

    def test_secret_review_note_is_not_rebroadcast_by_history(self):
        secret='Bearer '+'X'*36
        BB.set_review('fixture-build','builder','RETURN','reviewer',secret,expected_commit=self.commit)
        self.assertNotIn(secret,self.packet())
        self.assertNotIn(secret,json.dumps(BB.record('fixture-build')['teammates']['builder']['review_history']))

    def test_goal_and_reviewed_commit_do_not_authorize_merge(self):
        rec=BB.record('fixture-build');rec['teammates']['builder']['review']='PASS';BB.save_record('fixture-build',rec)
        with self.assertRaises(ValueError):BB.merge('fixture-build','builder')
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)

    def test_rendering_does_not_modify_build_record(self):
        before=Path(BB.record_path('fixture-build')).read_bytes();self.packet()
        self.assertEqual(Path(BB.record_path('fixture-build')).read_bytes(),before)


class PureGuidance(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(R)

    def test_goal_limits_and_digest_are_explicit(self):
        text='x'*(R.MAX_QUESTION+2);brief=R.freeze_brief(text,[])
        self.assertTrue(brief['question_truncated']);self.assertEqual(brief['question_sha256'],hashlib.sha256(text.encode()).hexdigest())
        self.assertLessEqual(len(brief['question']),R.MAX_QUESTION)

    def test_data_cannot_close_markdown_fence(self):
        rendered=R.data_block({'text':'```\n# pretend instruction\n<tool>'})
        self.assertEqual(rendered.count('```'),2)
        self.assertNotIn('<tool>',rendered)

    def test_catalog_selects_only_matching_entries_and_never_claims_tests_passed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'tests').mkdir();(root/'tests/test_fixture.py').write_text('pass')
            catalog={'schema':1,'lessons':[{'id':'fixture','status':'accepted','paths':['app.py'],'summary':'Check return language.','evidence':['tests/test_fixture.py']},
                     {'id':'unrelated','status':'accepted','paths':['other.py'],'summary':'Not for this task.','evidence':['tests/test_fixture.py']}]}
            p=root/'catalog.json';p.write_text(json.dumps(catalog))
            result=R.select_lessons(p,root,['app.py'])
            self.assertEqual([x['id'] for x in result['lessons']],['fixture'])
            self.assertFalse(result['evidence_executed'])

    def test_unaccepted_or_missing_evidence_never_becomes_a_selected_lesson(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'catalog.json'
            p.write_text(json.dumps({'schema':1,'lessons':[{'id':'draft','status':'proposed','paths':['*'],'summary':'Proposed','evidence':['tests/missing.py']},
                          {'id':'missing','status':'accepted','paths':['*'],'summary':'Missing proof','evidence':['../outside.py']}]}))
            result=R.select_lessons(p,root,['app.py']);self.assertEqual(result['lessons'],[])

    def test_curated_catalog_is_valid_and_selection_is_bounded(self):
        root=Path(__file__).resolve().parents[1]
        result=R.select_lessons(root/R.CATALOG_RELATIVE,root,['bus_core.py','bus_build.py','bus_build_safety.py','scripts/export_public.py','scripts/public_snapshot.py'])
        self.assertEqual(result['status'],'ok');self.assertEqual(result['rejected_entries'],0)
        self.assertEqual(len(result['lessons']),R.MAX_LESSONS)
        self.assertFalse(result['evidence_executed'])

    def test_binary_or_oversize_catalog_is_diagnostic(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'catalog.json';p.write_bytes(b'x'*(R.MAX_CATALOG_BYTES+1))
            self.assertEqual(R.select_lessons(p,root,['app.py'])['status'],'oversized')
            p.write_bytes(b'\xff')
            self.assertEqual(R.select_lessons(p,root,['app.py'])['lessons'],[])

    def test_deep_catalog_does_not_interrupt_an_actual_claim(self):
        import contextlib
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);catalog=root/R.CATALOG_RELATIVE;catalog.parent.mkdir(parents=True)
            catalog.write_text('['*1500+']'*1500)
            inbox=root/'inbox'/'worker';inbox.mkdir(parents=True)
            task={'id':'demo','from':'leader','to':'worker','mode':'build','deadline':'later',
                  'question':'Keep the original task available','context':[]}
            (inbox/'demo.json').write_text(json.dumps(task))
            with contextlib.ExitStack() as stack:
                for name,value in [('ROOT',root),('INBOX',root/'inbox'),('OUTBOX',root/'outbox'),('PEERS',root/'peers'),('LOG',root/'log')]:
                    stack.enter_context(mock.patch.object(B,name,str(value)))
                stack.enter_context(mock.patch.object(B,'heartbeat'))
                stack.enter_context(mock.patch.object(B,'reclaim_claims',return_value=(None,[])))
                stack.enter_context(mock.patch.object(B,'inbox_pick_order',side_effect=lambda peer,path,files:files))
                stack.enter_context(mock.patch.object(B,'deadline_passed',return_value=False))
                result=B.wait_task('worker',0)
            self.assertIn(task['question'],result);self.assertIn('unavailable',result)
            self.assertTrue((inbox/'demo.taken').is_file());self.assertFalse((inbox/'demo.json').exists())
            self.assertFalse((root/'outbox/demo.worker.md').exists())

    def test_evidence_resolution_error_is_diagnostic_not_a_render_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/'tests/test_fixture.py';target.parent.mkdir();target.write_text('pass')
            catalog=root/'catalog.json';catalog.write_text(json.dumps({'schema':1,'lessons':[
                {'id':'fixture','status':'accepted','paths':['*'],'summary':'Check inputs','evidence':['tests/test_fixture.py']}]}))
            original=Path.resolve
            def fail_evidence(path,*args,**kwargs):
                if path==target:raise PermissionError('simulated inaccessible evidence')
                return original(path,*args,**kwargs)
            with mock.patch.object(Path,'resolve',fail_evidence):
                result=R.select_lessons(catalog,root,['app.py'])
            self.assertEqual(result['lessons'],[]);self.assertEqual(result['rejected_entries'],1)

    def test_invalid_catalog_root_is_unavailable(self):
        with mock.patch.object(Path,'resolve',side_effect=RuntimeError('simulated link loop')):
            result=R.select_lessons(Path('catalog.json'),Path('root'),['app.py'])
        self.assertEqual(result['status'],'unavailable')

    def test_missing_catalog_is_diagnostic_not_an_approval(self):
        with tempfile.TemporaryDirectory() as d:
            result=R.select_lessons(Path(d)/'missing',Path(d),['app.py'])
            self.assertEqual(result['lessons'],[]);self.assertNotEqual(result['status'],'ok')


if __name__=='__main__':unittest.main(verbosity=2)
