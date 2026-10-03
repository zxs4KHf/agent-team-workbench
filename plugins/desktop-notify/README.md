# 桌面通知（desktop-notify）

有决策等你拍板、施工落地、收件箱新增、插件反复崩溃时，在 Windows 右下角弹一条通知。

- **默认关闭。** 在「扩展与来源 → 插件」里打开开关即可；关掉立即停止。
- **只读。** 只读 `/api/event-log`，不调用任何写接口。
- **不刷屏。** `batch_sec` 秒内的多条事件合成一条；每小时最多 `max_per_hour` 条；`quiet_hours`（如 `23-8`）时段只记日志。
- **首次启用不补发历史**（从「现在」开始读事件）。
- 弹窗用 Windows 自带的 PowerShell 通知接口，不需要装任何依赖；非 Windows 系统只写日志。
- 日志：`log/plugins/desktop-notify.log`（界面上「查看日志」）。

要换成企业微信 / 飞书机器人：复制本插件，把 `toast()` 换成对机器人 webhook 的 POST，webhook 地址放配置里（不要写进仓库）。
