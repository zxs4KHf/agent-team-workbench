# 给 AI Agent 的速查

你在改 Agent 协作台的代码。先读这一页，完整规则见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 必须做

1. **在自己的分支和工作副本里改。** `git worktree add -b <类型>/<主题>-<日期> .wt/<类型>-<主题>-<日期> main`。类型：`fix` `feat` `chore` `docs`；施工任务由 `bus_build` 自动建 `task/*`。主目录只放 main，不要在主目录改代码。
2. **改完跑测试。** `py -3 scripts/run_tests.py`（或 `--fast`）。失败先在 main 上复跑：main 也失败的登记到 `docs/FLAKY.md`，**不许删断言刷绿**。
3. **加测试。** 修 bug 先写能复现的测试；新模块在 `tests/test_<主题>_<编号或日期>.py`。测试只能用临时目录，**不能依赖 `peers/` `projects/` 等真实运行数据**。
4. **审查绑定提交号。** 报告写 `PASS @<短哈希>` 或 `RETURN @<短哈希>`。作者不审自己的代码。
5. **说清证据。** 报告里区分 [已验证]（你真跑过）和 [推断]（没跑过）。
6. **视频资料分析、关卡视频拆解或方法论视频研究时，** 读取 [视频证据分析方法](docs/methods/video-analysis.md)，将全文或本地副本附到派发上下文，成果经来源插件接口回传，由宿主保存讨论与验收。

## 不要做

- 不要提交运行数据：`log/ inbox/ outbox/ peers/ console/ projects/ attachments/ events/ intake/ feedback/ .runtime/ plugins.local.json`（已在 `.gitignore`）。
- 不要在 main 上解冲突、不要 `--force` 推送、不要改别人分支的历史。
- 不要让核心模块 import 插件代码，也不要在核心里写死其它项目的路径。跨项目只走 HTTP 接口和 `events/`。
- 前端改动不许让 `tests/smoke_design_ratchet.mjs` 的计数上升（内联样式、写死颜色、字号写法、按钮组合、`innerHTML =`、非变量圆角）。降了就跑 `--update` 收紧（ADR 0011）。
- 不要在 `app.js` 里继续堆新功能。新页面做成独立页面（参考 `web/v2/hub.html`）或插件页面。
- 不要把令牌、账号、私人路径写进代码或文档。私人配置放 `plugins.local.json` 或环境变量 `BUS_WORKSPACE`；`workspace.json` 是入库文件，不要往里写私人路径。

## 代码放哪

| 要做的事 | 放在 |
|---|---|
| 协作流程（派发、合并、决策、施工） | 对应的 `bus_*.py`，见 README 代码地图 |
| 接一个外部数据源或通知渠道 | `plugins/<id>/`，见 [docs/PLUGINS.md](docs/PLUGINS.md) |
| 新的 HTTP 接口 | `bus_web.py` 的 `do_GET` / `do_POST`，业务逻辑放在独立模块里，`bus_web` 只做转发 |
| 需要被别的项目感知的状态变化 | `bus_events.emit("<领域>.<动作>", {...})` |
| 前端独立页面 | `web/v2/<名字>.html` + `web/v2/js/<名字>.js`，只用 `styles/tokens.css` 的变量 |
| 重要的设计决定 | `docs/adr/NNNN-<主题>.md` |

## 环境注意

- Windows，源码是 CRLF。用脚本改文件时先检测换行符，不要整文件改换行。
- 控制台默认 GBK：跑 Python 加 `-X utf8`，或设置 `PYTHONIOENCODING=utf-8`。
- 工作台端口 8765，MCP 端口 8791。测试请起临时端口，不要碰正在运行的服务。
- 从某个工作副本启动 `bus_web.py`，数据目录就是该副本自己的（全新、空的），适合做冒烟测试。

## 合入后

在 `CHANGELOG.md` 顶部追加一条：日期、分支@提交、审查人、改了什么、**怎么回滚**。
