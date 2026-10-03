# 0014 Arena 做成可选插件

- 状态：已实施（2026-10-02，feat/arena-plugin-20261002；提议同日，用户：「Arena 相关的部分不应该做在整个页面里，应该做成插件，它不是我们硬性必须要的东西」）
- 前置已完成：Agent 分组改由工作台自己管（`bus_agent_groups`，`console/agent_groups.json`），Agent 页、发起页、Reviewer / 施工组、自动分配都不再向 Arena 要组表。

## 现在 Arena 和核心缠在哪

| 位置 | 内容 |
|---|---|
| `web/v2/js/arena.js` + `app.js` | 左侧导航「Arena」五个页面（模型组 / 榜单 / 账号 / 会话 / 路由与 Key）、ARENA_ACTIONS、轮询 |
| `bus_web.py` | `/api/arena/*` 全部转发到 9091 |
| `bus_guard.py` | 守护 `arena`（9091）与 `arenax`（中继）两个服务 |
| `arena/` 目录 | 控制面服务本体、会话池、账号、榜单 |
| 首页 / Agent 页 | 「从会话池自动接入」按钮、Arena 会话数（本次已从 Agent 页去掉） |

## 决定

按 ADR 0002 的插件形态（文件夹 + 清单 + 独立进程，只走 HTTP）把 Arena 挪到 `plugins/arena/`：

1. **服务**：`arena/` 整体移到 `plugins/arena/`，插件清单里声明进程（9091 控制面、可选的中继），由插件管理器而不是 `bus_guard` 拉起；插件关闭时这两个服务不启动。
2. **页面**：五个 Arena 页面做成插件自带页面（`plugins/arena/web/`），左侧导航只在插件启用时出现一个「Arena」入口，打开插件页；`app.js` / `arena.js` 里的 Arena 代码移出核心。
3. **接口**：`/api/arena/*` 不再由 `bus_web` 写死转发，改为插件代理（插件启用才有）。
4. **核心对 Arena 零依赖**：核心模块不 import 插件代码（AGENTS.md 规则）；Agent 接入的「从会话池自动接入」改为插件往 Agent 页挂一个可选按钮（插件关时不显示）。

## 分步（每步可单独合入、回滚）

1. 核心去依赖：分组（已完成）→ 删掉 Agent 页 / 首页里剩余的 Arena 引用 → `bus_guard` 里 Arena 两个服务改成「插件启用才守护」。
2. 页面搬家：Arena 五个页面挪成插件页，核心导航只留一个入口。
3. 服务搬家：`arena/` → `plugins/arena/`，插件清单声明进程与端口。
4. 测试：Arena 相关测试随插件走；核心测试在插件关闭时全绿。

## 回滚

每一步独立提交，`git revert`；插件形态下关闭插件即恢复「没有 Arena」的工作台。
