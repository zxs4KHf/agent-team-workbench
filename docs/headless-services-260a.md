# 无头运行切换与回滚手册（任务 20261011-214338-8b25 · 方案稿 260a）

> 目标：常驻服务不在前台弹控制台窗口、不抢焦点。本文是方案稿「最终方案」第 1/2 步的落地交付：
> 盘点结论 + 无头策略 + 切换步骤 + 一键回滚。

## 1. 盘点结论（Leader 2026-10-11 完成，本单直接采信）

- [已验证] 在跑的 ATW 服务已经是无头的：守护 `bus_guard.py` 用
  `CREATE_NO_WINDOW | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB` 拉起 workbench 8765；
  `start_guard.py`、`start_named_mcp.py`、`plugins/arena`、`console/cfp.ps1` 均为
  `CREATE_NO_WINDOW` 或 `-WindowStyle Hidden`。
- [已验证] 仅有的两处「会新建可见控制台」是 gitignore 内的手工脚本
  `console/start_web_8765.ps1` 与 `console/start_arena_9091.ps1`（原为 `-WindowStyle Minimized`）。
  Leader 已就地改为 `pythonw.exe + -WindowStyle Hidden`，回滚记录在
  `console/rollback-260a-headless.md`（console/ 不入库）。
- 仓库内因此缺一个**可测试、可复用**的无头启动入口——本单补上 `tools/start_headless.py`。

## 2. 无头策略

| 服务 | 端口 | 脚本（仓库内真实路径） | 日志 |
|---|---|---|---|
| 工作台 | 8765 | `bus_web.py` | `console/workbench_8765.log` |
| Arena 控制面 | 9091 | `plugins/arena/server/arena_server.py`（ADR 0014 后迁移，旧文档写的 `arena/arena_server.py` 已失效） | `console/arena_9091.log` |

要点：

1. **CREATE_NO_WINDOW**：启动器给子进程统一加
   `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`
   （与 `bus_guard.py:25` 同一套成熟组合）。不新建控制台窗口、不抢焦点；
   `BREAKAWAY_FROM_JOB` 让子进程脱离调用方的作业对象——实测缺失该位时，拉起会话的作业对象
   一结束子进程会被整体回收（试运行中 arena 子进程随工具会话结束而消失），所以必须带上。
2. **输出重定向**：子进程 stdin 接 DEVNULL（与 `bus_guard.spawn` 对齐，无头服务不继承
   交互式 stdin，避免读输入时永久挂起），stdout/stderr 并流写入 `console/<name>.log`；
   完全静默的可观测性代价由日志轮转 + 文件持续更新弥补（方案稿分歧点的要求）。
3. **日志轮转**：单文件超过 5 MB 自动改名为 `<name>-<YYYYmmdd-HHMMSS>.log`，原路径
   **新建空文件**（服务日志路径不缺位）；文件被占用时本轮跳过，不影响服务。
   历史档默认保留最近 3 代（`--keep N` 可调，0=不留历史），更老的自动清理——没有
   retention 等于把「不弹窗」换成「慢慢写满磁盘」。
4. **不重复实例**：启动前探测 127.0.0.1:port，已被占用视为「已在运行」并明确说明，不重复拉起。
   （探测与真正 Popen 之间存在极小竞态窗口；真要双开时后者会因端口绑定失败退出并写进日志。）
5. **退出码有语义**：全部服务「已启动」或「已在运行而跳过」→ 0；有服务既没启动也不是运行中跳过 → 1。
   脚本可以直接用退出码判断成败，不必解析中文 detail。
6. 守护/重启仍由既有 `bus_guard.py` 体系负责，本启动器只做「一次性无头拉起」，不做监督。

## 3. 切换步骤

```bat
:: 1) 停掉旧的前台窗口实例（若有）：直接关窗口，或按端口找 pid
::    netstat -ano | findstr ":8765"  →  taskkill /PID <pid> /F

:: 2) 无头拉起（本目录即仓库根）
py -3 tools\start_headless.py

:: 3) 验证：两个 pid 各打印一行；端口在听；日志在增长
netstat -ano | findstr ":8765 :9091"
type console\workbench_8765.log
```

前台不应出现任何新的 Console 窗口。`console/` 在 `.gitignore` 内，日志不会入库。

## 4. 验证

- **单元测试**（不动真实服务、不碰真实 console/）：

  ```bat
  py -3 tests\test_start_headless_8b25.py
  ```

  覆盖：CREATE_NO_WINDOW 断言（注入 fake Popen）、端口占用时跳过不重复启动、
  超阈值轮转改名 + 历史保留、未达阈值不动、`--rotate-only` 不启动服务、os.replace 失败容错。

- **轮转逻辑单独验证**（不启动任何服务）：

  ```bat
  py -3 tools\start_headless.py --rotate-only --max-bytes 16 --log-dir %TEMP%\headless_rot_test
  ```

  预先在该目录放一个超过 16 字节的 `workbench_8765.log`，执行后应出现 `workbench_8765-<时间戳>.log`。

## 5. 回滚

1. 停无头实例：`netstat -ano | findstr ":8765 :9091"` 找 pid，`taskkill /PID <pid> /F`。
2. 恢复旧启动方式：
   - 工作台：原来怎么起就怎么起（前台 `py -3 bus_web.py` 或 `console/start_web_8765.ps1`）；
   - Arena：`plugins/arena` 按其 `main.py` / plugin.json 的配置拉起（ADR 0014）。
3. `console/` 内两处 ps1 的改动（pythonw + Hidden）如需回退，对照
   `console/rollback-260a-headless.md`（gitignore 内，Leader 维护）逐条还原。
4. 确认端口未被重复占用：回滚后两个端口至多一个监听者。

## 6. 本单改动清单

| 文件 | 改动 |
|---|---|
| `tools/start_headless.py` | 新增：无头启动器（CREATE_NO_WINDOW、日志重定向与轮转、端口去重、--rotate-only） |
| `tests/test_start_headless_8b25.py` | 新增：7 条单测（见 §4） |
| `docs/headless-services-260a.md` | 新增：本文 |
