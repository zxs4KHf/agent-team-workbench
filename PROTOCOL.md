# 多模型对读总线 bus

多个 Agent 会话（Arena 网页会话经 ShunCode Bridge、本地 CLI Agent、API 模型）通过同一块磁盘交换任务与回答。Lead会话发任务、收回答、合并；peer 会话待命、作答。协议在文件里，任何 harness 只要能读写工作区就能接入。

## 目录

```
workbench/bus/
  bus_core.py        协议实现（dispatch / wait / reply / collect / status / archive）
  bus_tool.py        Bridge 自定义工具入口（stdin 读 JSON 参数）
  tool.js            Bridge 侧启动器：node → py -3 bus_tool.py
  bus_daemon.py      API peer 常驻进程（DeepSeek / GLM 等）
  bus_cli.py         本地 CLI Agent 适配器
  bus_mcp.py         自建 Streamable HTTP MCP 服务（0.0.0.0:8791/mcp，Bearer 鉴权）：六个 bus_* 工具经子进程复用 bus_tool.py，另含受限 ws_read / ws_write / ws_run；start_mcp.bat 一键启动
  PEER-PROMPT.md     给网页 peer 窗口的待命提示词
  peers/<peer>.json  心跳：status idle|busy|offline、model、last_seen、task_id
  inbox/<peer>/<task_id>.json   待领取任务（含嵌入的上下文文件内容）
  outbox/<task_id>.meta.json    任务元数据
  outbox/<task_id>.<peer>.md    回答
  log/<日期>/                   归档：任务包、回答、合并稿 <task_id>.merge.md
```

`.shuncode/mcp-tools/bus_*.json` 五个 manifest 由 Bridge 自动加载为 MCP 工具，对所有连接的会话可见。

## 时序

| 步骤 | 谁 | 动作 | 延迟 |
|---|---|---|---|
| 1 | peer | `bus_wait_task` 阻塞，每 2 秒查 inbox，最长 590 秒 | — |
| 2 | Lead | `bus_status` 确认 peer 在线（心跳 ≤180 秒） | 即时 |
| 3 | Lead | `bus_dispatch(peers, mode, question, context_files)` 写 inbox | 即时 |
| 4 | peer | `bus_wait_task` 返回任务包 | ≤2 秒 |
| 5 | peer | 作答，`bus_reply` 写 outbox，任务包移入 log | 模型耗时 |
| 6 | Lead | `bus_collect(task_id)` 每 2 秒查 outbox，到齐即返回 | ≤2 秒 |
| 7 | Lead | 合并，写 `log/<日期>/<task_id>.merge.md`，`bus_status(archive=task_id)` | — |

## 合并契约（Lead执行）

固定四段，使用用户提问的语言：

1. 共识要点：写明「N / 已答 M 家一致」，禁止「所有回答一致」这类无法核实的措辞；各家模型错误强相关，共识最不可信，Lead至少抽样核实其中一条并注明依据。
2. 分歧点：逐条列出，Lead读工作区文件核实后给出结论。
3. 最终方案：单一可执行方案；有共识采纳共识，否则选或综合证据最强的一项并说明理由；末尾列剩余风险。
4. 验证依据：读过的文件与核实的事实。

任务包 meta 可带 `rubric`（一句「什么算好答案」）；带了就按它评，不随轮次漂移。

## 三种模式

| 模式 | 用途 | 任务包对 peer 的要求 |
|---|---|---|
| independent | 方向性决策 | 结论 / 理由 / 最强反驳 |
| redteam | 压力测试已有方案 | 三个失败理由 + 一条修改建议 |
| relay | 长文档深化 | 在骨架上补细节并标注改动 |


### 暴露面纪律

Bridge 不做鉴权（用户定夺，2026-09-20）。控制手段是操作纪律：Bridge URL 只发给指定 Agent 窗口，不贴到公共位置；本轮对读结束后停止隧道映射或更换域名（收工前停隧道）。

## 接入方式

| harness | 方式 |
|---|---|
| Arena 等网页 Agent | 贴 Bridge URL + `PEER-PROMPT.md` |
| Claude Code / Codex CLI | `py -3 workbench/bus/bus_cli.py wait --peer codex --model <名>`，回复 `bus_cli.py reply --peer codex --task <id> --file answer.md` |
| API 模型 | `py -3 workbench/bus/bus_daemon.py --peers ark-deepseek,ark-glm`（默认只用 Coding Plan；按量接口需加 `--allow-paid`），profile 来自 `workbench/config.toml` |

## 成本

无任务时 peer 的 `bus_wait_task` 在本地进程内阻塞，模型零输出；每 590 秒空转一次约 20 token 输出。任务内容不留在 peer 窗口。网页 peer 按需开启，API daemon 常驻。

## 限制

- Bridge 单次工具调用上限 600 秒，因此 wait / collect 最长 590 秒；更长等待由模型再次调用。
- 隧道中断时所有网页会话一起断开，重连后需重新发送待命提示词。
- 任务默认 30 分钟有效期，过期未领取自动作废（改名 `.expired`）。
