# 任务 20261011-214338-8b25 → 返修 20261011-225953-67af：tools/start_headless.py 单元测试。
# 只用临时目录与注入的 fake Popen / probe，不真起服务、不碰真实 console/。跑法：py -3 tests/test_start_headless_8b25.py
import glob
import importlib.util
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("start_headless", os.path.join(ROOT, "tools", "start_headless.py"))
SH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SH)

CREATE_NO_WINDOW = 0x08000000 | 0x00000200 | 0x01000000  # 与 bus_guard.py:25 相同组合
SVC = {"name": "test_9999", "script": os.path.join("bus_web.py"), "args": ["--port", "9999"], "port": 9999}


class FakeProc:
    def __init__(self):
        self.pid = 4242


def devnull():
    import subprocess
    return subprocess.DEVNULL


def sub():
    import subprocess
    return subprocess


class StartHeadless(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="headless_test_")
        self.calls = []

    def fake_popen(self, args, **kw):
        self.calls.append((args, kw))
        return FakeProc()

    # 1 子进程以 CREATE_NO_WINDOW 启动（注入 fake Popen 断言 creationflags）
    def test_popen_uses_create_no_window_and_redirects(self):
        started, detail = SH.launch(SVC, popen=self.fake_popen, probe=lambda p: False, log_dir=self.tmp)
        self.assertTrue(started, detail)
        self.assertEqual(len(self.calls), 1)
        args, kw = self.calls[0]
        self.assertEqual(kw["creationflags"], CREATE_NO_WINDOW, "必须带 CREATE_NO_WINDOW，不得新建可见控制台")
        self.assertEqual(kw["cwd"], SH.ROOT)
        # 返修第 2 条：stdin 必须 DEVNULL，无头服务不得继承交互式 stdin
        self.assertEqual(kw["stdin"], devnull(), "stdin 必须 DEVNULL（与 bus_guard.spawn 对齐）")
        self.assertTrue(hasattr(kw["stdout"], "write") and not isinstance(kw["stdout"], int), "stdout 重定向到日志文件")
        self.assertEqual(kw["stderr"], sub().STDOUT, "stderr 并入 stdout")
        joined = " ".join(args)
        self.assertIn("bus_web.py", joined, "命令行指向服务脚本")
        self.assertIn("--port", joined)
        self.assertTrue(os.path.isdir(self.tmp))

    # 2 端口被占用：明确跳过，不产生重复实例
    def test_port_busy_skips_without_popen(self):
        started, detail = SH.launch(SVC, popen=self.fake_popen, probe=lambda p: True, log_dir=self.tmp)
        self.assertFalse(started)
        self.assertIn("已被占用", detail)
        self.assertIn("不重复启动", detail)
        self.assertEqual(self.calls, [], "端口占用时不得调用 Popen")

    # 3 默认探测路径的真边界：自己刚 bind 的端口 → True；释放后的高位端口 → False
    # （返修建议第 7 条：原断言 port_busy(0) 恒 False，覆盖不了真实探测路径）
    def test_default_probe_real_boundary(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertTrue(SH.port_busy(s.getsockname()[1]), "自己刚监听的端口必须探测为占用")
        free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        free.bind(("127.0.0.1", 0))
        free_port = free.getsockname()[1]
        free.close()  # 关闭后内核释放，无人监听
        self.assertFalse(SH.port_busy(free_port), "已释放的高位端口应探测为空闲")

    # 4 轮转：超过阈值改名并新建文件（任务书语义：改名**并新建**，返修建议第 6 条修正断言）
    def test_rotate_over_threshold(self):
        path = os.path.join(self.tmp, "test_9999.log")
        with open(path, "wb") as f:
            f.write(b"x" * 32)
        self.assertTrue(SH.rotate_file(path, max_bytes=16), "超过阈值必须轮转")
        self.assertTrue(os.path.exists(path), "轮转后原路径新建空文件，服务日志路径不缺位")
        self.assertEqual(os.path.getsize(path), 0, "新建的日志为空，供后续 append")
        hist = glob.glob(os.path.join(self.tmp, "test_9999-*.log"))
        self.assertEqual(len(hist), 1, "历史日志按时间戳改名保留")
        self.assertEqual(os.path.getsize(hist[0]), 32, "历史日志内容完整")

    # 5 轮转：未达阈值不动
    def test_rotate_under_threshold(self):
        path = os.path.join(self.tmp, "test_9999.log")
        with open(path, "wb") as f:
            f.write(b"x" * 4)
        self.assertFalse(SH.rotate_file(path, max_bytes=16))
        self.assertTrue(os.path.exists(path))

    # 6 --rotate-only：只轮转不启动服务
    def test_rotate_only_does_not_start(self):
        path = os.path.join(self.tmp, "workbench_8765.log")
        with open(path, "wb") as f:
            f.write(b"x" * 64)
        rc = SH.main(["--rotate-only", "--log-dir", self.tmp, "--max-bytes", "16"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, [])
        self.assertTrue(os.path.exists(path), "轮转后原路径保留（新建空文件）")
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "workbench_8765-*.log"))), 1)

    # 7 轮转失败容错：文件被占用（os.replace 抛 OSError）时返回 False 不崩溃
    def test_rotate_error_is_tolerated(self):
        path = os.path.join(self.tmp, "test_9999.log")
        with open(path, "wb") as f:
            f.write(b"x" * 64)
        real_replace = os.replace
        def boom(src, dst):
            raise PermissionError("locked")
        os.replace = boom
        try:
            self.assertFalse(SH.rotate_file(path, max_bytes=16))
        finally:
            os.replace = real_replace
        self.assertTrue(os.path.exists(path), "轮转失败时原日志保持原样")

    # ---- 返修 20261011-225953-67af 的新增回归 ----

    # 返修第 1 条：--max-bytes 必须转发到真实拉起路径（旧版被静默丢弃）
    def test_main_forwards_max_bytes_to_launch(self):
        with mock.patch.object(SH, "launch", return_value=(True, "stub")) as launch_mock:
            rc = SH.main(["--log-dir", self.tmp, "--max-bytes", "16"])
        self.assertEqual(rc, 0)
        self.assertEqual(launch_mock.call_count, len(SH.SERVICES))
        for call in launch_mock.call_args_list:
            self.assertEqual(call.kwargs.get("max_bytes"), 16,
                             "main 必须把 --max-bytes 转发给 launch（旧版静默丢弃）")
            self.assertEqual(call.kwargs.get("log_dir"), self.tmp)

    # 返修第 4 条：main() 退出码有语义——服务失败返回 1，「已在运行而跳过」返回 0
    def test_main_exit_code_semantics(self):
        with mock.patch.object(SH, "launch", return_value=(False, "x: 端口 1 已被占用，视为已在运行，不重复启动")):
            self.assertEqual(SH.main(["--log-dir", self.tmp]), 0, "占用跳过不算失败")
        with mock.patch.object(SH, "launch", return_value=(False, "x: 启动失败")):
            self.assertEqual(SH.main(["--log-dir", self.tmp]), 1, "有服务失败必须返回 1")

    # 返修第 3 条：轮转历史有保留上限，超过 keep 代自动清理
    def test_prune_history_keeps_recent_generations(self):
        path = os.path.join(self.tmp, "test_9999.log")
        open(path, "wb").close()
        base, ext = os.path.splitext(path)
        for i in range(5):  # 造 5 代历史（时间戳文件名按字典序即时间序）
            with open("%s-2026010%d-000000%s" % (base, i + 1, ext), "wb") as f:
                f.write(b"old")
        SH.prune_history(path, keep=2)
        hist = sorted(glob.glob(os.path.join(self.tmp, "test_9999-*.log")))
        self.assertEqual(len(hist), 2, "超过 keep 的历史被清理")
        self.assertIn("%s-20260104-000000%s" % (base, ext), hist, "保留的是最近的两代")
        self.assertIn("%s-20260105-000000%s" % (base, ext), hist)

    def test_prune_keep_zero_clears_all_history(self):
        path = os.path.join(self.tmp, "test_9999.log")
        open(path, "wb").close()
        base, ext = os.path.splitext(path)
        for i in range(3):
            with open("%s-2026010%d-000000%s" % (base, i + 1, ext), "wb") as f:
                f.write(b"old")
        SH.prune_history(path, keep=0)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "test_9999-*.log"))), 0,
                         "keep=0 = 不保留历史，只留当前日志")


if __name__ == "__main__":
    unittest.main(verbosity=2)
