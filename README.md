# Agent 协作台（AgentTeamWorkbench）

**把多个网页 AI 窗口编成一支团队：你提问题，Leader 派给几个 Agent 分头回答，合并成一份方案；你拍板后，Agent 在各自的分支里施工，互审通过才进主线。**

```
  你 ──提问/拍板──▶  浏览器工作台 (8765)  ◀──实时──  扩展与来源：收件箱 · 体验反馈 · 插件
                          │  指令队列
                          ▼
                     Leader（bus_console）──派发/收集──▶  Agent 们（通过 MCP 8791 接入）
                          │                                   │
                          ▼                                   ▼
                  合并文档 log/…merge.md          施工：每人一个 git 工作副本 → 互审 → 合入 main
                          │
                          ▼
                  events/ 事件记录 ──▶ 通知插件、其它项目按需订阅
```

## 5 分钟上手

**需要：** Windows 10/11、Python 3.11+（`py -3`）、Git。跑测试另需 Node.js 与 Playwright（可选）。

```powershell
git clone https://github.com/zxs4KHf/agent-team-workbench.git
cd agent-team-workbench
py -3 -m pip install -r requirements.txt
start_all.bat
```

`start_all.bat` 拉起服务守护：它启动 MCP 服务（8791）和工作台（8765），崩溃自动重启，然后打开 <http://127.0.0.1:8765>。
第一次启动会自动建好运行目录，并在本机生成 MCP 主令牌 `console/mcp_token.txt`（不会打印，也不进 git）。

**接上第一批 Agent：**

1. **决定接入地址。** 本机的宿主（Claude 桌面、Claude Code、Cursor 等）什么都不用填，默认就是 `http://127.0.0.1:8791`。网页版 AI 连不到你的电脑，要准备一个指到本机 8791 的公网 HTTPS 地址（例如 Cloudflare Tunnel），填到 **设置 → 服务 → 接入地址（Bridge）**。
2. **接入 Leader。** 左侧 **Agent → Leader 接入**，把链接加到一个宿主的 MCP 服务器里。Leader 负责派发、合并、跟进。
3. **接入 Agent。** **Agent → 接入 Agent**：点一个模型，名字自动编号，复制链接或完整提示词给对应的宿主。接几个都行，同一模型的归到一组。
4. **派一个问题。** 顶栏 **派发任务**（快捷键 N）。想最快拿到答案选「抢单」，想多个视角选「各自独立回答」。

| 我想… | 在哪 |
|---|---|
| 看进行中的任务、拍板合并结果 | 左侧 **进行中 / 决策** |
| 让 Agent 动手改代码 | 在决策上点「采纳」，进入「已采纳，等待落地」后派施工：每人一个 git 工作副本，互审通过才合入 |
| Agent 掉线了 | **Agent → 唤醒掉线的 N 个**：复制唤醒语粘贴到它的对话里 |
| 接外部数据源或通知渠道 | 顶栏 **扩展与来源 → 插件**（写法见 [docs/PLUGINS.md](docs/PLUGINS.md)） |
| 用着不顺手 | 任意页面右下角 **反馈**（Alt+B）：本机留一份，也可以一键发成 GitHub Issue |

## 代码地图

按「一件事一个文件」组织。模块都在根目录（外部启动脚本按文件名引用，暂不搬进包里）。

### 核心：协作流程

| 文件 | 负责 |
|---|---|
| `bus_core.py` | 总线内核：peer 心跳、inbox/outbox、任务生命周期、派发与收集 |
| `bus_projects.py` | 项目与决策流：决策记录、指令队列、Leader 租约与 `bus_console` |
| `bus_project_store.py` · `bus_locks.py` · `bus_implementation_lock.py` | 并发安全：项目事务、跨进程文件锁、原子写 |
| `bus_lineage.py` | 决策谱系：人工交接、关联子单 |
| `bus_lead_notify.py` | Leader 显示名与交接事件 |
| `bus_build.py` · `bus_build_safety.py` | 施工：每人一个 worktree、审查绑定精确提交、临时副本合并 + 门禁 + 快进 |

### 接入：Agent 与人

| 文件 | 负责 |
|---|---|
| `bus_mcp.py` · `start_named_mcp.py` | MCP 服务（Streamable HTTP；Bearer 令牌或 `/mcp/<令牌>` 路径直连），给网页 Agent 用的 11 个工具 |
| `named_workspace_io.py` · `runtime_exec.py` | `ws_*` 工具的读写与受限命令执行 |
| `bus_web.py` | 浏览器工作台的 HTTP 服务：页面、API、SSE 实时推送、请求来源校验 |
| `web/v2/` | 工作台前端（`index.html` 主界面，`hub.html` 扩展与来源页） |
| `plugins/arena/` | Arena 可选插件：控制面服务（`server/`）、插件页面（`web/`），关掉不影响工作台（ADR 0014） |
| `bus_cli.py` · `bus_tool.py` · `tool.js` | 命令行工具 |

