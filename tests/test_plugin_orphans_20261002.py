"""插件子进程挂到工作台的作业对象上：工作台进程被强杀，插件进程跟着退（2026-10-02 查到 5 次重启攒了 10 个孤儿插件进程）。"""
import os
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_WORKBENCH = r'''
import subprocess, sys, time
sys.path.insert(0, %r)
import bus_plugins as PL
c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(c.pid, PL._bind_to_workbench(c), flush=True)
time.sleep(60)
''' % ROOT


def alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True).stdout.decode("mbcs", "replace")
    return str(pid) in out


@unittest.skipUnless(os.name == "nt", "作业对象只在 Windows 上")
class PluginOrphans(unittest.TestCase):
    def test_plugin_dies_with_workbench(self):
        wb = subprocess.Popen([sys.executable, "-c", FAKE_WORKBENCH], stdout=subprocess.PIPE)
        pid, bound = wb.stdout.readline().decode().split()
        pid = int(pid)
        try:
            self.assertEqual(bound, "True")
            self.assertTrue(alive(pid))
            wb.kill()
            wb.wait(10)
            for _ in range(20):
                if not alive(pid):
                    break
                time.sleep(0.25)
            self.assertFalse(alive(pid), "工作台被强杀后插件进程还活着")
        finally:
            wb.stdout.close()
            if alive(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


if __name__ == "__main__":
    unittest.main()
