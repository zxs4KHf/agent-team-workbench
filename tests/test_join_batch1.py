"""Run the original batch-join assertions in an isolated process.

The legacy file executed sys.exit at import and changed module globals, breaking
unittest discovery. All original checks live unchanged in join_batch1_checks.py.
"""
from pathlib import Path
import os
import subprocess
import sys
import unittest


class JoinBatchCLI(unittest.TestCase):
    def test_original_batch_join_checks(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,'-X','utf8','-u','tests/join_batch1_checks.py'],
            cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=60,
            env={**os.environ,'PYTHONIOENCODING':'utf-8'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('FAIL:',result.stdout)

if __name__=='__main__':
    unittest.main(verbosity=2)
