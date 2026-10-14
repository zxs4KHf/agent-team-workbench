# 不稳定测试登记

规则见 [WORKFLOW.md §5](WORKFLOW.md)：测试失败时，先在旧版本上复跑。旧版本也会失败的，就是既有的不稳定问题，登记在这里并开单修复；**不许靠删断言让它变绿**。

**浏览器测试统一治理（2026-10-02）**：`scripts/run_tests.py` 把用到 Playwright 的测试全部放到最后串行跑；失败自动复跑一次诊断。状态为「待查」的条目复跑通过记为 ⚠ 通过，其余（没登记、或复跑仍失败）一律算失败。每次偶发追加到 `log/flaky_runs.jsonl`（含首次失败的末尾输出，能看到是哪个选择器超时），攒够样本后回来补「根因」并修；修好后把状态改成「已修」，runner 就不再容忍它。

| 测试 | 现象 | 根因 | 状态 |
|---|---|---|---|
| `tests/ui_combo_5556.py` test_1 | 全量跑时偶发"可见三项未重排" | app.js 在下一帧（rAF）才给源行加 `.dragging`，测试只派发一次 dragover，偶尔赶在下一帧之前（fable2 审查 b0a3 定位） | 2026-09-28 已修：dragstart 后等 `.dragging` 出现，断言不改 |
| `tests/ui_events_local_ac1a.py` | 2026-10-01 全量跑时一次 `wait_for_selector` 10 秒超时；单独复跑本分支两次、main 一次均通过 | [推断] 机器负载高（全量测试与临时端口预览服务同时跑）时首屏渲染超过 10 秒；未定位到具体选择器 | 待查：复现时记录是哪个选择器，再决定是等待条件不对还是渲染真慢 |
| `tests/test_notify_page_60e8.py`、`tests/test_notify_r2_60e8.py` | 2026-10-01 全量跑时各一次失败（61 秒 / 37 秒，单跑 19 / 7 秒）；单独复跑本分支与 main 均通过 | [推断] 与 `ui_events_local_ac1a` 同因：全量测试与其他重任务并发、机器负载高时浏览器测试超时 | 待查：给浏览器测试单独的超时余量，或全量跑时串行化浏览器测试 |
| `tests/ui_combo_5556.py`「compose_typing_ime」 | 2026-10-10 全量跑首跑与复跑都在首屏 `wait_for_selector` 10 秒超时；单独跑本分支 2 次、main 1 次均通过 | [推断] 与 30f6 / 44f1 / ac1a 同类（全量跑时首屏渲染超 10 秒），这一类已有 6 个测试 | 待查：同上；CI 只跑 `--fast`，不受影响 |
| `tests/ui_brief_feedback_30f6.py`「overview_board_counts_alert」 | 2026-10-03 全量跑首跑与复跑都在首屏 `wait_for_selector` 10 秒超时；单独连跑 2 次通过 | [推断] 与 44f1 / ac1a 同类：全量跑时首屏渲染超过 10 秒。[已验证] 不是测试泄漏浏览器进程（无 ms-playwright 残留），当时 Leader 与 8 个 Agent 正在工作、本机另有 74 个用户浏览器进程 | 待查：浏览器测试的首屏等待改为按负载放宽，或全量跑时与线上服务隔离 |
| `tests/test_notify_5406.py`（`goto` 报 `net::ERR_NO_BUFFER_SPACE`） | 2026-10-02 合并后全量跑一次失败，复跑通过；单独连跑 3 次通过 | [推断] Windows 套接字缓冲耗尽（WSAENOBUFS）：全量测试里起的临时 HTTP / MCP 服务很多，连接收尾后的 TIME_WAIT 堆积时偶发；与断言无关 | 待查：复现时记录 `netstat -an` 的 TIME_WAIT 数；若确认是测试泄漏连接，给临时服务加 keep-alive 关闭或 SO_REUSEADDR |
| `tests/ui_audit_selftest.py`「反向对照：去掉校正时，键盘聚焦的说明滚动后丢掉」 | 2026-10-02 全量跑时一次失败；单独复跑本分支与 main 均通过 | [已验证] 根因在 `tip.js`：说明层第一次插进 body 被自己的 MutationObserver 当成重绘，又排了一次校正；负载高时这次校正晚到、落在滚动收起之后，把说明弹回来。加 6 个 CPU 满载进程并行跑 3 次复现 1 次；修复后同样负载 10/10 通过 | 已修（fix/review-author-20261002：观察器忽略说明层自己的插入 / 移除） |
| `tests/ui_dispatch_confirm_44f1.py` | 2026-10-02 全量跑时一次失败（64 秒）；单独复跑本分支两次、main 一次均通过 | [推断] 与其它浏览器测试同因：全量跑时负载高。[已验证] 2026-10-02 新 runner 抓到一次：首屏 `wait_for_selector("#question")` 10 秒超时，复跑通过 | 待查：卡在首屏渲染（发起页表单），不是派发确认逻辑 |
| `tests/ui_lead_label_3e32.py` | 2026-10-02 全量跑时两种现象：等 `#leadEvents .lead-event` 10 秒超时；或页面 console 多一条 `ERR_CONNECTION_REFUSED`。单跑 main 2/2 通过、拆分分支 1/2 | [推断] 机器负载高（同时有十几个 Agent 在跑，CPU 68%）时首屏 / SSE 重连慢；[已验证] 拆分前后首屏都在 0.4 秒左右，线上旧版本 4 次里也有 1 次 30 秒没出首屏 | 待查：console 那条是测试服务器关闭后 SSE 重连打到已关端口，可考虑测试里过滤关服后的报错 |
| `tests/test_plugin_proxy_20261002.py`「test_http_forwarding」 | 2026-10-11 全量跑（134/135）失败：`502 != 503`（期望插件上游下线时返回 503 plugin_down，实得 502）；main 上单跑复现同一断言 | [已验证] 与本机代理环境变量有关：宿主机设置了 `http_proxy/https_proxy=http://127.0.0.1:55125` 且**未设 `no_proxy`**，`bus_web.py:459` 用 urllib 访问插件上游（127.0.0.1）时被代理接管，代理对已关闭的上游返回 502，于是走不到 `URLError/OSError` 的 503 分支。对照实验：加 `no_proxy=127.0.0.1,localhost` 后同一测试**通过 1/1（5s）**。全仓源码里没有 502 这条路径（另见 plugins/arena 的 502 与本测试无关） | 待查/环境相关：非仓库缺陷，换到无代理环境应通过；本机复跑请先 `no_proxy=127.0.0.1,localhost` |

## 2026-10-14：Leader 事件排序用例的全局租约耦合（修复候选）

`tests/test_lead_events_2c14.py::TestUnstable::test_open_sorted_newest_first` 创建临时事件目录，但 `lead_events_open()` 仍读取全局 `B.lease()`。当其它测试留下 epoch=2 的租约时，旧日期的 epoch=1 事件被已恢复通知过滤，从预期的 b,d,a 变成 b,d。新导出候选与发布基线的此用例及实现完全一致；注入同样的虚构租约，新旧各3次均复现。此证据证明环境耦合，不声称已经追溯最初那个租约的写入进程。

修复仅在该测试文件的基础夹具里明确注入空租约，不读主工作区租约，原排序/计数/持久化断言不变。已恢复通知规则仍由 `test_lead_events_quiet_20261003.py` 的显式租约夹具覆盖。没有删除断言、增加超时或把失败测试加入跳过列表；需以组合候选完整回归与独立审查决定是否过门禁。
