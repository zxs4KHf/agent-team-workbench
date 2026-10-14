# 桌面通知（desktop-notify）

有决策等你拍板、施工落地、收件箱新增、插件反复崩溃时，在 Windows 右下角弹一条通知。

- **默认关闭。** 在「扩展与来源 → 插件」里打开开关即可；关掉立即停止。
- **只读。** 只读 `/api/event-log`，不调用任何写接口。
- **不刷屏。** `batch_sec` 秒内的多条事件合成一条；每小时最多 `max_per_hour` 条；`quiet_hours`（如 `23-8`）时段只记日志。
- **首次启用不补发历史**（从「现在」开始读事件）。
- 弹窗用 Windows 自带的 PowerShell 通知接口，不需要装任何依赖；非 Windows 系统只写日志。
- 日志：`log/plugins/desktop-notify.log`（界面上「查看日志」）。

要换成企业微信 / 飞书机器人：复制本插件，把 `toast()` 换成对机器人 webhook 的 POST，webhook 地址放配置里（不要写进仓库）。


## 持久通知（ADR 0019）

实例目录由 WORKBENCH_PLUGIN_DATA 提供。ledger.json 的校验和仅用于发现本地损坏，不证明外部来源可信；initialized 标记防止账本缺失后静默从 now 开始。首次启用不补发旧历史，重启恢复游标和待发批次。已完成事件保留有界去重历史（默认2000条），不是无限期全局exactly-once。pending/inflight/unknown共用容量上限，满时停止前移游标。

免打扰、限流只延后。handed_off是Windows通知API交接成功，不等于用户已读；无法确定的发送为unknown，不自动重试。账本损坏/写失败停插件，不自动清空。原子替换和文件fsync降低进程崩溃风险，不承诺所有文件系统或断电情形都零丢失。

处理unknown前先停此插件，以下命令只操作指定本机账本，不发送网络请求。inspect包含通知文本，输出不要公开：

```text
python plugins/desktop-notify/main.py inspect --state-dir <插件状态目录>
python plugins/desktop-notify/main.py retry <事件ID> --state-dir <插件状态目录> --expected-revision <当前版本> --actor <操作者> --reason <接受重复风险的理由>
python plugins/desktop-notify/main.py resolve <事件ID> --state-dir <插件状态目录> --expected-revision <当前版本> --actor <操作者> --reason <处理理由>
```

retry只将该条重新入队，重新启用插件后才尝试发送；resolve表示不再通知，不代表业务完成。回滚旧插件前先停插件并保留整个私有账本，旧版本不提供持久恢复。
