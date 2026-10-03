// Bridge 自定义工具入口：node tool.js <action>，参数从 SHUNCODE_TOOL_ARGS 读取，转交 bus_tool.py
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const action = process.argv[2];
// 参数来源：Bridge 走环境变量 SHUNCODE_TOOL_ARGS；手动测试可走 stdin
let args = process.env.SHUNCODE_TOOL_ARGS || "";
if (!args) {
  try { args = require("node:fs").readFileSync(0, "utf8"); } catch { args = ""; }
}
if (!args.trim()) args = "{}";
const script = path.join(__dirname, "bus_tool.py");
const candidates = process.platform === "win32" ? [["py", ["-3", script, action]], ["python", [script, action]]] : [["python3", [script, action]]];

let last;
for (const [exe, argv] of candidates) {
  last = spawnSync(exe, argv, { input: args, encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" }, maxBuffer: 8 * 1024 * 1024 });
  if (!last.error) break;
}
if (!last || last.error) {
  process.stderr.write("找不到 Python 解释器\n");
  process.exit(2);
}
process.stdout.write(last.stdout || "");
process.stderr.write(last.stderr || "");
process.exit(last.status ?? 1);
