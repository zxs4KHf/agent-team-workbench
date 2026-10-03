"""施工单卡住（2026-10-02 用户：「催审不好用、过期的清不掉、改派不好使」）。
- 施工单改派只给「正在等任务」且属于「能施工」分组的人；没有就明说，不再停在假在线的人身上；
- 页面「改派」对施工单走施工专用改派（新 worktree），不是通用改派；
- 没有提交的施工单点「催审」，回答里写明是谁没交；
- 页面：没交的过期施工单给「改派施工 / 取消」，进行中页有「清理已过期」。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402
import bus_core as B          # noqa: E402
import bus_reassign as RA     # noqa: E402
import bus_review as RV       # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontsrc import front_src  # noqa: E402  ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = {"slow": "flash", "good": "opus", "good2": "opus", "gone": "opus"}


class StuckBuild(Base):
    def setUp(self):
        super().setUp()
        self.g = mock.patch.object(B, "peer_group", lambda p: GROUPS.get(p, "other"))
        self.g.start()
        self.r = mock.patch.object(RA, "builder_groups", lambda: ["opus"])
        self.r.start()

    def tearDown(self):
        self.r.stop()
        self.g.stop()
        super().tearDown()

    def listen(self, peer):
        B.heartbeat(peer, "idle", "m", "", B.now_iso(), 60)

    def test_builder_pick_needs_listening_and_builder_group(self):
        self.listen("slow")                         # 在等，但不是施工组
        B.heartbeat("good", "idle", "m")            # 施工组，在线但没在等
        self.assertEqual(RA.pick_free_target("gone", [], role="builder"), "")
        self.listen("good2")
        self.assertEqual(RA.pick_free_target("gone", [], role="builder"), "good2")
        self.assertIn(RA.pick_free_target("gone", ["good2"]), ("slow", ""))   # 非施工单不限组

    def test_no_builder_error_says_why(self):
        B.write_json(os.path.join(B.OUTBOX, "B1.meta.json"), {"id": "B1", "mode": "build", "peers": ["gone"], "question": "q"})
        import bus_build as BB
        with mock.patch.object(BB, "record", lambda tid: {"teammates": {"gone": {}}}):
            with self.assertRaises(ValueError) as cm:
                RA._reassign_build("B1", "gone", by="user", listening_only=True)
        self.assertIn("没有正在等任务的施工 Agent", str(cm.exception))
        self.assertIn("opus", str(cm.exception))

    def test_nudge_review_on_build_without_commit_names_builder(self):
        import bus_build as BB
        builds = os.path.join(self.tmp, "builds")
        os.makedirs(builds)
        B.write_json(os.path.join(builds, "B2.json"), {"task_id": "B2", "status": "building", "teammates": {"opus5": {"commit": ""}, "ds1": {"commit": ""}}})
        B.write_json(os.path.join(B.OUTBOX, "B2.meta.json"), {"id": "B2", "mode": "build", "peers": ["ds1"], "question": "q"})
        with mock.patch.object(BB, "BUILD_DIR", builds):
            r = RV.review_now("B2")
        self.assertEqual(r["dispatched"], [])
        self.assertIn("ds1 还没交提交", r["errors"][0]["error"])

    def test_web_reassign_routes_build_to_build_path(self):
        src = open(os.path.join(ROOT, "bus_web.py"), encoding="utf-8").read()
        i = src.index('if kind == "reassign":')
        block = src[i:i + 900]
        self.assertIn('meta.get("mode") == "build"', block)
        self.assertIn("RA._reassign_build(", block)
        self.assertIn("RA.pick_free_target(", block)

    def test_frontend_buttons(self):
        views = front_src("views.js")
        app = front_src("app.js")
        i = views.index("mode === 'build' && !pr.answered")
        self.assertLess(i, views.index("items.push(b('nudge-review', '催审'"), "没交的过期施工单先命中「改派施工」分支，不再落到催审")
        self.assertIn("改派施工", views)
        self.assertIn('data-action="clear-expired"', views)
        self.assertIn("'clear-expired': async", app)


if __name__ == "__main__":
    unittest.main()
