# ArenaX 依赖图

> **2026-10-02 更新（ADR 0014）**：Arena 已做成可选插件 `plugins/arena/`。下文里的 `arena/*.py` 现在在 `plugins/arena/server/`，`web/v2/js/arena.js` 在 `plugins/arena/web/`；`bus_web` 不再写死 `/api/arena/*` 转发（改为插件清单的 `proxy`，插件关掉就 404），`bus_guard` 不再拉起 9091 和 ArenaX（改由插件 `main.py` 按配置 `arenax_root` 拉起）。

> 2026-09-28 · Leader controller 整理。目的：说清工作台哪些地方依赖外部的 ArenaX，以及换掉或拿掉它时要改哪里。
> 结论先行：**工作台核心（总线、看板、施工、审查）不依赖 ArenaX。** ArenaX 只服务"Arena 控制面"这一块功能（账号池、会话池、模型路由、榜单）。拿掉 ArenaX 后，这些页面显示"上游不可达"，其余一切照常。

## 1. 分层

```
浏览器（工作台页面 web/v2/js/arena.js）
   │  /api/arena/*
   ▼
bus_web.py  :8765   ── proxy_arena()：原样转发，地址 = 环境变量 ARENA_URL（默认 http://127.0.0.1:9091）
   │
   ▼
arena/arena_server.py :9091   ← 我们自己的代码，入库
   ├─ /api/arena/*  控制面（给页面）
   └─ /v1/*         中继入口（给外部 Agent Harness，按组 Key 鉴权）
   │
   ▼
arena/arena_upstream.py       ← 唯一的"接缝"：对上游的全部调用都在这个文件里（22 个函数：call/open_stream 两个通道 + 20 个具体接口）
   │  地址 = console 设置 settings.upstream（默认 http://127.0.0.1:9090）
   ▼
ArenaX :9090                  ← 外部闭源程序，不在本仓库，不由我们维护
   （本机路径在 Arena 插件配置 arenax_root 里，不入库；出网代理见插件配置 arenax_proxy）
```

## 2. 耦合点清单

| # | 位置 | 依赖什么 | 强度 | 没有 ArenaX 时 | 可配置项 |
|---|---|---|---|---|---|
| 1 | `bus_guard.py` `start_arenax` / `SERVICES["arenax"]` | 本机 ArenaX 目录、9090 端口、7892 代理 | ~~硬~~ → **软**（本分支） | 之前：守护会反复重启它。现在：检测不到 `run_server.py` 就不登记此服务 | `ARENAX_ROOT`、`ARENAX_URL`、`ARENAX_PROXY`（空=不走代理）、`ARENAX_DISABLE=1` |
| 2 | `arena/arena_upstream.py` | ArenaX 的 HTTP 接口（见第 3 节）：控制类走 `call()`，中继类走 `open_stream()` 原样转发 | 接缝 | 所有调用抛 `UpstreamError(503)`，页面显示"上游不可达" | `settings.upstream` |
| 3 | `arena/arena_store.py` 默认设置 | `upstream = http://127.0.0.1:9090` | 默认值 | — | 页面"设置"可改 |
| 4 | `arena/arena_core.py` | 通过 `arena_upstream` 间接依赖；组请求给上游加临时别名 `<gid>@<sid前8位>`，依赖 ArenaX 的会话池命中规则 | 间接 | 同 #2 | — |
| 5 | `arena/arena_server.py` | 只依赖 `arena_upstream`；`RELAY_PATHS` 里的 11 条中继路径经 `open_stream` 原样转发给上游（见第 3 节） | 间接 | 9091 自己能起，`/health` 正常；中继请求返回 503 | `--port`、`--host` |
| 6 | `bus_web.py` `proxy_arena` | 9091（不是 9090） | 软 | 9091 不在时 `/api/arena/*` 返回 502，其它 API 不受影响 | `ARENA_URL` |
| 7 | `bus_core.py` 读取 `/api/arena/groups` | 9091，超时 1.5 秒，失败静默 | 软 | 成员页不显示 Arena 分组 | `ARENA_URL` |
| 8 | `web/v2/js/arena.js` | `/api/arena/*` | 软 | Arena 视图报错提示，其它视图正常 | — |
| 9 | `arena/test_arena.py` | 真实 9090 | 集成测试 | 报"upstream 9090 不可达"——**不在** `scripts/run_tests.py` 默认集合里 | — |
| 10 | `tests/join_batch1_checks.py`、`tests/test_notify_page_60e8.py` | 桩替身 / 路由拦截 | 无 | 不受影响 | — |
| 11 | 文档：CHANGELOG、ARCHITECTURE | 描述性 | 无 | — | — |

