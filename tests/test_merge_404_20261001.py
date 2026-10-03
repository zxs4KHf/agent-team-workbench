"""合并稿 404 根治（2026-10-01）：实测 38 条合并稿点开 404——20 条落在别处、18 条从没写出来。

1. 按文件名兜底：错误日期目录、workbench/bus/log、<仓库目录名>/log 下的旧稿能找到；
2. 只认带任务号的文件名，不会把别的同名文件当合并稿；
3. post 登记 merge_path 前文件必须存在，否则报错告诉 Leader 正确落点（不再只登记路径）；
4. Leader 提示词里的落点改成 log/<YYYY-MM-DD>/。
跑法：py -3 tests/test_merge_404_20261001.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_multi_leader_20261001 import Base  # noqa: E402  复用临时目录夹具（B.ROOT/LOG/PROJECTS 都指向临时目录）
import bus_core as B      # noqa: E402
import bus_projects as P  # noqa: E402

TID = "20260929-155031-bbce"


def put(*parts, text="# 合并稿\n\n## 最终方案\n\nok\n"):
    full = os.path.join(B.ROOT, *parts)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return full


class Merge404(Base):
    def test_found_in_wrong_date_folder(self):
        want = put("log", "20260929", f"{TID}.merge.md")       # 日期目录没连字符
        self.assertEqual(P.resolve_merge_path(f"log/2026-09-29/{TID}.merge.md"), (want, "legacy_search"))

    def test_found_under_workbench_bus_log(self):
        want = put("workbench", "bus", "log", "20260929", f"{TID}.merge.md")
        self.assertEqual(P.resolve_merge_path(f"log/2026-09-29/{TID}.merge.md")[0], want)

    def test_found_under_repo_name_prefix(self):
        want = put(os.path.basename(os.path.normpath(B.ROOT)), "log", "2026-10-01", f"{TID}.merge.md")
        self.assertEqual(P.resolve_merge_path(f"log/2026-09-29/{TID}.merge.md")[0], want)

    def test_canonical_still_wins(self):
        canon = put("log", "2026-09-29", f"{TID}.merge.md")
        put("workbench", "bus", "log", "20260929", f"{TID}.merge.md", text="旧副本")
        self.assertEqual(P.resolve_merge_path(f"log/2026-09-29/{TID}.merge.md"), (canon, "canonical"))

    def test_only_task_named_files_are_searched(self):
        put("log", "2026-09-29", "README.md")
        self.assertEqual(P.resolve_merge_path("log/2026-09-30/README.md"), ("", "miss"))

    def test_post_rejects_unwritten_merge_path(self):
        p = P.create_project("demo", [], "independent", [], "")
        with self.assertRaises(ValueError) as cm:
            P.post(p["slug"], TID, "merged", "标题", "摘要", f"log/2026-09-29/{TID}.merge.md")
        self.assertIn("合并稿文件不存在", str(cm.exception))
        self.assertIn(f"log/<YYYY-MM-DD>/{TID}.merge.md", str(cm.exception))
        put("log", "2026-09-29", f"{TID}.merge.md")
        P.post(p["slug"], TID, "merged", "标题", "摘要", f"log/2026-09-29/{TID}.merge.md")
        rec = next(d for d in P.decisions(p["slug"]) if d["task_id"] == TID)
        self.assertEqual(rec["merge_path"], f"log/2026-09-29/{TID}.merge.md")

    def test_leader_prompt_points_to_canonical_folder(self):
        src = open(P.__file__, encoding="utf-8").read()
        self.assertIn("写合并稿到 log/<YYYY-MM-DD>/<task_id>.merge.md", src)
        self.assertNotIn("写合并稿到 workbench/bus/log", src)


if __name__ == "__main__":
    unittest.main()
