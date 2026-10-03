# -*- coding: utf-8 -*-
"""20261001-065052-5509 · ADR 0010 第 2 步：时间戳带偏移。

验的是「跨时区进程」这个真实故障：常驻进程缓存了 UTC+8，新进程用 UTC-4，
同一份心跳在两个进程里算出来的 age 差 12 小时 → 在线的 Agent 被判离线。
带偏移之后，无论哪个进程的本地时区是什么，比较结果都必须一致。

全部在 tempfile 临时目录里跑，不碰真实 peers/ inbox/ outbox/。
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bus_core as B  # noqa: E402

SH = dt.timezone(dt.timedelta(hours=8))      # Asia/Shanghai
NY_EDT = dt.timezone(dt.timedelta(hours=-4))  # America/New_York 夏令时
NY_EST = dt.timezone(dt.timedelta(hours=-5))  # America/New_York 冬令时


class TestParseTs(unittest.TestCase):
    def test_带偏移(self):
        d = B.parse_ts("2026-10-01T12:32:14+08:00")
        self.assertIsNotNone(d.tzinfo)
        self.assertEqual(d.astimezone(dt.timezone.utc).hour, 4)

    def test_Z结尾(self):
        self.assertEqual(B.parse_ts("2026-10-01T04:32:14Z").astimezone(dt.timezone.utc).hour, 4)

    def test_无偏移旧值按本地补(self):
        d = B.parse_ts("2026-10-01T12:32:14")
        self.assertIsNotNone(d.tzinfo, "legacy 值必须补上偏移，不能留 naive")
        self.assertEqual(d.replace(tzinfo=None), dt.datetime(2026, 10, 1, 12, 32, 14))

    def test_解析不了返回None(self):
        for bad in ["", None, "not-a-time", "9999", 12345, [], {}]:
            self.assertIsNone(B.parse_ts(bad), repr(bad))

    def test_now_iso带偏移且可被自己解析(self):
        s = B.now_iso()
        self.assertRegex(s, r"[+-]\d{2}:\d{2}$|Z$")
        self.assertIsNotNone(B.parse_ts(s))
        self.assertLess(abs(B.age_sec(s)), 5)

    def test_aware与naive混比不抛异常(self):
        """回归：parse_ts 以前的实现会产出 naive，和 aware 相减直接 TypeError。"""
        self.assertIsInstance(B.age_sec("2026-10-01T12:32:14"), float)
        self.assertIsInstance(B.age_sec("2026-10-01T12:32:14+08:00"), float)


class TestCrossTimezone(unittest.TestCase):
    """同一份带偏移的时间戳，在两个不同时区的「进程」里算出同样的 age。"""

    def ages(self, stamp):
        """用两个固定偏移模拟两个进程：把 now_dt 换掉即可（astimezone 是进程级时区）。"""
        out = []
        real = B.now_dt
        try:
            for tz in (SH, NY_EDT):
                B.now_dt = (lambda t=tz: dt.datetime(2026, 10, 1, 12, 35, 0, tzinfo=SH).astimezone(t))
                out.append(B.heartbeat_age(stamp))
        finally:
            B.now_dt = real
        return out

    def test_新心跳在两个时区都判在线(self):
        fresh = dt.datetime(2026, 10, 1, 12, 34, 0, tzinfo=SH).isoformat()   # 60 秒前
        for age, skewed in self.ages(fresh):
            self.assertFalse(skewed)
            self.assertAlmostEqual(age, 60, delta=2)
            self.assertLessEqual(age, B.HEARTBEAT_STALE_SEC, "新心跳必须判在线")

    def test_旧心跳在两个时区都判离线(self):
        old = dt.datetime(2026, 10, 1, 11, 0, 0, tzinfo=SH).isoformat()      # 95 分钟前
        for age, skewed in self.ages(old):
            self.assertFalse(skewed)
            self.assertGreater(age, B.HEARTBEAT_STALE_SEC)

    def test_同一时刻换写法结果一致(self):
        same = dt.datetime(2026, 10, 1, 12, 34, 0, tzinfo=SH)
        a = [x[0] for x in self.ages(same.isoformat())]
        b = [x[0] for x in self.ages(same.astimezone(NY_EDT).isoformat())]
        c = [x[0] for x in self.ages(same.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"))]
        for x, y, z in zip(a, b, c):
            self.assertAlmostEqual(x, y, delta=1)
            self.assertAlmostEqual(x, z, delta=1)


class TestSkewBoundary(unittest.TestCase):
    def age(self, delta_sec):
        real = B.now_dt
        now = dt.datetime(2026, 10, 1, 12, 0, 0, tzinfo=SH)
        try:
            B.now_dt = lambda: now
            return B.heartbeat_age((now + dt.timedelta(seconds=delta_sec)).isoformat())
        finally:
            B.now_dt = real

    def test_超前299秒仍按正常机器误差(self):
        age, skewed = self.age(+B.CLOCK_SKEW_SEC - 1)
        self.assertFalse(skewed)
        self.assertEqual(age, 0.0)

    def test_超前301秒判不可信(self):
        age, skewed = self.age(+B.CLOCK_SKEW_SEC + 1)
        self.assertTrue(skewed)
        self.assertEqual(age, 1e9, "不可信 = 按失联算，不能让它冒充在线")

    def test_正好300秒不越界(self):
        self.assertFalse(self.age(+B.CLOCK_SKEW_SEC)[1])

    def test_解析失败按失联且不标skewed(self):
        self.assertEqual(B.heartbeat_age("坏数据"), (1e9, False))
        self.assertEqual(B.heartbeat_age(None), (1e9, False))


class TestDST(unittest.TestCase):
    """夏令时切换那一小时：EDT(-4) → EST(-5)，本地墙钟 01:30 出现两次。

    带偏移之后两个 01:30 是不同的瞬间，差值必须算对；这正是只存本地字符串会算错的地方。
    """

    def test_切换前后两个同名本地时刻互不混淆(self):
        before = dt.datetime(2026, 11, 1, 1, 30, 0, tzinfo=NY_EDT)   # UTC 05:30
        after = dt.datetime(2026, 11, 1, 1, 30, 0, tzinfo=NY_EST)    # UTC 06:30
        self.assertEqual((after - before).total_seconds(), 3600)
        self.assertEqual(B.parse_ts(before.isoformat()).astimezone(dt.timezone.utc).hour, 5)
        self.assertEqual(B.parse_ts(after.isoformat()).astimezone(dt.timezone.utc).hour, 6)

    def test_跨切换的心跳不被判成未来(self):
        real = B.now_dt
        try:
            B.now_dt = lambda: dt.datetime(2026, 11, 1, 1, 30, 0, tzinfo=NY_EST)   # 切换后
            age, skewed = B.heartbeat_age(dt.datetime(2026, 11, 1, 1, 30, 0, tzinfo=NY_EDT).isoformat())
            self.assertFalse(skewed, "切换前的 01:30 是一小时前，不是未来")
            self.assertAlmostEqual(age, 3600, delta=2)
        finally:
            B.now_dt = real

    def test_无偏移旧值随本机时区解释是legacy已知代价(self):
        """同一无偏移串在两个不同本机时区下必须解析出不同时刻——legacy 的有损性被真实钉死（qoder1：原断言恒真）。"""
        same = "2026-11-01T01:30:00"
        real_tz = B._local_tz
        sh = dt.timezone(dt.timedelta(hours=8))
        ny = dt.timezone(dt.timedelta(hours=-5))
        try:
            B._local_tz = lambda: sh
            t_sh = B.parse_ts(same)
            B._local_tz = lambda: ny
            t_ny = B.parse_ts(same)
        finally:
            B._local_tz = real_tz
        self.assertIsNotNone(t_sh)
        self.assertIsNotNone(t_ny)
        self.assertNotEqual(t_sh, t_ny, "无偏移串的解析结果必须随 _local_tz 变化，否则 legacy 有损性无从体现")
        self.assertAlmostEqual(abs((t_sh - t_ny).total_seconds()), 13 * 3600, delta=1)


class TestLease(unittest.TestCase):
    def setUp(self):
        self.saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG", "LOCK")]
        self.tmp = tempfile.mkdtemp(prefix="5509-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.LOCK = os.path.join(self.tmp, "console", "lead.lock")
        B.ensure_dirs()
        os.makedirs(os.path.dirname(B.LOCK), exist_ok=True)

    def tearDown(self):
        for m, n, v in self.saved:
            setattr(m, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_新发租约带偏移且剩余约等于TTL(self):
        l = B.acquire_lease("leader1")
        self.assertRegex(l["expires"], r"[+-]\d{2}:\d{2}$")
        self.assertAlmostEqual(B.lease_left(l), B.LEASE_TTL_SEC, delta=3)

    def test_过期租约可被顶替(self):
        past = (B.now_dt() - dt.timedelta(seconds=10)).replace(microsecond=0).isoformat()
        self.assertAlmostEqual(B.lease_left({"holder": "x", "expires": past}), -10, delta=2)

    def test_剩余超出TTL加容差判不可信(self):
        far = (B.now_dt() + dt.timedelta(seconds=B.LEASE_TTL_SEC + B.CLOCK_SKEW_SEC + 60)).isoformat()
        self.assertEqual(B.lease_left({"holder": "x", "expires": far}), 0.0)
        B.write_json(B.LOCK, {"holder": "x", "expires": far})
        self.assertEqual(B.lease(), {}, "坏租约必须让位，否则热备永远接不了班")
        self.assertEqual(B.acquire_lease("hot-standby")["holder"], "hot-standby")

    def test_旧格式无偏移租约仍可读(self):
        legacy = (dt.datetime.now() + dt.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%S")
        self.assertGreater(B.lease_left({"holder": "x", "expires": legacy}), 0)

    def test_租约解析失败按过期(self):
        self.assertEqual(B.lease_left({"holder": "x", "expires": "坏"}), 0.0)


class TestNoRawStringCompare(unittest.TestCase):
    """只堵一半等于没堵：后端不得再有「拿时间戳直接字符串比较 / strptime 固定格式」的残留。"""

    FILES = ["bus_core.py", "bus_projects.py", "bus_guard.py", "bus_web.py",
             "bus_lead_notify.py", "bus_lineage.py", "bus_mcp.py"]

    def test_没有19截断(self):
        bad = []
        for f in self.FILES:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:  # with 关闭句柄，避免 ResourceWarning
                for i, l in enumerate(fh, 1):
                    if "[:19]" in l and "parse_ts" not in l:
                        bad.append("%s:%d" % (f, i))
        self.assertEqual(bad, [], "时间戳不得再用 [:19] 截断掉偏移（豁免只允许出现在 parse_ts 函数体内）")

    def test_没有strptime固定格式解析时间戳(self):
        bad = []
        for f in self.FILES:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                for i, l in enumerate(fh, 1):
                    if "strptime" in l and "%Y-%m-%dT%H:%M:%S" in l:
                        bad.append("%s:%d" % (f, i))
        self.assertEqual(bad, [], "改用 parse_ts，固定格式解析挡不住带偏移的值")

    def test_写时间戳一律带偏移(self):
        # 单引号/双引号写法都要抓（qoder1 阻断 3：bus_guard.py 用单引号曾绕过双引号字面量匹配）
        pat = re.compile(r"strftime\(['\"]%Y-%m-%dT%H:%M:%S['\"]\)")
        bad = []
        for f in self.FILES:
            with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                for i, l in enumerate(fh, 1):
                    if pat.search(l):
                        bad.append("%s:%d" % (f, i))
        self.assertEqual(bad, [], "写入必须用 now_iso()/iso_after()/astimezone().isoformat()，它们带偏移")


class TestStatusExposesSkew(unittest.TestCase):
    def setUp(self):
        self.saved = [(B, n, getattr(B, n)) for n in ("ROOT", "PEERS", "INBOX", "OUTBOX", "LOG")]
        self.tmp = tempfile.mkdtemp(prefix="5509s-")
        B.ROOT = self.tmp
        B.PEERS, B.INBOX, B.OUTBOX, B.LOG = (os.path.join(self.tmp, d) for d in ("peers", "inbox", "outbox", "log"))
        B.ensure_dirs()

    def tearDown(self):
        for m, n, v in self.saved:
            setattr(m, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_时钟异常的机器要在状态里看得见(self):
        future = (B.now_dt() + dt.timedelta(hours=12)).replace(microsecond=0).isoformat()
        B.write_json(os.path.join(B.PEERS, "bad1.json"), {"peer": "bad1", "model": "m", "status": "idle", "last_seen": future})
        B.write_json(os.path.join(B.PEERS, "ok1.json"), {"peer": "ok1", "model": "m", "status": "idle", "last_seen": B.now_iso()})
        st = B.status()
        self.assertIn("bad1", st["clock_skew"])
        self.assertNotIn("ok1", st["clock_skew"])
        self.assertEqual(st["clock_skew_n"], 1)
        bad = next(p for p in st["peers"] if p["peer"] == "bad1")
        self.assertTrue(bad.get("clock_skew"))
        self.assertFalse(bad["online"], "时间戳不可信的机器不能冒充在线")
        self.assertTrue(next(p for p in st["peers"] if p["peer"] == "ok1")["online"])


class TestCmdCreatedTsFallback(unittest.TestCase):
    """qoder1 阻断 1 的回归钉子：implementation_snapshot 的 created 坏值不得让 AttributeError 逃出 except 元组。"""

    def test_created为空串与null都回退到_ts_of(self):
        import bus_projects as BP
        for bad in ("", None):
            self.assertEqual(BP._cmd_created_ts({"created": bad}, "20261001-080000-abcd"),
                             BP._ts_of("20261001-080000-abcd"),
                             "created=%r 必须回退 _ts_of 而不是抛 AttributeError" % (bad,))

    def test_created合法值按解析结果(self):
        import bus_projects as BP
        ts = BP._cmd_created_ts({"created": "2026-10-01T08:00:00+08:00"}, "x")
        self.assertAlmostEqual(ts, dt.datetime(2026, 10, 1, 8, 0, 0, tzinfo=SH).timestamp(), delta=1)

    def test_rec_ts同一时刻无偏移与带偏移等价(self):
        # 不依赖宿主时区：把 _local_tz 桩成 +08:00，无偏移串与带 +08:00 的同一墙钟必须等价（legacy 按本机时区解释）
        real_tz = B._local_tz
        try:
            B._local_tz = lambda: SH
            old = {"created": "2026-10-01T08:00:00"}
            new = {"created": "2026-10-01T08:00:00+08:00"}
            self.assertAlmostEqual(B.rec_ts(old, "created"), B.rec_ts(new, "created"), delta=1)
        finally:
            B._local_tz = real_tz
        self.assertEqual(B.rec_ts({"created": ""}, "created"), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
