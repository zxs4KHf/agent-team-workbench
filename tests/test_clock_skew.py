# 时钟/时区错乱防护：未来时间戳的心跳不能被判在线，也不能让 Leader 租约永不过期。
import datetime as dt, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bus_core as B  # noqa: E402


def iso(sec_from_now):
    return (dt.datetime.now() + dt.timedelta(seconds=sec_from_now)).strftime("%Y-%m-%dT%H:%M:%S")


class ClockSkew(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self._peers = B.PEERS; B.PEERS = self.tmp

    def tearDown(self):
        B.PEERS = self._peers; shutil.rmtree(self.tmp, ignore_errors=True)

    def seen(self, peer, sec_from_now):
        B.write_json(os.path.join(self.tmp, peer + ".json"), {"peer": peer, "status": "idle", "last_seen": iso(sec_from_now)})
        return B.peer_state(peer)

    def test_far_future_is_offline_and_flagged(self):
        s = self.seen("a", 11 * 3600)                 # 时区从 +8 切到 -4：旧心跳变成 12 小时后的「未来」
        self.assertFalse(s["online"]); self.assertTrue(s.get("clock_skew")); self.assertGreater(s["age_sec"], 10 ** 8)

    def test_small_skew_tolerated(self):
        s = self.seen("b", 60)
        self.assertTrue(s["online"]); self.assertNotIn("clock_skew", s); self.assertEqual(s["age_sec"], 0)

    def test_normal_ages(self):
        self.assertTrue(self.seen("c", -10)["online"])
        self.assertFalse(self.seen("d", -(B.HEARTBEAT_STALE_SEC + 60))["online"])

    def test_bad_or_missing_timestamp(self):
        B.write_json(os.path.join(self.tmp, "e.json"), {"peer": "e", "status": "idle", "last_seen": "garbage"})
        self.assertFalse(B.peer_state("e")["online"])
        B.write_json(os.path.join(self.tmp, "f.json"), {"peer": "f", "status": "idle"})
        self.assertFalse(B.peer_state("f")["online"])

    def test_future_controller_does_not_block_relay(self):
        self.seen("controller", 11 * 3600)
        self.assertGreaterEqual(B.heartbeat_age(B.peer_state("controller")["last_seen"])[0], B.RELAY_AFTER_SEC,
                                "Leader 心跳在未来时租约必须视为过期，否则热备永远接不了班")

    def test_heartbeat_skew_boundary(self):
        self.assertEqual(B.heartbeat_age(iso(B.CLOCK_SKEW_SEC - 10)), (0.0, False))
        self.assertEqual(B.heartbeat_age(iso(B.CLOCK_SKEW_SEC + 10)), (1e9, True))


class LeaseSkew(unittest.TestCase):
    """sota2 审查 7078：实际租约判定（lease / acquire_lease）也要防未来时间戳，并走完候选→抢租约。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (B.PEERS, B.LOCK)
        B.PEERS = os.path.join(self.tmp, "peers"); os.makedirs(B.PEERS)
        B.LOCK = os.path.join(self.tmp, "console", "controller.lock"); os.makedirs(os.path.dirname(B.LOCK))

    def tearDown(self):
        B.PEERS, B.LOCK = self._saved; shutil.rmtree(self.tmp, ignore_errors=True)

    def hold(self, holder, expires_in):
        B.write_json(B.LOCK, {"holder": holder, "epoch": 7, "since": iso(-60), "expires": iso(expires_in)})

    def beat(self, peer, sec_from_now):
        B.write_json(os.path.join(B.PEERS, peer + ".json"), {"peer": peer, "status": "idle", "last_seen": iso(sec_from_now)})

    def test_future_lease_is_void_and_standby_takes_over(self):
        self.hold("controller", 12 * 3600)            # 旧时区写下的租约：12 小时后才「过期」
        self.beat("controller", 12 * 3600)
        self.assertEqual(B.lease(), {})
        got = B.acquire_lease("standby")
        self.assertEqual(got["holder"], "standby")
        self.assertLessEqual(B.lease_left(got), B.LEASE_TTL_SEC + 1)

    def test_valid_lease_not_stolen_even_if_holder_heartbeat_skewed(self):
        self.hold("controller", B.LEASE_TTL_SEC - 60)  # 正常续约过的租约
        self.beat("controller", 12 * 3600)            # 心跳被标 skewed 也不影响租约本身
        self.assertEqual(B.acquire_lease("standby")["holder"], "controller")

    def test_lease_boundary(self):
        self.hold("controller", B.LEASE_TTL_SEC + B.CLOCK_SKEW_SEC - 30)   # 容差内：仍有效
        self.assertEqual(B.lease()["holder"], "controller")
        self.hold("controller", B.LEASE_TTL_SEC + B.CLOCK_SKEW_SEC + 30)   # 超出：时间戳不可信
        self.assertEqual(B.lease(), {})
        self.hold("controller", -5)                                       # 正常过期
        self.assertEqual(B.lease(), {})
        B.write_json(B.LOCK, {"holder": "controller", "expires": "garbage"})
        self.assertEqual(B.lease(), {})

    def test_holder_renews_normally(self):
        got = B.acquire_lease("controller")
        again = B.acquire_lease("controller")
        self.assertEqual((again["holder"], again["epoch"]), ("controller", got["epoch"]))
        self.assertGreater(B.lease_left(again), B.LEASE_TTL_SEC - 5)


class DeadlineSkew(unittest.TestCase):
    """ADR 0010：截止判断只认 deadline_ts（epoch），不拿本地时间字符串跨进程比较。
    实况 2026-10-01：派给 sota1/sota3 的评审在派发当秒就被改名为 .json.expired——判断方的进程比写入方快 12 小时。"""
    def setUp(self):
        import time as _t; self.t = _t
        self.tmp = tempfile.mkdtemp()
        self._saved = {n: getattr(B, n) for n in ("INBOX", "OUTBOX", "PEERS", "LOG")}
        for n in self._saved:
            setattr(B, n, os.path.join(self.tmp, n.lower()))
        B.ensure_dirs()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(B, n, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, peer, tid, **task):
        d = os.path.join(B.INBOX, peer); os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, tid + ".json"), {"id": tid, **task})
        return d

    def test_skewed_string_does_not_expire_fresh_task(self):
        # 写入方比判断方慢 12 小时：字符串看着早已过期，时间戳说还剩 30 分钟
        d = self.put("p", "t1", deadline=iso(-11 * 3600), deadline_ts=self.t.time() + 1800)
        self.assertEqual(B.mark_expired(), [])
        self.assertTrue(os.path.exists(os.path.join(d, "t1.json")))

    def test_ts_in_past_expires_even_if_string_in_future(self):
        d = self.put("p", "t2", deadline=iso(11 * 3600), deadline_ts=self.t.time() - 1)
        self.assertEqual(B.mark_expired(), [("p", "t2")])
        self.assertTrue(os.path.exists(os.path.join(d, "t2.json.expired")))

    def test_legacy_task_without_ts_uses_string(self):
        self.put("p", "old", deadline=iso(-60)); self.put("p", "new", deadline=iso(600))
        self.assertEqual(B.mark_expired(), [("p", "old")])

    def test_bad_ts_falls_back(self):
        self.assertFalse(B.deadline_passed({"deadline": iso(600), "deadline_ts": "soon"}))
        self.assertFalse(B.deadline_passed({"deadline": iso(600), "deadline_ts": True}))
        self.assertTrue(B.deadline_passed({"deadline": iso(-600), "deadline_ts": None}))

    def test_wait_task_claims_skewed_fresh_task(self):
        self.put("w", "t3", **{"from": "controller"}, to="w", mode="review", question="q", angle="", context="", created=iso(0), deadline=iso(-11 * 3600), deadline_ts=self.t.time() + 1800)
        B.wait_task("w", 1)
        self.assertTrue(os.path.exists(os.path.join(B.INBOX, "w", "t3.taken")), "时间戳未到期就必须能被领取")

    # ---- sota3 d56d：任务级（meta）截止与副本截止的一致性 ----
    def meta(self, tid, peers, **kw):
        m = {"id": tid, "from": "controller", "mode": "independent", "question": "q", "peers": peers, "min_answers": len(peers),
             "created": iso(-2 * 86400), **kw}
        B.write_json(os.path.join(B.OUTBOX, tid + ".meta.json"), m)
        return m

    def read_meta(self, tid):
        return B.read_json(os.path.join(B.OUTBOX, tid + ".meta.json"), None)

    def test_extend_meta_legacy_not_shrunk(self):
        orig = iso(3 * 3600)
        m = {"deadline": orig}
        B._extend_meta_deadline(m, *B.deadline_after(30))
        self.assertNotIn("deadline_ts", m, "旧 meta 不能凭空写入更早的 epoch")
        self.assertEqual(m["deadline"], orig, "字符串截止只延不缩")
        m2 = {"deadline": iso(60), "deadline_ts": self.t.time() + 60}
        B._extend_meta_deadline(m2, *B.deadline_after(30))
        self.assertAlmostEqual(m2["deadline_ts"] - self.t.time(), 1800, delta=5)

    def test_close_stale_closes_when_all_copies_dead(self):
        # 对照：确实能关，下面两例的「不关」才有意义
        self.meta("t4", ["a"], deadline=iso(-3600), deadline_ts=self.t.time() - 3600)
        self.put("a", "t4", deadline=iso(-3600), deadline_ts=self.t.time() - 3600)
        self.assertEqual([r["task_id"] for r in B.close_stale(24)], ["t4"])

    def test_close_stale_keeps_task_with_live_copy(self):
        # 旧任务改派后：meta 聚合截止已过，但 b 的副本还剩 30 分钟
        self.meta("t5", ["a", "b"], deadline=iso(-3600), deadline_ts=self.t.time() - 3600)
        self.put("a", "t5", deadline=iso(-3600), deadline_ts=self.t.time() - 3600)
        d = self.put("b", "t5", deadline=iso(1800), deadline_ts=self.t.time() + 1800)
        self.assertEqual(B.close_stale(24), [])
        self.assertTrue(os.path.exists(os.path.join(d, "t5.json")))

    def test_requeue_extends_meta_and_survives_close_stale(self):
        self.meta("t6", ["a"], deadline=iso(-3600), deadline_ts=self.t.time() - 3600)
        d = os.path.join(B.INBOX, "a"); os.makedirs(d, exist_ok=True)
        B.write_json(os.path.join(d, "t6.json.expired"), {"id": "t6", "deadline": iso(-3600), "deadline_ts": self.t.time() - 3600})
        B.requeue("t6", "a", 30)
        self.assertAlmostEqual(self.read_meta("t6")["deadline_ts"] - self.t.time(), 1800, delta=5)
        self.assertEqual(B.close_stale(24), [])
        self.assertTrue(os.path.exists(os.path.join(d, "t6.json")))

    def test_deadline_after_writes_both(self):
        s, ts = B.deadline_after(30)
        self.assertAlmostEqual(ts - self.t.time(), 1800, delta=5)
        # ADR 0010 第 2 步：人读的那串现在带偏移（25 字符），且必须能被 parse_ts 解析回同一时刻
        self.assertRegex(s, r"[+-]\d{2}:\d{2}$")
        self.assertAlmostEqual(B.parse_ts(s).timestamp(), ts, delta=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
