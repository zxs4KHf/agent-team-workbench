"""直连 Agent（2026-10-21 用户：外部窗口接入、可单独派活、能调能见文件夹 / 权限模式 / 工具）。

钉住的行为：
1. 文件夹边界：只能碰配置里的文件夹；只读文件夹不能写；凭据文件拒读；相对路径落在第一个文件夹。
2. 四档权限：只读 / 逐项审批 / 自动改文件 / 完全放开，命令按只读、自动放行、外发、串联符号分流。
3. 审批 = 绑定内容的一次性凭据：批准后按记录原样执行一次；记录被改过拒绝执行；拒绝不执行；处理过的不能再处理。
4. 写文件保留原换行（CRLF 仓库不会被整份改成 LF），不在用户文件夹里留锁目录。
5. MCP：直连令牌只看得到、只调得动 direct_*；Leader 主令牌看不到 direct_*；直连令牌调 ws_read 被拦。
全部用临时目录（BUS_DIRECT_DIR 指过去），不碰真实运行数据。"""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="direct-agent-")
os.environ["BUS_DIRECT_DIR"] = os.path.join(TMP, "direct")
sys.path.insert(0, REPO)
import bus_direct as DIR  # noqa: E402

MASTER = "m" * 40


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        # realpath：CI 的临时目录是 8.3 短名（RUNNER~1），被测代码按真实长路径比较与返回
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="da-", dir=TMP))
        self.rw = os.path.join(self.root, "repo")
        self.ro = os.path.join(self.root, "docs")
        self.out = os.path.join(self.root, "outside")
        for d in (self.rw, self.ro, self.out):
            os.makedirs(d)
        with open(os.path.join(self.rw, "a.txt"), "wb") as f:
            f.write(b"line1\r\nline2\r\nline3\r\n")
        with open(os.path.join(self.ro, "note.md"), "w", encoding="utf-8") as f:
            f.write("只读说明\n")
        with open(os.path.join(self.out, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("outside\n")
        with open(os.path.join(self.rw, ".env"), "w", encoding="utf-8") as f:
            f.write("KEY=fake\n")
        self.agent = DIR.save({"name": "测试直连", "mode": "auto_edit",
                               "folders": [{"path": self.rw, "access": "write"}, {"path": self.ro, "access": "read", "label": "文档"}]})

    def full(self, mode=None):
        if mode:
            DIR.save({"id": self.agent["id"], "mode": mode})
        return DIR.get(self.agent["id"])


class Config(Base):
    def test_create_hides_token_and_issues_one(self):
        self.assertNotIn("token", self.agent)
        self.assertTrue(self.agent["token_hint"].startswith("…"))
        self.assertGreaterEqual(len(DIR.reveal_token(self.agent["id"])), 32)
        self.assertIn(("" + DIR.reveal_token(self.agent["id"]), "direct:" + self.agent["id"]), DIR.token_identities())

    def test_folder_rules(self):
        with self.assertRaises(ValueError):
            DIR.normalize_folders([{"path": "relative/dir"}])
        with self.assertRaises(ValueError):
            DIR.normalize_folders([{"path": os.path.join(self.root, "nope")}])
        with self.assertRaises(ValueError):
            DIR.normalize_folders([{"path": os.path.splitdrive(self.root)[0] + "\\" if os.name == "nt" else "/"}])
        got = DIR.normalize_folders([{"path": self.rw}, {"path": self.rw + os.sep, "access": "read"}, self.ro])
        self.assertEqual([g["path"] for g in got], [self.rw, self.ro], "同一路径只留一条")

    def test_disabled_and_rotated_token_drop_out(self):
        old = DIR.reveal_token(self.agent["id"])
        DIR.rotate_token(self.agent["id"])
        toks = [t for t, _ in DIR.token_identities()]
        self.assertNotIn(old, toks)
        DIR.save({"id": self.agent["id"], "enabled": False})
        self.assertNotIn("direct:" + self.agent["id"], [w for _, w in DIR.token_identities()])

    def test_summary_feeds_agents_page_without_secrets(self):
        # Agent 页的直连卡片只靠整页状态里的 summary：每人的启停、待批、待办、文件夹数要对，令牌不能混进来
        aid = self.agent["id"]
        DIR.request_approval(self.full(), {"kind": "run", "command": "git push", "cwd": self.rw}, "外发要批准")
        DIR.add_task(aid, "看看 README")
        DIR.save({"id": aid, "enabled": False})
        s = DIR.summary()
        row = next(r for r in s["list"] if r["id"] == aid)
        self.assertEqual((row["enabled"], row["pending"], row["open_tasks"], row["folders"], row["writable"]), (False, 1, 1, 2, 1))
        self.assertEqual(s["agents"], len(s["list"]))
        self.assertGreaterEqual(s["pending"], 1)
        self.assertNotIn(DIR.reveal_token(aid), json.dumps(s, ensure_ascii=False))

    def test_bad_mode_and_unknown_project_rejected(self):
        with self.assertRaises(ValueError):
            DIR.save({"id": self.agent["id"], "mode": "god"})
        with self.assertRaises(ValueError):
            DIR.save({"id": self.agent["id"], "projects": ["不存在的项目-xyz"]})


class Boundary(Base):
    def test_locate(self):
        a = self.full()
        _, full, _ = DIR.locate(a, "a.txt")
        self.assertEqual(full, os.path.join(self.rw, "a.txt"), "相对路径按第一个文件夹算")
        _, full, _ = DIR.locate(a, os.path.join(self.ro, "note.md"))
        self.assertEqual(full, os.path.join(self.ro, "note.md"))
        with self.assertRaises(PermissionError):
            DIR.locate(a, os.path.join(self.out, "secret.txt"))
        with self.assertRaises((PermissionError, ValueError)):
            DIR.locate(a, "../outside/secret.txt")
        with self.assertRaises(PermissionError):
            DIR.locate(a, os.path.join(self.ro, "note.md"), write=True)
        with self.assertRaises(PermissionError):
            DIR.locate(a, ".env")

    def test_read_list_search(self):
        a = self.full()
        txt = DIR.read(a, ["a.txt", os.path.join(self.out, "secret.txt"), ".env"])
        self.assertIn("2: line2", txt)
        self.assertIn("version: sha256:", txt)
        self.assertNotIn("TOPSECRET", txt)
        self.assertEqual(txt.count("ERROR"), 2, "越界和凭据各报一次错，正文不漏")
        self.assertIn("a.txt", DIR.list_dir(a, "."))
        self.assertNotIn("KEY=fake", DIR.search(a, "KEY"), "搜索不翻凭据文件")
        self.assertIn("a.txt:2:", DIR.search(a, "LINE2"))


class Modes(Base):
    def test_commands(self):
        for mode, cmd, want in [
            ("readonly", "git status", "allow"), ("readonly", "git status; del a.txt", "deny"), ("readonly", "py -3 x.py", "deny"),
            ("readonly", "git diff --output=x.txt", "deny"),
            ("ask", "git log -3", "allow"), ("ask", "git commit -m x", "approval"),
            ("auto_edit", "git commit -m x", "allow"), ("auto_edit", "git push", "approval"), ("auto_edit", "gh pr create -f", "approval"),
            ("auto_edit", "git commit -m x; git push", "approval"), ("auto_edit", "rm -r src", "approval"),
            ("bypass", "git push", "allow"), ("bypass", "anything | at all", "allow"),
        ]:
            self.assertEqual(DIR.classify_command(self.full(mode), cmd)[0], want, f"{mode}: {cmd}")

    def test_writes(self):
        self.assertEqual([DIR.classify_write(self.full(m))[0] for m in ("readonly", "ask", "auto_edit", "bypass")],
                         ["deny", "approval", "allow", "allow"])


class Perform(Base):
    def run_op(self, op, why=""):
        return asyncio.run(DIR.perform(self.full(), op, why))

    def test_auto_edit_writes_and_keeps_crlf(self):
        ver = DIR.read(self.full(), ["a.txt"]).split("version: ")[1].split("\n")[0]
        out = self.run_op({"kind": "patch", "path": "a.txt", "old_text": "line2", "new_text": "LINE-2", "expected_version": ver})
        self.assertTrue(out.startswith("OK"), out)
        with open(os.path.join(self.rw, "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"line1\r\nLINE-2\r\nline3\r\n", "CRLF 原样保留，只改那一处")
        out = self.run_op({"kind": "write", "path": "new/b.md", "content": "x\n", "expected_version": "missing"})
        self.assertTrue(out.startswith("OK"), out)
        self.assertFalse(os.path.exists(os.path.join(self.rw, ".workbench-locks")), "锁不落在用户文件夹里")
        self.assertIn("Conflict", str(self._conflict()), "版本过期拒写")

    def _conflict(self):
        try:
            return self.run_op({"kind": "write", "path": "a.txt", "content": "y", "expected_version": "sha256:stale"})
        except ValueError as e:
            return e

    def test_readonly_folder_and_mode_refuse(self):
        with self.assertRaises(PermissionError):
            self.run_op({"kind": "write", "path": os.path.join(self.ro, "x.md"), "content": "x", "expected_version": "missing"})
        self.full("readonly")
        self.assertTrue(self.run_op({"kind": "write", "path": "z.md", "content": "x", "expected_version": "missing"}).startswith("DENIED"))
        self.assertFalse(os.path.exists(os.path.join(self.rw, "z.md")))

    def test_approval_executes_once_after_approve(self):
        self.full("ask")
        old_wait = DIR.WAIT_SEC
        DIR.WAIT_SEC = 1
        try:
            out = self.run_op({"kind": "write", "path": "c.md", "content": "approved\n", "expected_version": "missing"}, "试一下")
        finally:
            DIR.WAIT_SEC = old_wait
        self.assertTrue(out.startswith("PENDING"), out)
        self.assertFalse(os.path.exists(os.path.join(self.rw, "c.md")), "没批准不落盘")
        ap_id = re.search(r"ap-[0-9a-z-]+", out).group(0)
        rec = DIR.get_approval(ap_id)
        self.assertEqual((rec["status"], rec["why"]), ("pending", "试一下"))
        self.assertIn("新建", rec["preview"])
        DIR.decide(ap_id, "approve", background=False)
        rec = DIR.get_approval(ap_id)
        self.assertEqual(rec["status"], "done", rec.get("result"))
        with open(os.path.join(self.rw, "c.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "approved\n")
        self.assertTrue(asyncio.run(DIR.wait_approval(ap_id, 1)).startswith("DONE"))
        with self.assertRaises(ValueError):
            DIR.decide(ap_id, "approve", background=False)

    def test_tampered_or_denied_never_runs(self):
        a = self.full("ask")
        rec = DIR.request_approval(a, {"kind": "write", "path": "d.md", "content": "ok", "expected_version": "missing", "agent": a["id"]}, "r")
        path = DIR._approval_path(rec["id"])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["op"]["path"] = "evil.md"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.assertEqual(DIR.decide(rec["id"], "approve", background=False)["status"], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.rw, "evil.md")))
        rec2 = DIR.request_approval(a, {"kind": "write", "path": "e.md", "content": "ok", "expected_version": "missing", "agent": a["id"]}, "r")
        DIR.decide(rec2["id"], "deny", "先别改")
        self.assertIn("先别改", asyncio.run(DIR.wait_approval(rec2["id"], 1)))
        self.assertFalse(os.path.exists(os.path.join(self.rw, "e.md")))

    @unittest.skipUnless(os.name == "nt", "PowerShell 执行只在 Windows 上测")
    def test_run_in_bypass_keeps_quotes_and_utf8(self):
        self.full("bypass")
        cmd = f"& '{sys.executable}' -c \"print('ok 中文', 1+1)\""
        out = self.run_op({"kind": "run", "command": cmd, "cwd": "", "timeout_sec": 60})
        self.assertIn("exit_code: 0", out)
        self.assertIn("ok 中文 2", out)
        self.assertIn(self.rw, out, "默认在第一个可写文件夹里跑")
        with self.assertRaises(PermissionError):
            self.run_op({"kind": "run", "command": "dir", "cwd": self.out})

    def test_tasks_roundtrip(self):
        t = DIR.add_task(self.agent["id"], "把 README 的安装步骤核对一遍")
        self.assertIn(t["id"], DIR.tasks_text(self.agent["id"]))
        DIR.update_task(self.agent["id"], t["id"], "done", "核对完，两处过时已改")
        self.assertEqual(DIR.tasks(self.agent["id"])[0]["result"], "核对完，两处过时已改")
        self.assertIn("没有给你的待办", DIR.tasks_text(self.agent["id"]))

    def test_prompt_states_scope_and_rules(self):
        p = DIR.system_prompt(self.full("ask"))
        for must in (self.rw, "[可写]", self.ro, "[只读]", "逐项审批", "direct_approval", "不要换个写法", "[已验证]"):
            self.assertIn(must, p)


class Mcp(Base):
    """真起 bus_mcp（临时端口、临时主令牌、BUS_DIRECT_DIR 指向临时目录）。"""

    def setUp(self):
        super().setUp()
        self.tok_file = os.path.join(self.root, "token.txt")
        with open(self.tok_file, "w", encoding="utf-8") as f:
            f.write(MASTER)
        self.port = free_port()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", BUS_DIRECT_DIR=os.environ["BUS_DIRECT_DIR"])
        self.proc = subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(REPO, "bus_mcp.py"), "--port", str(self.port),
                                      "--token-file", self.tok_file], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("MCP 没起来")

    def tearDown(self):
        self.proc.kill()
        self.proc.wait(10)

    def call(self, token, method, params=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return {"http": e.code}
        data = [json.loads(ln[5:]) for ln in text.splitlines() if ln.startswith("data:")] or [json.loads(text)]
        return data[-1]

    def tools(self, token):
        self.call(token, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        return [t["name"] for t in self.call(token, "tools/list")["result"]["tools"]]

    def tool(self, token, name, args):
        res = self.call(token, "tools/call", {"name": name, "arguments": args})
        if "error" in res:
            return "RPCERROR " + json.dumps(res["error"], ensure_ascii=False)
        r = res["result"]
        return ("TOOLERROR " if r.get("isError") else "") + "".join(c.get("text", "") for c in r.get("content", []))

    def test_identity_split(self):
        direct = DIR.reveal_token(self.agent["id"])
        names = self.tools(direct)
        self.assertTrue(names and all(n.startswith("direct_") for n in names), names)
        self.assertIn("direct_run", names)
        lead = self.tools(MASTER)
        self.assertIn("bus_console", lead)
        self.assertFalse([n for n in lead if n.startswith("direct_")], "Leader 看不到 direct_*")
        blocked = self.tool(direct, "ws_read", {"paths": ["README.md"]})
        self.assertTrue(blocked.startswith(("TOOLERROR", "RPCERROR")), blocked)
        self.assertNotIn("# ", blocked[:50])
        self.assertIn("不对当前身份开放", blocked)
        self.assertIn("不对当前身份开放", self.tool(MASTER, "direct_info", {}))
        info = self.tool(direct, "direct_info", {})
        self.assertIn("测试直连", info)
        self.assertIn(self.rw, info)
        self.assertIn("2: line2", self.tool(direct, "direct_read", {"paths": ["a.txt"]}))
        self.assertIn("ERROR", self.tool(direct, "direct_read", {"paths": [os.path.join(self.out, "secret.txt")]}))
        DIR.save({"id": self.agent["id"], "enabled": False})
        self.assertEqual(self.call(direct, "tools/list").get("http"), 401, "停用后令牌立即失效")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