### 扩展层（2026-09-28 新增）

| 文件 | 负责 |
|---|---|
| `bus_events.py` | **事件记录** `events/日期.jsonl`：只追加，其它项目按游标订阅（[ADR 0003](docs/adr/0003-events-outbox.md)） |
| `bus_plugins.py` · `plugins/` | **插件**：一个文件夹一个插件，独立进程，开关即时生效（[写插件](docs/PLUGINS.md)） |
| `bus_intake.py` | **收件箱**：外部条目先等你确认，再变成 Leader 指令 |
| `bus_feedback.py` | **体验反馈**：随手记 → 一键转任务 → 改完标记解决（[ADR 0004](docs/adr/0004-feedback-loop.md)） |

### 运维

| 文件 | 负责 |
|---|---|
| `bus_guard.py` | 服务守护：巡检、连续失败自动拉起、网页一键重启 |
| `bus_settings.py` | 设置页读写 |
| `lead_keepalive.py` · `controller_activity.py` · `bus_daemon.py` | Leader 保活与活动脉冲 |
| `start_*.bat` · `start_workbench_hidden.vbs` | Windows 启动脚本 |
| `tools/` | 发布校验、代理自检、角色表模板 `roles.json`（本机实例在 `console/roles.json`，缺失时自动回退到模板） |
| `scripts/run_tests.py` | 一键跑全部测试 |
| `scripts/gc_builds.py` | 清理停滞的施工：删工作副本与 task 分支，未合入的提交打存档标签、未提交改动存补丁（默认只预演） |

## 数据放在哪（都不进 git）

| 目录 | 内容 |
|---|---|
| `projects/` | 各项目的决策记录 |
| `log/` | 每个任务的问答与合并文档 |
| `inbox/` `outbox/` `peers/` | 任务流转与 Agent 心跳 |
| `console/` | 指令队列、运行态、凭据（`mcp_token.txt`） |
| `events/` `intake/` `feedback/` | 事件记录、收件箱、体验反馈 |
| `plugins.local.json` · `.runtime/` | 插件开关与配置、插件私有状态 |

代码和数据分开之后：换分支、回滚代码都不会动你的任务历史；Agent 施工时的 `git add -A` 也不会把运行数据带进提交。原因见 [ADR 0005](docs/adr/0005-runtime-data-out-of-git.md)。

## 怎么改这个项目

**所有改动都走：开分支 → 独立工作副本 → 测试 → 另一个 Agent 审查（绑定提交号）→ 合入 main → 记录改了什么、怎么回滚。** 详见 [docs/WORKFLOW.md](docs/WORKFLOW.md)；给 AI Agent 的速查在 [AGENTS.md](AGENTS.md)。

```powershell
py -3 scripts/run_tests.py            # 全部测试
py -3 scripts/run_tests.py --fast     # 跳过浏览器测试
```

## 文档索引

| 文档 | 看什么 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层、数据流、模块边界、依赖规则 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 接下来做什么 |
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | 分支、审查、合并、版本规范 |
| [docs/PLUGINS.md](docs/PLUGINS.md) | 怎么写一个插件 |
| [docs/ARENA-DEPENDENCIES.md](docs/ARENA-DEPENDENCIES.md) | 外部组件 ArenaX 的依赖图：哪里耦合、换掉要改哪里 |
| [docs/adr/](docs/adr/) | 重要决定的来龙去脉（为什么这么做、放弃了什么） |
| [docs/FLAKY.md](docs/FLAKY.md) | 不稳定测试登记 |
| [PROTOCOL.md](PROTOCOL.md) · [JOIN-PROMPT.md](JOIN-PROMPT.md) | 总线协议、Agent 接入提示词 |

## 安全边界

- 工作台只监听本机，只接受发往 `127.0.0.1` / `localhost` 的请求，写操作必须同源——你浏览的其它网站不能替你派任务。局域网访问需显式设置 `WORKBENCH_ALLOWED_HOSTS`。
- `ws_*` 工具拦截路径逃逸、凭据文件和总线控制目录，但**不是操作系统沙箱**：`ws_run` 执行的代码等同本机代码。
- MCP 令牌在 `console/mcp_token.txt`，不要写进提示词、文档或聊天记录。
