"""P0 regressions. All mutable bus paths use temporary directories, never production."""
import concurrent.futures
import json
import multiprocessing
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_implementation_state_5054 import Base, iso
import bus_core as B
import bus_projects as P

BUILD = '20260924-001500-ab12'


def configure(root):
    B.ROOT = B.WORKSPACE = root
    B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(root, d) for d in ('peers','inbox','outbox','log'))
    B.LOCK = os.path.join(root, 'console', 'controller.lock')
    B.PRIORITY = os.path.join(root, 'console', 'priority.json')
    P.PROJECTS = os.path.join(root, 'projects')
    P.COMMANDS = os.path.join(root, 'console', 'commands')
    P.DONE = os.path.join(root, 'console', 'done')
    P.BUILDS = os.path.join(root, 'console', 'builds')
    P.ROLES = os.path.join(root, 'console', 'roles.json')
    B.model_groups = lambda: []


def worker(root, start, output):
    configure(root)
    start.wait(15)
    try:
        output.put(P.request_implement('p1', 'root')['command_id'])
    except Exception as exc:
        output.put('ERROR:' + repr(exc))


class TestLoop(Base):
    def setUp(self):
        self.old_workspace = B.WORKSPACE
        super().setUp()
        B.WORKSPACE = self.tmp
        self.decision('root', execution='pending')

    def tearDown(self):
        B.WORKSPACE = self.old_workspace
        super().tearDown()

    def rec(self):
        return next(x for x in P.decisions(self.slug) if x['task_id'] == 'root')

    def cmds(self):
        return [x for x in os.listdir(P.COMMANDS) if x.endswith(('.json','.taken'))]

    def test_ack_requires_real_build_and_preserves_queue(self):
        cid = P.request_implement(self.slug, 'root')['command_id']
        for result in ('done', 'task_id: ' + BUILD):
            with self.assertRaises(ValueError):
                P.ack_command(cid, result)
            self.assertIn(cid + '.json', self.cmds())
        self.assertFalse(os.listdir(P.DONE))

    def test_ack_links_separate_build_and_progress_never_falls_back(self):
        cid = P.request_implement(self.slug, 'root')['command_id']
        self.build(BUILD, {'f1': {'commit':'', 'merged':False}})
        P.ack_command(cid, 'task_id: ' + BUILD)
        self.assertEqual(self.rec()['implementation_task_id'], BUILD)
        self.assertEqual(self.impl('root')['state'], 'dispatched')
        self.assertEqual(self.impl('root')['build_task_id'], BUILD)
        os.makedirs(os.path.join(B.INBOX, 'f1'))
        B.write_json(os.path.join(B.INBOX, 'f1', BUILD+'.taken'), {})
        self.assertEqual(self.impl('root')['state'], 'building')
        self.build(BUILD, {'f1': {'commit':'abc123456', 'merged':False}})
        self.assertEqual(self.impl('root')['state'], 'review')
        self.build(BUILD, {'f1': {'commit':'abc123456', 'merged':True, 'merge_commit':'def123456'}})
        self.assertEqual(self.impl('root')['state'], 'merged')
        r = P.request_implement(self.slug, 'root')
        self.assertEqual(r['state'], 'merged')
        self.assertTrue(r['dedup'])
        self.assertFalse(self.cmds())
        self.assertIn('already done', P.ack_command(cid, 'task_id: '+BUILD))

    def test_nudge_after_ack_dedups_linked_build(self):
        cid = P.request_implement(self.slug, 'root')['command_id']
        self.build(BUILD, {'f1': {'commit':'', 'merged':False}})
        P.ack_command(cid, 'task_id: '+BUILD)
        result = P.request_implement(self.slug, 'root')
        self.assertTrue(result['dedup'])
        self.assertEqual(result['build_task_id'], BUILD)
        self.assertEqual(self.cmds(), [])

    def test_cancelled_even_with_existing_command_cannot_nudge_or_ack(self):
        cid = P.request_implement(self.slug, 'root')['command_id']
        P.upsert_decision(self.slug, 'root', status='cancelled')
        with self.assertRaises(ValueError): P.request_implement(self.slug, 'root')
        with self.assertRaises(ValueError): P.ack_command(cid, 'done')
        self.assertIn(cid+'.json', self.cmds())
        P.ack_command(cid, 'cancelled by user', ok=False)
        self.assertFalse(self.cmds())

    def test_direct_release_ack_and_finished_nudge_no_new_work(self):
        cid = P.request_implement(self.slug, 'root')['command_id']
        P.upsert_decision(self.slug, 'root', execution='released')
        P.ack_command(cid, 'direct fix verified and posted')
        self.assertTrue(P.request_implement(self.slug,'root')['dedup'])
        self.assertFalse(self.cmds())

    def test_wrong_repo_build_rejected(self):
        cid = P.request_implement(self.slug,'root')['command_id']
        self.build(BUILD, {'f1': {'commit':'', 'merged':False}})
        p = P.load_project(self.slug); p['repo_dir']=os.path.join(self.tmp,'other'); P.save_project(p)
        with self.assertRaisesRegex(ValueError, '仓库'):
            P.ack_command(cid, 'task_id: '+BUILD)
        self.assertFalse(self.rec().get('implementation_task_id'))

    def test_cross_project_same_id_has_distinct_commands(self):
        p2 = P.create_project('p2', [], 'independent', [])
        P.upsert_decision(p2['slug'],'root',status='adopted',execution='pending')
        r1=P.request_implement(self.slug,'root'); r2=P.request_implement(p2['slug'],'root')
        self.assertNotEqual(r1['command_id'],r2['command_id'])
        snap=P.implementation_snapshot([])
        self.assertEqual(P.implementation_of('root',snap,self.slug)['command_id'],r1['command_id'])
        self.assertEqual(P.implementation_of('root',snap,p2['slug'])['command_id'],r2['command_id'])

    def test_threads_enqueue_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids=list(pool.map(lambda _:P.request_implement(self.slug,'root')['command_id'],range(16)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(len(self.cmds()),1)

    def test_processes_enqueue_once(self):
        ctx=multiprocessing.get_context('spawn'); start=ctx.Event(); output=ctx.Queue()
        ps=[ctx.Process(target=worker,args=(self.tmp,start,output)) for _ in range(4)]
        for p in ps:p.start()
        start.set()
        try:
            ids=[output.get(timeout=20) for _ in ps]
            self.assertFalse(any(x.startswith('ERROR:') for x in ids),ids)
            self.assertEqual(len(set(ids)),1,ids)
            self.assertEqual(len(self.cmds()),1)
        finally:
            for p in ps:
                p.join(10)
                if p.is_alive():p.terminate();p.join()
            output.close()

    def test_missing_link_record_does_not_silently_duplicate_work(self):
        P.upsert_decision(self.slug,'root',implementation_task_id=BUILD)
        with self.assertRaisesRegex(ValueError,'缺失'):P.request_implement(self.slug,'root')
        self.assertFalse(self.cmds())

    def test_comment_is_carried(self):
        r=P.request_implement(self.slug,'root',comment='只改本仓库')
        c=B.read_json(os.path.join(P.COMMANDS,r['command_id']+'.json'))
        self.assertEqual(c['payload']['comment'],'只改本仓库')


if __name__ == '__main__':
    multiprocessing.freeze_support()
    unittest.main(verbosity=2)
