# 一键接入第一批（65d2）自测：四个根因各自的回归断言。
# 跑法：py -3 tests/test_join_batch1.py （在仓库根目录，不需要起服务、不发真实请求）

import json, os, sys, time, types

sys.path.insert(0, os.path.join(os.getcwd(), "plugins", "arena", "server"))

# arena_core 依赖 arena_secrets / arena_store / arena_upstream，用桩替身
# 夹具：临时工作台根目录，放两个既有 peer。运行数据（peers/）自 2026-09-28 起不入库，测试不能依赖真实目录。
import tempfile
WB = tempfile.mkdtemp(prefix="joinb1-")
os.makedirs(os.path.join(WB, "peers"))
for _p in ("fable2", "fable3"):
    with open(os.path.join(WB, "peers", _p + ".json"), "w", encoding="utf-8") as _f:
        json.dump({"peer": _p, "last_seen": "2026-01-01T00:00:00"}, _f)
store = {"settings": {"workbench_root": WB},
         "sessions": {"sessions": [{"id": "sess-aaaa1111", "account": "a@x.com", "title": "claude fable 5", "peer": "fable9"}]},
         "accounts": {"accounts": [{"email": "a@x.com", "status": "", "status_code": 200, "has_token": True, "cookie_count": 1}]},
         "routes": {"groups": [], "upstream_aliases": {}}, "probes": {"records": []}}

st = types.ModuleType("arena_store")
st.load = lambda k: store.get(k, {})
st.save = lambda k, v: store.__setitem__(k, v)
st.update = lambda k, fn: fn(store.setdefault(k, {}))
st.now_text = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
sys.modules["arena_store"] = st

sec = types.ModuleType("arena_secrets"); sec.seal = lambda s: "sealed"; sys.modules["arena_secrets"] = sec
up = types.ModuleType("arena_upstream"); up.chat_probe = lambda *a, **k: {}; up.list_accounts = lambda: {}; sys.modules["arena_upstream"] = up

import arena_core as AC
AC.ROOT_DIR = os.path.join(os.getcwd(), "plugins", "arena", "server")

