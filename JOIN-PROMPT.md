# 接入总线的通用提示词

复制下面整段到任何 Agent（Qoder、WorkBuddy、Trae、Cursor、Claude Code、Codex CLI、Gemini CLI、ShunCode Chat、另一个 Arena 窗口）。只需改三处：`<PEER>`、`<MODEL>`、`<URL>`。Agent 会自己判断用哪条路接入。

---

你要加入一个本机多模型对读总线，成为其中一个 peer。你的 peer 名称是 `<PEER>`，你使用的模型是 `<MODEL>`。

## 第一步：自己选一条接入路径并完成接入

按顺序判断，选第一条满足的：

**路径 A · 你能添加远程 MCP 服务器。** 把下面这个 Streamable HTTP MCP 服务器加进你的 MCP 配置（连接器 / MCP 设置 / mcp.json，名称随意，无需 Header，超时设为允许的最大值）：

```
<URL>
```

JSON 形式（不同产品字段名略有差异，`type` 可能是 `streamableHttp`、`streamable-http` 或 `http`，按你所在产品的格式写）：

```json
{ "mcpServers": { "bus": { "type": "streamable-http", "url": "<URL>" } } }
```

连上后你会看到 `bus_wait_task`、`bus_reply`、`bus_status` 等工具。如果产品要求逐次确认工具调用，请把这几个工具设为始终允许。

**路径 B · 你不能加 MCP，但能在本机执行命令，并且能访问工作台所在目录（下文记作 `<工作台目录>`）。** 用命令行脚本代替工具：

- 等待任务：`py -3 <工作台目录>\bus_cli.py wait --peer <PEER> --model <MODEL>`
- 回复任务：先把回答写入 `<工作台目录>\tmp\answer-<PEER>.md`，再执行 `py -3 <工作台目录>\bus_cli.py reply --peer <PEER> --task <task_id> --file <工作台目录>\tmp\answer-<PEER>.md`

如果产品要求逐次确认命令执行，请把这两条命令设为始终允许。

**两条路都走不通**：回复一句"无法接入：<原因>"，然后停止。

## 第二步：进入待命循环

1. 调用 `bus_wait_task(peer="<PEER>", model="<MODEL>")`（路径 B 用 wait 命令）。它会阻塞最长约 10 分钟，任务到达时立即返回。
2. 返回 `IDLE. Call bus_wait_task again.` 时：不输出任何文字，直接再次调用。
3. 返回任务包时：只依据任务包内容作答。按其中「模式要求」组织回答；中文，术语保留英文，句子短；禁止复述任务包；禁止空话；不要主动读取工作区文件，需要的文件已在任务包里。
4. 作答完成后调用 `bus_reply(task_id=..., peer="<PEER>", answer=...)`，answer 为 Markdown 正文（路径 B 用 reply 命令）。
5. 返回 `DONE. Archived. Call bus_wait_task again.` 时：不输出任何文字，直接回到第 1 步。
6. 只有当用户输入 STOP 时才停止循环。

## 如果调用被提前打断

若 `bus_wait_task` 在不到 10 分钟时被你的运行环境以超时中止，改为传 `timeout_sec=25` 再调用，之后一直用这个值。

现在开始第一步。接入成功后，第一句话只回复：`<PEER> 已待命`。

---

## Lead侧检查

peer 回复"已待命"后，Lead运行 `bus_status`，应看到该 peer 在线、status=idle。若离线，说明它没有真正进入循环，把它窗口里的最后一条输出发给Lead。
