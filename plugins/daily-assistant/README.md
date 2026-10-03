# 个人助理数据源插件

**做什么**：个人助理每次采集完会写 `~/.config/daily-assistant/data/collected/<日期>-<来源>.json`。
本插件每 10 秒看一眼这些文件的修改时间，有变化就读出新增、未完成、命中关键词的条目，送进工作台「扩展与来源 → 收件箱」。
你点「转成任务」才会交给 Leader；点「忽略」就结束。

**不做什么**：不改个人助理的任何文件，不 import 它的代码，不直接给 Leader 派活。

**已完成的不送**：`done: true`、`completed: true`，以及四象限表格里「状态」为 已完成 / 已关闭 / 已取消 的行（`fields.状态` 也认）。

**配置**：在「扩展与来源 → 插件」里改，保存后插件自动重启生效。本机配置写在 `plugins.local.json`（不入库）。

**状态文件**：`.runtime/plugins/daily-assistant/state.json`。删掉它 = 下次启用时重新打基线。

## 输入契约

每个采集文件必须是 **JSON 对象且带布尔 `ok`**。插件按下表归类，归类结果决定要不要读 `items`（或 `data.items`）：

| 文件内容 | 归类 | 条目会入队吗 | 记 mtime（下轮不再重读）吗 |
|---|---|---|---|
| `ok: true` | `ok` | 会（基线期只记已见） | 记 |
| `ok: false`，且带 unsupported 标记¹ | `unsupported`（该来源本机不适用，不算失败） | 不会 | 不记 |
| `ok: false`，无 unsupported 标记 | `collect_failed (ok_false)` | 不会 | 不记，下轮重读 |
| **没有 `ok` 字段**，或顶层是纯列表 | 默认 `collect_failed (ok_missing)`；仅当配置 `ok_missing: "accept"` 时视为 `ok` | 默认不会 | 默认不记 |
| `ok` 不是布尔（如 `"true"`、`1`） | `collect_failed (ok_not_bool)` | 不会 | 不记 |
| 非 JSON / 半写入 / 非对象 / 读不了 | `collect_failed (bad_json / not_object / unreadable)` | 不会 | 不记，下轮重读 |

¹ unsupported 标记：`unsupported: true`、`skipped: true`，或 `reason` / `error` / `status` / `message` 里含 `unsupported`、`not_supported`、`not supported`、`不支持`、`不适用`。`ok: true` 时不看这些字段。标记清单按目前推断写死，待与个人助理实机文件核对后再增删。

**为什么 `ok` 缺失默认算失败**：这个插件的任务是召回，漏召回可以补，把「采集根本没成功」当成功则会一直静默。个人助理升级后若换了文件格式，默认口径会让插件立刻报 `ok_missing` 并以非零码退出，而不是继续跑着不出活。确认旧格式（纯列表）确实还在用时，显式把 `ok_missing` 改成 `accept`。

## 退出码与失败行为

| 码 | 含义 |
|---|---|
| 0 | 正常。**只有部分来源** `collect_failed` 时也是 0：好来源照常入队，失败来源在日志里列出（只列来源与原因，不列正文），其文件下轮重读 |
| 2 | 配置错误（`keywords` 非法正则、`ok_missing` 不在 fail/accept、`fail_rounds` < 1） |
| 3 | **所有有文件且非 unsupported 的来源都 `collect_failed`**。`--check` 立即返回；常驻模式连续 `fail_rounds`（默认 3）轮都如此才退出，避免采集器正在写文件的瞬间误杀。退出前不写状态文件 |
| 4 | 仅 `--check`：没有任何可采集的输入（文件都不存在，或全部 unsupported）。常驻模式遇到这种情况继续等 |

`unsupported` 与文件缺失既不算成功也不算失败，不参与 3 的判定。

**契约检查（只读，不写状态、不入队、不打印正文）**：
```
python plugins/daily-assistant/main.py --check
```
输出每个 `<日期>-<来源>` 的归类、原因与条目数，最后一行汇总，退出码同上表。

**单次试跑**（打印候选条目，不发送、不保存）：
```
python plugins/daily-assistant/main.py --once
```

## 基线与重判

「基线」= 某个来源**第一次被成功读到**时，当时文件里的全部条目。

- **按来源分别打基线**，不是按插件整体。来源 A 读成功、来源 B 读失败时，只有 A 算打过基线；B 修好后第一次成功读仍按基线处理，所以 B 的历史项不会在修好那一刻涌进来。
- **基线项不入队**，只写进 `seen`（已见）。首次启用全源都失败时什么也不记，下次启动仍按未打基线处理。
- 打过基线之后，同一来源**新增**的条目才会入队；未命中关键词的新增条目也会记为已见。
- 旧版状态文件（只有 `seen` / `mtimes`）视为所有来源都已打过基线，升级后不会再打一次。

**什么时候会被重新判定（再次进收件箱）**：
1. 条目的标题或正文前 200 字变了 → 指纹变了 → 当作新条目。
2. 已见记录只保留 60 天（按首次见到的日期）；过期清理后，条目所在文件下次变化时若它仍在且未完成，会再次入队。这是有意的「长期未完成提醒」。
3. 删掉 `state.json` → 所有来源重新打基线（是重打基线，不是重判：当时已有的条目都不入队）。

**什么时候不会**：改 `keywords` 不会重判已见条目；被「忽略」的收件箱条目由工作台按 `source + fingerprint` 去重，插件重发也不会再出现。

以上语义由 `tests/test_daily_assistant_contract.py` 锁定。

**替代了什么**：旧的 `adapters/daily_assistant.py`（直接往 Leader 指令队列送，首轮可能导入约 85 条，且没过滤四象限的已完成行）。
