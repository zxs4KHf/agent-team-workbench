"""F02 project identity across public dispatch layers. Temporary data only."""
import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bus_core as B
import bus_build as BB
import bus_projects as P
import bus_tool


class ProjectContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dispatch-scope-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'repo'
        self.repo.mkdir()
        (self.repo/'.git').mkdir()
        self.patches = []
        for obj, key, value in [(B,'WORKSPACE',str(self.root)),(P,'PROJECTS',str(self.root/'projects')),
                                (B,'INBOX',str(self.root/'inbox')),(B,'OUTBOX',str(self.root/'outbox')),
                                (B,'PEERS',str(self.root/'peers'))]:
            patch = mock.patch.object(obj,key,value);patch.start();self.addCleanup(patch.stop)
        for name in ('a','b'):
            p = self.root/'projects'/name/'project.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'slug':name,'name':name,'repo_dir':str(self.repo)}),encoding='utf-8')

    def test_mcp_schema_exposes_and_forwards_project(self):
        tree = ast.parse((ROOT/'bus_mcp.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='bus_dispatch')
        self.assertIn('project', [a.arg for a in node.args.args+node.args.kwonlyargs])
        self.assertTrue(any(isinstance(n,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='project' for k in n.keys) for n in ast.walk(node)))

    def test_bus_tool_forwards_explicit_project(self):
        response={'task_id':'fixture','peers':['p'],'deadline':'','min_answers':1,'offline':[]}
        with mock.patch.object(sys,'argv',['bus_tool','dispatch']), \
             mock.patch.object(sys,'stdin',io.StringIO(json.dumps({'project':'b','question':'test','peers':['p']}))), \
             mock.patch.object(B,'dispatch',return_value=response) as dispatch, contextlib.redirect_stdout(io.StringIO()):
            bus_tool.main()
        self.assertEqual(dispatch.call_args.kwargs.get('project'),'b')

    def test_unknown_build_has_no_setup_write_or_worktree_side_effect(self):
        with mock.patch.object(B,'ensure_dirs') as setup, mock.patch.object(BB,'open_worktrees') as opened, \
             mock.patch.object(B,'write_json') as written:
            with self.assertRaises(ValueError):
                B.dispatch('caller',[],'build','implement',[])
        setup.assert_not_called();opened.assert_not_called();written.assert_not_called()

    def test_explicit_unknown_project_does_not_create_it(self):
        with mock.patch.object(B,'ensure_dirs') as setup, mock.patch.object(BB,'open_worktrees') as opened:
            with self.assertRaises(ValueError):
                B.dispatch('caller',[],'build','implement',[],project='missing',repo_dir=str(self.repo))
        setup.assert_not_called();opened.assert_not_called()
        self.assertFalse((self.root/'projects/missing').exists())

    def test_mismatching_repo_rejected_before_setup(self):
        with mock.patch.object(B,'ensure_dirs') as setup:
            with self.assertRaises(ValueError):
                B.dispatch('caller',[],'build','implement',[],project='b',repo_dir=str(self.root/'other'))
        setup.assert_not_called()

    def test_project_path_escape_rejected_before_setup(self):
        with mock.patch.object(B,'ensure_dirs') as setup:
            with self.assertRaises(ValueError):
                B.dispatch('caller',[],'build','implement',[],project='../b',repo_dir=str(self.repo))
        setup.assert_not_called()

    def test_explicit_project_wins_peer_inference_and_reaches_payload(self):
        with mock.patch.object(B,'ensure_dirs'), mock.patch.object(B,'peer_state',return_value={'online':True}), \
             mock.patch.object(B,'role_line_for',return_value=''), mock.patch.object(B,'resolve_context',return_value=[]), \
             mock.patch.object(B,'_infer_project_for_peer',return_value='a'), mock.patch.object(B,'write_json') as write:
            B.dispatch('caller',['worker'],'independent','question',[],project='b',substitute=False)
        records=[call.args[1] for call in write.call_args_list]
        self.assertTrue(records)
        self.assertTrue(all(record.get('project')=='b' for record in records))
        self.assertTrue(all(record.get('project_source')=='explicit' for record in records))

    def test_parent_project_conflict_rejected(self):
        with mock.patch.object(B,'ensure_dirs') as setup, mock.patch.object(P,'project_for_task',return_value='a'):
            with self.assertRaises(ValueError):
                B.dispatch('caller',[],'build','implement',[],project='b',parent_task='20261013-000000-abcd')
        setup.assert_not_called()

    def test_legacy_nonbuild_none_repo_is_not_a_breaking_change(self):
        from bus_dispatch_scope import resolve
        self.assertEqual(resolve('independent', None, '', None, lambda:''), ('','',''))
        self.assertEqual(resolve('independent', 'old-label', '', None, lambda:'a'), ('old-label','','explicit'))

    def test_valid_build_uses_registered_repo_and_keeps_scope_in_task(self):
        fake={'teammates':{'worker':{'path':str(self.repo/'.wt/fixture'),'branch':'task/fixture'}}}
        with mock.patch.object(B,'ensure_dirs'), mock.patch.object(B,'peer_state',return_value={'online':True}), \
             mock.patch.object(B,'role_line_for',return_value=''), mock.patch.object(B,'resolve_context',return_value=[]), \
             mock.patch.object(BB,'open_worktrees',return_value=fake) as opened, mock.patch.object(B,'write_json') as write:
            B.dispatch('caller',['worker'],'build','implement',[],project='b',substitute=False)
        self.assertEqual(Path(opened.call_args.args[2]).resolve(),self.repo.resolve())
        self.assertTrue(all(call.args[1].get('project')=='b' for call in write.call_args_list))


if __name__ == '__main__':
    unittest.main(verbosity=2)
