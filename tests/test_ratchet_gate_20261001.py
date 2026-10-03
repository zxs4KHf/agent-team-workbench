"""门禁接设计护栏（ADR 0011）：动了 web/v2 就跑 smoke_design_ratchet.mjs，超限即门禁失败。

在临时目录里搭一个最小工程（真实护栏脚本 + 极小的 web/v2 + 上限 json），不碰真实仓库。
跑法：py -3 tests/test_ratchet_gate_20261001.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bus_build as BB  # noqa: E402

KEYS = ("inline_style", "hardcoded_color", "font_size_value", "button_class", "inner_html_assign", "raw_radius")


class RatchetGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wb-ratchet-gate-"))
        (self.tmp / "tests").mkdir()
        (self.tmp / "web" / "v2" / "js").mkdir(parents=True)
        shutil.copy(ROOT / "tests" / "smoke_design_ratchet.mjs", self.tmp / "tests")
        limits = {k: 0 for k in KEYS}
        limits["inline_style"] = 1
        (self.tmp / "tests" / "design_ratchet.json").write_text(
            json.dumps({"limits": limits, "why": {}}), encoding="utf-8")
        self.page = self.tmp / "web" / "v2" / "js" / "page.js"
        self.page.write_text("export const a = '<i style=\"x\"></i>';\n", encoding="utf-8")
        self.policy = mock.patch.object(BB, "policy", lambda key, default=None: default)
        self.policy.start()

    def tearDown(self):
        self.policy.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gates(self, files):
        return BB.run_gates(str(self.tmp), files)

    def ratchet_items(self, out):
        return [i for i in out["items"] if i["gate"] == "design_ratchet"]

    def test_within_limit_passes(self):
        out = self.gates(["web/v2/js/page.js"])
        self.assertEqual(len(self.ratchet_items(out)), 1)
        self.assertTrue(out["ok"], out)

    def test_over_limit_fails_gate(self):
        self.page.write_text("export const a = '<i style=\"x\"></i><b style=\"y\"></b>';\n", encoding="utf-8")
        out = self.gates(["web/v2/js/page.js"])
        self.assertFalse(out["ok"])
        self.assertIn("inline_style", self.ratchet_items(out)[0]["out"])

    def test_backend_only_change_skips_ratchet(self):
        (self.tmp / "x.py").write_text("X = 1\n", encoding="utf-8")
        self.assertEqual(self.ratchet_items(self.gates(["x.py"])), [])

    def test_vendor_change_skips_ratchet(self):
        (self.tmp / "web" / "v2" / "vendor").mkdir()
        (self.tmp / "web" / "v2" / "vendor" / "lib.js").write_text("export {};\n", encoding="utf-8")
        self.assertEqual(self.ratchet_items(self.gates(["web/v2/vendor/lib.js"])), [])

    def test_repo_without_ratchet_is_unaffected(self):
        (self.tmp / "tests" / "smoke_design_ratchet.mjs").unlink()
        out = self.gates(["web/v2/js/page.js"])
        self.assertEqual(self.ratchet_items(out), [])
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
