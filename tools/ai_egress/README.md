# tools/ai_egress —— Codex 同轮 turn-state 透传（**未上线**）

任务 `20260929-154823-69f3`，落地合并稿 `20260928-200817-6334` 的收窄方案。

## 状态：未上线，默认不接线

- 本目录是**独立的、可审查的状态机模块**，仓库里**没有任何代码 import 它**（`grep -r ai_egress` 只命中本目录与其测试）。
- `TurnStateConfig.enabled` **默认 `False`**；不开这个 flag，捕获与发送都是空操作。
- 模块**不发真实网络、不读本机凭据 / 配置 / 环境变量、不写任何文件**。接口是抽象的，由未来的数据面自己去接 HTTP / WS。

## 为什么是这个形状（定位结论）

- 当前 main **不存在正式的 source-controlled egress 数据面**：仓库全文搜 `x-codex-turn-state` / `turn_state` / `native_gateway` / `ai_egress` **零命中**，`tools/` 下也没有相关模块。
- 唯一的实现副本在**工作区外的** `tmp/ai_egress_delivery_20260928_arena/native/`（`native_gateway.py` / `native_trial_manager.py` / `quality_job.py` / `test_native_gateway.py`），**未发布、未纳入版本控制的生产路径**。检查其内容：只有 `test_native_gateway.py` 断言了 `x-codex-turn-state` 请求 / 响应头的**透明转发**，`native_gateway.py` 本身**没有任何 turn-state 状态机**（无捕获、无 first-write、无 auth 绑定）。
- 按任务包要求：**不把 tmp 文件搬进生产**，改为在 `tools/ai_egress/` 新增独立模块 + 测试 + 本 README。

## 契约

| 项 | 实现 |
|---|---|
| 状态 | 内存 `Map<(auth_id, turn_id) -> str>`，`threading.Lock` 保护，**first-write-wins** |
| 作用域 | 只在**同一个 logical turn** 内回放（sampling / tool continuation / inline compaction） |
| 新 turn | 无状态开始（`begin_turn` 只做清理，不继承） |
| 清空 | `end_turn`、`switch_auth`（清掉旧 auth 全部条目）、进程重启（只存内存） |
| 缺 id | `auth_id` 或 `turn_id` 缺失 / 空白 → **既不缓存也不发送** |
| HTTP | `HttpTurnStateAdapter`：只透传 `x-codex-turn-state` 请求 / 响应头 |
| WS | `WsTurnStateAdapter`：只在 `response.create.client_metadata` 发送，只从 `response.metadata` 事件读取 |
| 校验 | 空 / 空白 / 非字符串 / 含控制字符 / 超长（`max_len`，默认 8192）→ **忽略且不中断响应** |
| 长度 | `length_bucket()` 把所有非空长度归一个桶。**绝不按长度判风控 / 质量 / 账号状态** |
| 持久化 | 无。不写 Excel、日志、session JSON、导出 |
| 日志 | `log_fields()` 默认只给 `turn_state_present: bool`；显式 `with_digest=True` 才追加 12 位 sha256 前缀与长度桶，**永不返回原值或精确长度** |
| 异常 | `scrub()` 用于把已知原值从报错文本里抹掉 |
| 上游 | 只对 `upstream == "codex"` 发送；其它上游一律空操作 |

## 用法（示意，当前无人调用）

```python
from tools.ai_egress.turn_state import TurnStateConfig, TurnStateStore, HttpTurnStateAdapter

store = TurnStateStore(TurnStateConfig(enabled=True))   # 默认 False，必须显式打开
http = HttpTurnStateAdapter(store)

store.switch_auth(auth_id)                 # auth 切换时调用，自动清旧状态
store.begin_turn(auth_id, turn_id)         # 新 logical turn
http.observe_response(auth_id, turn_id, resp_headers)      # 捕获首个值
extra = http.request_headers(auth_id, turn_id)             # 同轮 continuation / compaction 回放
store.end_turn(auth_id, turn_id)           # turn 结束即清
```

## 明确不做

跨 turn 注入、跨 auth 复用、长度白名单判定（社区那套已被上游维护者撤回）、「良民证」式质量承诺、把值写进 Excel / 日志 / 导出。合并稿的五条停止条件同样适用于未来的任何接线。

## 测试

`py -3 -m unittest tests.test_ai_egress_turn_state -v`（仓库根目录）——20 例，覆盖同轮回放、first-write 不覆盖、新轮清零、auth 切换清零、新进程为空、缺 id / 空 / 畸形 / 超长忽略、并发 first-write、HTTP 与 WS 位置、flag off、非 Codex、长度反向测试（312 与 780 不产生不同分类）、跨轮 / 跨 auth 注入必须失败、运行过程零落盘、源码不含 `open(` / `logging` / `httpx` / `socket` / `openpyxl` 等。
