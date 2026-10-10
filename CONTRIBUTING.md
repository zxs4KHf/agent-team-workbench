# 一起开发

欢迎一起改 Agent 协作台。下面是改代码前需要知道的全部规矩；给 AI Agent 的速查版在 [AGENTS.md](AGENTS.md)，完整流程在 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 准备环境

- Windows 10/11、Python 3.11+、Git、Node.js 20+（前端测试用 Node 自带模块跑，不需要 `npm install`）。
- 依赖：`py -3 -m pip install -r requirements.txt`；要跑浏览器测试再装 `py -3 -m pip install -r requirements-dev.txt` 和 `py -3 -m playwright install chromium`。
- 目前只支持 Windows：服务守护、子进程作业对象、启动脚本都是 Windows 专用。想补 macOS / Linux 支持非常欢迎，先开个 Issue 讨论。

## 改代码的流程

1. **开分支、开独立工作副本**，不在主目录改：
   `git worktree add -b fix/<主题>-<日期> .wt/fix-<主题>-<日期> main`（类型用 `fix` `feat` `chore` `docs`）。
2. **修 bug 先写能复现的测试**，新测试放 `tests/test_<主题>_<日期>.py`。测试只能用临时目录，不能读 `peers/` `projects/` 这些运行数据。
3. **改完跑测试**：`py -3 scripts/run_tests.py`（完整，含浏览器测试）。只改后端时可以先跑 `--fast`，提 PR 前跑一次完整的。
   - 失败了先在 main 上复跑；main 也失败的登记到 [docs/FLAKY.md](docs/FLAKY.md)。**不删断言刷绿。**
4. **前端改动不让设计约束计数上升**：`node tests/smoke_design_ratchet.mjs`（内联样式、写死颜色、字号写法、按钮组合、`innerHTML =`、非变量圆角）。数降了就跑 `--update` 收紧。颜色、字号、间距只用 `web/v2/styles/tokens.css` 的变量。
5. **提 PR**：说清改了什么、为什么、怎么验证、怎么回滚；验证里区分 **[已验证]**（真跑过，附命令和输出）和 **[推断]**（没跑过）。作者不审自己的代码。

## 代码放哪

| 要做的事 | 放在 |
|---|---|
| 协作流程（派发、合并、决策、施工） | 对应的 `bus_*.py`，见 README 的代码地图 |
| 接外部数据源或通知渠道 | `plugins/<id>/`，见 [docs/PLUGINS.md](docs/PLUGINS.md)；核心模块不 import 插件代码 |
| 新的 HTTP 接口 | `bus_web.py` 只做转发，业务逻辑放独立模块 |
| 前端新页面 | `web/v2/<名字>.html` + `web/v2/js/<名字>.js`，不往 `app.js` / `main.js` 里堆 |
| 重要的设计决定 | `docs/adr/NNNN-<主题>.md` |

## 不要做

- 不要提交运行数据（`log/ inbox/ outbox/ peers/ console/ projects/ attachments/ events/ intake/ feedback/ .runtime/ plugins.local.json`，已在 `.gitignore`）。
- 不要把令牌、账号、本机私人路径写进代码或文档；私人配置放 `plugins.local.json` 或环境变量。
- 源码是 CRLF 换行，用脚本改文件时先检测换行符，不要整文件改换行。

## 反馈问题

工作台任意页面右下角的「反馈」按钮可以直接生成一个预填好的 GitHub Issue（只带你写的话和版本、浏览器等环境信息）。也可以直接在仓库开 Issue。
