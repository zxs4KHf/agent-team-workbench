import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # 从 tests/ 运行时找到根目录模块
import unittest,tempfile,threading
from pathlib import Path
import named_workspace_io as W

class WorkspaceTools(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_create_and_read_version(self):
        W.write(self.root,'a.txt','one\ntwo\n','missing')
        got=W.read(self.root,['a.txt'],2,2)
        self.assertIn('2: two',got);self.assertNotIn('1: one',got);self.assertIn('sha256:',got)
    def test_overwrite_requires_version(self):
        W.write(self.root,'a.txt','one')
        with self.assertRaises(ValueError):W.write(self.root,'a.txt','two')
    def test_correct_version_and_atomic_replace(self):
        W.write(self.root,'a.txt','one')
        v=W.version((self.root/'a.txt').read_bytes())
        W.write(self.root,'a.txt','two',v)
        self.assertEqual((self.root/'a.txt').read_text(),'two')
        self.assertFalse(list(self.root.glob('.mcp-write-*')))
    def test_stale_version_is_rejected(self):
        W.write(self.root,'a.txt','one')
        with self.assertRaises(ValueError):W.write(self.root,'a.txt','bad','sha256:no')
        self.assertEqual((self.root/'a.txt').read_text(),'one')
    def test_exact_patch(self):
        W.write(self.root,'a.txt','one\ntwo')
        v=W.version((self.root/'a.txt').read_bytes())
        W.patch(self.root,'a.txt','two','THREE',v)
        self.assertEqual((self.root/'a.txt').read_text(),'one\nTHREE')
    def test_ambiguous_patch_rejected(self):
        W.write(self.root,'a.txt','xx')
        with self.assertRaises(ValueError):W.patch(self.root,'a.txt','x','y',W.version(b'xx'))
    def test_control_state_write_denied(self):
        with self.assertRaises(PermissionError):W.write(self.root,'workbench/bus/console/new.json','{}')
    def test_credential_paths_denied(self):
        for p in ['.env','workbench/bus/console/mcp_token.txt','workbench/bus/console/bridge.json','.cloudflared/secret.json','.private/backup','a.key']:
            with self.subTest(p=p),self.assertRaises(PermissionError):W.resolve(self.root,p)
    def test_traversal_denied(self):
        with self.assertRaises(ValueError):W.resolve(self.root,'../elsewhere.txt')
    def test_create_only_conflict(self):
        W.write(self.root,'a.txt','one','missing')
        with self.assertRaises(ValueError):W.write(self.root,'a.txt','two','missing')
    def test_threaded_cas_one_winner(self):
        W.write(self.root,'a.txt','one');v=W.version(b'one');barrier=threading.Barrier(2);results=[]
        def f(n):
            barrier.wait()
            try:W.write(self.root,'a.txt',str(n),v);results.append(True)
            except ValueError:results.append(False)
        ts=[threading.Thread(target=f,args=(n,)) for n in range(2)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(sorted(results),[False,True])
    def test_binary_read_is_not_text(self):
        (self.root/'binary.bin').write_bytes(b'\x00\xff')
        self.assertIn('ERROR:',W.read(self.root,['binary.bin']))

if __name__=='__main__':unittest.main()
