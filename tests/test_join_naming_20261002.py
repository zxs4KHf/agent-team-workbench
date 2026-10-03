"""接入新 Agent：常用模型 + 名称推荐（2026-10-02 用户：「下拉不全，把常用模型都列为选项」「根据模型名和已经指派的 Agent 自动给出推荐的命名」）。
用 node 直接跑 app-editors.js 里的 nextPeerName / commonModels，喂假的状态，不碰真实数据。"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "web", "v2", "js")

HARNESS = r"""
const peers = %s, groups = %s;
globalThis.window = globalThis; globalThis.document = { querySelector: () => null, addEventListener() {}, documentElement: { dataset: {} } };
globalThis.localStorage = { getItem: () => null, setItem() {} };
const src = await import('node:fs').then(fs => fs.readFileSync(%s, 'utf8'));
const pick = name => { const i = src.indexOf(name); let d = 0, j = src.indexOf('{', i); for (; j < src.length; j++) { if (src[j] === '{') d++; else if (src[j] === '}' && !--d) break; } return src.slice(i, j + 1); };
const S = () => ({ peers, agent_groups: groups });
const agentList = () => peers;
const canon = m => String(m || '').toLowerCase();
const groupOfModel = model => { const k = canon(model); return groups.find(g => (g.match || []).some(m => k === m || k.startsWith(m + '-'))); };
const SHORT_NAMES = { 'gpt-6-astra': 'astra' };
eval(pick('function nextPeerName(') + ';' + pick('function commonModels(') + ';globalThis.R = { nextPeerName, commonModels };');
console.log(JSON.stringify({ sol: R.nextPeerName('gpt-6-sol'), astra: R.nextPeerName('gpt-6-astra'), unknown: R.nextPeerName('kimi-k9'),
  models: R.commonModels().map(m => [m.model, m.n]) }));
"""


@unittest.skipUnless(shutil.which("node"), "需要 node")
class JoinNaming(unittest.TestCase):
    def run_js(self, peers, groups):
        code = HARNESS % (json.dumps(peers), json.dumps(groups), json.dumps(os.path.join(JS, "app-editors.js")))
        r = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_follows_existing_naming_and_ranks_models(self):
        peers = [{"peer": f"sol-6-{i}", "model": "gpt-6-sol"} for i in (1, 2, 5)] + [
            {"peer": "astra1", "model": "gpt-6-astra"}, {"peer": "x", "model": "_other"}]
        groups = [{"id": "gpt-6-astra", "name": "GPT", "match": ["gpt-6-astra"]}, {"id": "glm", "name": "GLM", "match": ["glm-5.3"]}]
        r = self.run_js(peers, groups)
        self.assertEqual(r["sol"], "sol-6-6", "沿用同模型已有命名，接最大序号往后")
        self.assertEqual(r["astra"], "astra2", "组短名 + 第一个空闲序号")
        self.assertEqual(r["unknown"], "kimi1")
        self.assertEqual(r["models"][0], ["gpt-6-sol", 3], "按使用人数排序")
        self.assertIn(["glm-5.3", 0], r["models"], "分组里配置过、还没人用的模型也列出")
        self.assertNotIn("_other", [m for m, _ in r["models"]], "内部占位模型不列")


if __name__ == "__main__":
    unittest.main()