fail = []
def ok(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond: fail.append(msg)

# ---- 根因① 名字冲突 ----
taken = AC._taken_peer_names()
ok("fable2" in taken, "taken 含 peers/ 下离线 peer fable2")
ok("fable3" in taken, "taken 含超期离线 peer fable3（旧逻辑只看在线快照会漏）")
ok("fable9" in taken, "taken 含 sessions.json 里已登记的 peer fable9")

n1, s1 = AC._alloc_peer_name("claude-fable-5")
ok(n1 not in taken, "分配名 %s 不与既有 peer 冲突" % n1)
n2, s2 = AC._alloc_peer_name("claude-fable-5")
ok(n1 != n2, "同组连点两次得到两个不同名字：%s / %s" % (n1, n2))
ok(AC.JOIN_RESERVED.get(n1, 0) > time.time(), "第一个名字已被预留，第二次分配不会抢占")

n3, s3 = AC._alloc_peer_name("fable2")            # 故意用已存在的名字
ok(n3 != "fable2" and s3 == "collision-renamed", "撞名时加后缀改名而非覆盖：fable2 -> %s (%s)" % (n3, s3))
ok(os.path.exists(os.path.join(WB, "peers", "fable2.json")), "原 fable2.json 仍在，未被覆盖")

AC._release_peer_name(n1)
ok(n1 not in AC.JOIN_RESERVED, "释放后预留名回收")
for _n in (n2, n3):
    AC._release_peer_name(_n)   # 测试自己直接分配的，清掉以便后面检查 start_join 的泄漏

# ---- 根因② Cloudflare 403：浏览器 UA ----
ok("Mozilla/5.0" in AC.BROWSER_HEADERS["User-Agent"], "BROWSER_HEADERS 带浏览器 UA")
import urllib.request
captured = {}
real = urllib.request.urlopen
urllib.request.urlopen = lambda req, timeout=None: captured.update({"ua": req.get_header("User-agent"), "acc": req.get_header("Accept")}) or (_ for _ in ()).throw(SystemExit)
try:
    AC._urlopen("http://127.0.0.1:1/x", timeout=1)
except SystemExit:
    pass
finally:
    urllib.request.urlopen = real
ok(captured.get("ua", "").startswith("Mozilla/5.0"), "_urlopen 出站请求实际带上了浏览器 UA")
ok("event-stream" in (captured.get("acc") or ""), "_urlopen 带 Accept: text/event-stream")

src = open(os.path.join(os.getcwd(), "plugins", "arena", "server", "arena_core.py"), encoding="utf-8").read()
ok("urllib.request.urlopen(src" not in src and "urllib.request.urlopen(f\"{src}" not in src,
   "arena_core 里不再有裸 urllib.request.urlopen 出站调用")

# ---- 根因③ chat_probe 失败不拦截接入 ----
import inspect
js = inspect.getsource(AC.start_join)
ok("probe_error = str(e)" in js and "job[\"reason_code\"] = job.get(\"reason_code\") or code" in js,
   "chat_probe 异常被吞成 reason_code，继续往下走心跳等待")
ok(js.index("res = UP.chat_probe") < js.index('job["message"] = "等待 peer 心跳"'), "探测失败后仍进入心跳等待阶段")
ok("os.path.getmtime(peer_file) > sent_at" in js, "成功判定要求心跳文件 mtime 晚于发送时刻（不再被旧文件骗）")
ok("model_mismatch" in js and "bad_status" in js, "心跳判定含 model 与 status 三重校验")

# ---- 根因④ 余额/限额原样透传 ----
ok(AC._classify_error("Error: daily spend limit reached for this account") == "spend_limit", "spend limit 归类 spend_limit")
ok(AC._classify_error("403 Forbidden (cloudflare)") == "cf_403", "403 归类 cf_403")
ok(AC._classify_error("publicAccessToken extraction failed") == "no_token", "token 提取失败归类 no_token")
e = AC.JoinError("spend_limit", "账号 a@x.com 额度用尽：reset 03:00")
ok(e.reason_code == "spend_limit" and "reset 03:00" in str(e), "JoinError 保留原文而非吞成「失败」")

# 额度耗尽的账号：start_join 必须在发出对话前就失败，且带原文
store["accounts"]["accounts"][0].update({"status": "exhausted", "error_reason": "daily spend limit, reset 03:00"})
sent = {"called": False}
up.chat_probe = lambda *a, **k: sent.update({"called": True}) or {}
job = AC.start_join("claude-fable-5", "sess-aaaa1111", "", "http://127.0.0.1:1")
for _ in range(50):
    if job["status"] != "running": break
    time.sleep(0.1)
ok(job["status"] == "failed" and job["reason_code"] == "spend_limit", "额度受限的会话直接 failed:spend_limit（实得 %s/%s）" % (job["status"], job["reason_code"]))
ok("daily spend limit, reset 03:00" in job["error"], "上游原文出现在 job.error 里：%s" % job["error"])
ok(sent["called"] is False, "不健康账号不浪费一次对话（chat_probe 未被调用）")

# bridge 未登记 → bridge_missing，且同样不发对话
store["accounts"]["accounts"][0].update({"status": "", "error_reason": ""})
AC._urlopen = lambda url, timeout=30, headers=None: (_ for _ in ()).throw(OSError("connection refused"))
job2 = AC.start_join("claude-fable-5", "sess-aaaa1111", "", "http://127.0.0.1:1")
for _ in range(50):
    if job2["status"] != "running": break
    time.sleep(0.1)
ok(job2["status"] == "failed" and job2["reason_code"] in ("error", "timeout", "cf_403"),
   "取提示词失败 → failed 带 reason_code（实得 %s）" % job2["reason_code"])

# 失败后预留名必须归还，否则连续失败会耗尽名字空间
leaked = [n for n, t in AC.JOIN_RESERVED.items() if t > time.time()]
ok(not leaked, "失败路径已归还预留名，无泄漏（残留 %s）" % leaked)

print()
print("FAILED %d" % len(fail) if fail else "ALL PASS")
sys.exit(1 if fail else 0)