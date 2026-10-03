"""Real-process project transactions and read-only queue inspection fixtures."""
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus_core as B
import bus_projects as P


def configure(root):
    root = Path(root)
    B.ROOT = B.WORKSPACE = str(root)
    P.PROJECTS = str(root / 'projects')
    P.COMMANDS = str(root / 'console' / 'commands')
    P.DONE = str(root / 'console' / 'done')
    P.ensure()


def writer(root, index, ready, start, output):
    configure(root)
    ready.put(True)
    start.wait(20)
    try:
        for number in range(8):
            P.upsert_decision(slug='p1', task_id=f'worker-{index}-{number}', status='draft', title='fixture')
        output.put('ok')
    except Exception as exc:
        output.put(type(exc).__name__ + ': ' + str(exc))


class ProjectTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-project-tx-')
        self.root = Path(self.temp.name)
        self.patches = [mock.patch.object(B,'ROOT',str(self.root)), mock.patch.object(B,'WORKSPACE',str(self.root)),
                        mock.patch.object(P,'PROJECTS',str(self.root/'projects')),
                        mock.patch.object(P,'COMMANDS',str(self.root/'console'/'commands')),
                        mock.patch.object(P,'DONE',str(self.root/'console'/'done')),
                        mock.patch.object(B,'priority',return_value={'task_order':['priority-task']})]
        for patch in self.patches: patch.start()
        P.create_project('p1', [], 'independent', [])
    def tearDown(self):
        P.invalidate_state()
        for patch in reversed(self.patches): patch.stop()
        self.temp.cleanup()
    def test_keyword_arguments_and_project_alias_persist(self):
        P.update_project(slug='p1', patch={'agent_meta':{'builder1':{'display_name':'编码助手','note':'fixture'}}})
        self.assertEqual(P.load_project('p1')['agent_meta']['builder1']['display_name'],'编码助手')
        P.upsert_decision(slug='p1',task_id='decision-one',status='draft')
        self.assertEqual(P.decisions('p1')[0]['task_id'],'decision-one')
    def test_disjoint_alias_updates_do_not_drop_other_agent(self):
        P.update_project('p1',{'agent_meta':{'builder1':{'note':'one'}}})
        P.update_project('p1',{'agent_meta':{'builder2':{'note':'two'}}})
        self.assertEqual(set(P.load_project('p1')['agent_meta']),{'builder1','builder2'})
    def test_team_configuration_persists(self):
        teams=[{'name':'Review','peers':['builder1'],'mode':'redteam'}]
        P.update_project('p1',{'teams':teams})
        self.assertEqual(P.load_project('p1')['teams'],teams)
    def test_unknown_project_field_rejected(self):
        with self.assertRaises(ValueError): P.update_project('p1',{'repo_dir':'outside'})
    def test_invalid_metadata_shape_rejected(self):
        for value in ([],None,{'builder1':'invalid'},{'../peer':{'note':'invalid'}}):
            with self.subTest(value=value),self.assertRaises(ValueError): P.update_project('p1',{'agent_meta':value})
    def test_parent_path_is_rejected(self):
        for path in ('../p1','p1/other','p1'+chr(92)+'other','D:/outside','.'):
            with self.subTest(path=path),self.assertRaises(ValueError): P.load_project(path)
    def test_write_invalidates_cached_state(self):
        P._STATE_CACHE.update(at=time.time(),data={'old':True})
        P.update_project('p1',{'description':'changed'})
        self.assertIsNone(P._STATE_CACHE['data'])
    def test_inspection_does_not_requeue_old_inflight_command(self):
        path=Path(P.COMMANDS)/'old-command.taken'
        B.write_json(path,{'id':'old-command','kind':'dispatch','payload':{}})
        os.utime(path,(time.time()-3600,time.time()-3600))
        before=path.read_bytes()
        self.assertEqual(P.pending_commands(),[])
        self.assertTrue(path.exists()); self.assertEqual(path.read_bytes(),before)
        self.assertFalse(path.with_suffix('.json').exists())
    def test_claims_are_sorted_before_claiming_and_bounded(self):
        for cid,tid in [('a-command','other-task'),('z-command','priority-task')]:
            B.write_json(Path(P.COMMANDS)/(cid+'.json'),{'id':cid,'kind':'dispatch','payload':{'task_id':tid}})
        result=P.pending_commands(claim=True,holder='reviewlead')
        self.assertEqual([item['id'] for item in result],['z-command'])
        self.assertEqual(result[0]['claimed_by'],'reviewlead')
        self.assertTrue((Path(P.COMMANDS)/'a-command.json').exists())
    def test_relay_cannot_claim_user_decision_actions(self):
        B.write_json(Path(P.COMMANDS)/'a-command.json',{'id':'a-command','kind':'adopt','payload':{}})
        self.assertEqual(P.pending_commands(claim=True,relay=True,holder='backup'),[])
    def test_context_boundary_rejects_prefix_sibling_and_credentials(self):
        other=self.root.parent/(self.root.name+'-other')
        for value in (str(other/'secret.txt'),'.env','console/mcp_token.txt'):
            with self.subTest(value=value),self.assertRaises((ValueError,PermissionError)): B.resolve_context([value])
    def test_context_utf8_bom_is_text(self):
        path=self.root/'note.md'; path.write_bytes(bytes([239,187,191])+b'plain text')
        self.assertEqual(B.resolve_context(['note.md'])[0]['content'],'plain text')
    def test_real_process_updates_preserve_all_rows(self):
        context=mp.get_context('spawn'); ready=context.Queue(); start=context.Event(); output=context.Queue()
        jobs=[context.Process(target=writer,args=(str(self.root),i,ready,start,output)) for i in range(4)]
        try:
            for job in jobs:job.start()
            for job in jobs:ready.get(timeout=25)
            start.set()
            results=[output.get(timeout=30) for job in jobs]
            self.assertEqual(results,['ok']*4)
            self.assertEqual(len(P.decisions('p1')),32)
        finally:
            for job in jobs:
                job.join(5)
                if job.is_alive():job.terminate();job.join(5)
            ready.close();output.close()

if __name__=='__main__':
    unittest.main(verbosity=2)