## 3. 上游接口面（`arena_upstream.py` 用到的 ArenaX 接口）

| 类别 | 接口 | 说明 |
|---|---|---|
| 健康 | `GET /health` | `health()`；守护进程另用 `GET /v1/models` 探活（返回 401/403 也算活着） |
| 标准·探测 | `POST /v1/chat/completions` | `chat_probe()`，OpenAI 兼容 |
| 标准·中继（原样转发） | `/v1/chat/completions`、`/v1/completions`、`/v1/embeddings`（OpenAI）；`/v1/messages`、`/v1/messages/count_tokens`（Anthropic）；`/v1/responses`（OpenAI Responses）；以及不带 `/v1` 前缀的同名路径 | `arena_server.RELAY_PATHS` → `open_stream()`。**上游缺哪条，对应风格的外部 Agent 就会断** |
| 账号 | `/v1/accounts/list`、`check_status`、`check_all`、`batch_add`、`delete`、`reset_cooldown`、`switch`、`sync_routes` | ArenaX 私有 |
| 账号（不再支持） | `/v1/accounts/auto_register`、`batch_auto_register` | 自动注册账号，属于绕过平台限制的能力。**我们不实现、不扩展，建议下个版本从界面移除入口** |
| 会话 | `GET /v1/agent/sessions?limit=`（`list_sessions`）、`sessions/delete`、`sessions/update-model`、`/v1/sessions/batch_create`、`detect_one` | ArenaX 私有 |
| 路由 | `/v1/relay/config`、`/v1/relay/stats`（`relay_stats`）、`route/set`、`route/delete` | ArenaX 私有 |

**换上游时只需要改 `arena_upstream.py`。** 标准部分是"探测"和"中继"两行，其余都是 ArenaX 私有接口。

## 4. 如果将来要换掉 ArenaX

推荐路线：用**官方 API 的网关**替代（例如直接调各家官方 API，或者用 LiteLLM 这类开源网关统一成 OpenAI 格式）。
1. 在 `arena_upstream.py` 旁边新写一个 `arena_upstream_official.py`，实现同名函数：
   - `chat_probe` 直接调网关；
   - driver 必须**声明自己支持哪些中继路径**（例如只支持 OpenAI 风格时，`/v1/messages` 应返回明确的 501 和说明，而不是 503"上游不可达"）；
   - 账号、会话类函数改成"API Key 管理"语义，或者返回 `{"unsupported": true}`。
2. 用设置项选择 driver（`settings.driver = "arenax" | "official"`），`arena_core` 不需要动。
3. 把守护进程的 `arenax` 服务关掉（`ARENAX_DISABLE=1`），换成网关自己的进程（如果需要）。
4. 界面：账号池、会话池页面按 driver 能力隐藏不支持的按钮。

这一步**本次不做**，等你决定用哪个上游再开分支。

## 5. 本机运行时（不在仓库里，供排查）

- ArenaX 进程：`.venv` 启动器 → uv 解释器（父子两个进程，是**同一个实例**，不是两份）。
- 端口：9090（ArenaX）、9091（控制面）、8765（工作台）、8791（MCP）。
- 代理：127.0.0.1:7892（ArenaX 出网）。
- 日志：`console/arenax_9090.log`、`console/arena_9091.log`（由守护进程 `spawn()` 写，没有 `logs/` 子目录）。
- 代理：`ARENAX_PROXY` 默认仍是本机 `http://127.0.0.1:7892`，这样本机不设环境变量时行为不变；别的机器不需要代理时设为空字符串。
