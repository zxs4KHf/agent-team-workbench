# 0003 跨项目互通只用一份只追加的事件记录

**情况**：同一台机器上可能有多个工作台（本项目 8765，以及其它项目自己的工作台），希望"数据实时同步、不硬耦合"。之前各项目要么互相读对方内部文件，要么在启动脚本里写死别的项目的路径。

**决定**：
- 工作台每次重要状态变化都追加一行到 `events/YYYY-MM-DD.jsonl`。
- 字段取 CloudEvents 子集：`specversion id source type time subject data`。
- 读方自己记游标（`日期:字节偏移`），通过 `GET /api/event-log?cursor=` 或直接读文件；工作台不需要知道谁在读。
- 事件类型：`decision.<状态>`、`command.queued`、`intake.*`、`feedback.*`、`plugin.*`。
- `emit` 永不抛异常。

**为什么**：
- "发件箱"模式（outbox）是跨系统同步最稳的做法：写方只管追加，读方按自己的节奏消费，任一方停机都不丢数据。
- 文件格式人能直接看，出问题用记事本就能排查。
- CloudEvents 是 CNCF 毕业标准，以后要接 Webhook / 消息队列不用改字段。

**放弃了什么**：不是推送式的，读方要轮询（本机轮询成本可以忽略）。没有做签名和投递保证；真要推到外网时按 Standard Webhooks 加签名。

**回滚**：`git revert --no-commit 0034936..d7b6d12`（事件、插件、收件箱、反馈四块互相 import，ADR 0002/0003/0004 只能一起撤）。只想停写事件：把 `bus_events.emit` 改成空函数即可，其它模块的 import 不受影响。不要直接删 `bus_events.py`，那样 bus_intake / bus_feedback / bus_plugins / bus_web 都会起不来。
