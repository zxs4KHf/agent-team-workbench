"""bus_tool console 守卫：缺 action 不再默认 wait，匿名 wait 不再冒充 controller。

线上事故（2026-10-11）：外部同步脚本把内容线卡片 JSON 从 stdin 灌进
`py -3 bus_tool.py console`（没有 action 字段），旧代码 `a.get("action", "wait")`
默认成 wait → 以 controller 身份阻塞最长 590 秒，且 console_wait 每轮轮询都会
pending_commands(claim=True, holder=controller) 把用户指令真的领走写 .taken，
而调用方 subprocess 丢弃 stdout、只看 returncode → 指令每 10 分钟被
release_orphan_commands 退回一次，永久空转。

隔离方式：子进程跑一个驱动脚本，走与真实 CLI 完全相同的 bus_tool.main() 代码路径，
但把 bus_projects.PROJECTS、bus_events.EVENTS 补丁到临时目录，并用 BUS_WORKSPACE
把 console/commands、peers 等运行态也指到临时目录——PROJECTS/EVENTS 挂在 B.ROOT
（仓库根）而不是 WORKSPACE，单靠环境变量隔离不到，必须显式补丁。全程不碰真总线。
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DRIVER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {root!r})
    import bus_projects, bus_events
    bus_projects.PROJECTS = {projects!r}          # 隔离项目目录（默认在仓库根 projects/）
    bus_events.EVENTS = {events!r}                # 隔离事件旁路（默认在仓库根 events/）
    sys.argv = ['bus_tool.py', 'console']
    import bus_tool
    try:
        bus_tool.main()
    except Exception as e:                        # 复刻 bus_tool.__main__ 的错误语义
        print(f'ERROR: {{e}}', file=sys.stderr)
        sys.exit(1)
""")


class ConsoleGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wb-console-guard-")
        self.ws = Path(self.tmp.name)
        (self.ws / "projects" / "demo").mkdir(parents=True)
        (self.ws / "events").mkdir()
        # project.json 按 create_project 的字段结构预置（post 只对已存在的项目记账）
        (self.ws / "projects" / "demo" / "project.json").write_text(json.dumps(
            {"slug": "demo", "name": "demo", "description": "", "peers": [], "mode": "independent",
             "context_files": [], "default_team": "", "repo_dir": "", "created": "2026-10-11T00:00:00-04:00",
             "decisions": [], "next": [], "notes": []}, ensure_ascii=False), encoding="utf-8")
        self.decisions = self.ws / "projects" / "demo" / "decisions.json"
        self.decisions.write_text("[]", encoding="utf-8")
        self.driver = self.ws / "_driver.py"
        self.driver.write_text(DRIVER.format(root=str(ROOT), projects=str(self.ws / "projects"),
                                             events=str(self.ws / "events")), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, payload):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(self.driver)],
            input=json.dumps(payload, ensure_ascii=False), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, cwd=str(ROOT),
            env={**os.environ, "BUS_WORKSPACE": str(self.ws), "PYTHONIOENCODING": "utf-8"})

    def _snapshot(self):
        """(decisions mtime_ns, decisions bytes, 全工作区 .taken 清单) 三元组"""
        st = self.decisions.stat()
        taken = sorted(str(p.relative_to(self.ws)) for p in self.ws.rglob("*.taken"))
        return (st.st_mtime_ns, self.decisions.read_bytes(), taken)

    def test_1_卡片无action_非零退出带提示_零副作用(self):
        """外部同步脚本的真实形态：stdin 只有一张卡片 JSON，没有 action 字段。

        修复前：默认 sub="wait" → 以 controller 身份阻塞最长 590 秒并领走指令写 .taken。
        修复后：非零退出 + stderr 可读提示 + decisions.json 与 .taken 零变化。
        """
        card = {"project": "demo", "task_id": "line-story_episode", "status": "dispatched",
                "title": "内容线卡片", "summary": "没有任何 action 字段的调用"}
        before = self._snapshot()
        r = self._run(card)
        self.assertNotEqual(r.returncode, 0, f"应当非零退出，实际 stdout={r.stdout!r}")
        self.assertIn("console 需要 action", r.stderr)
        self.assertEqual(before, self._snapshot(),
                         "卡片调用不允许任何落盘副作用（.taken / decisions.json）")

    def test_2_wait无peer_真Leader正常等待(self):
        """action=wait 且 peer 省略：真 Leader（controller 身份）正常执行 wait 逻辑，返回 IDLE 而非报错。"""
        r = self._run({"action": "wait", "timeout_sec": 5})
        self.assertEqual(r.returncode, 0, f"应当成功退出，实际 stderr={r.stderr!r}")
        self.assertIn("IDLE", r.stdout)
        self.assertEqual([], list(self.ws.rglob("*.taken")))

    def test_2b_mcp_console_wait无peer回归(self):
        """真 Leader 通过 bus_mcp.bus_console(action='wait') 调用且不传 peer 时不崩溃，返回 IDLE。"""
        import asyncio
        import bus_mcp
        out = asyncio.run(bus_mcp.bus_console(action="wait", timeout_sec=5))
        self.assertIn("IDLE", out, f"真实 MCP 调用无 peer 应当返回 IDLE，实际为: {out!r}")
        self.assertNotIn("ERROR", out)

    def test_3a_post正常路径仍工作(self):
        """action=post 在隔离工作区照常落盘（不报 action 守卫的错）。"""
        r = self._run({"action": "post", "project": "demo", "task_id": "T1",
                       "status": "dispatched", "title": "内容线卡片", "summary": "正常路径回归"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("console 需要 action", r.stderr)
        self.assertIn("内容线卡片", self.decisions.read_text(encoding="utf-8"))

    def test_3b_ack不报action相关错误(self):
        """action=ack：最小断言——守卫不误伤（缺 command_id 的 KeyError 是另一回事）。"""
        r = self._run({"action": "ack", "command_id": "no-such-cid"})
        self.assertNotIn("console 需要 action", r.stderr)
        self.assertNotIn("禁止匿名回落", r.stderr)


if __name__ == "__main__":
    unittest.main()
