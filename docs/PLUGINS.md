# 写一个插件

工作台的扩展方式只有一种：在 `plugins/` 下放一个文件夹。工作台发现它、按你的开关启动它、崩了按限额重启它。
插件和工作台之间**只走 HTTP 接口**，互不 import 代码——所以插件写错了、崩了，工作台照常运行；关掉开关，进程立即停止。

> 为什么这样设计：见 [adr/0002-plugins.md](adr/0002-plugins.md)。参考了 VS Code（扩展独立进程）、Home Assistant（每个集成一个清单、单独重载）、Obsidian（开关即时生效）。

## 最小例子

```
plugins/
  hello/
    plugin.json
    main.py
```

`plugin.json`：

```json
{
  "id": "hello",
  "name": "示例插件",
  "version": "0.1.0",
  "api": "workbench-plugin/1",
  "kind": "source",
  "description": "每分钟往收件箱送一条测试条目",
  "entry": ["python", "main.py"],
  "config": { "interval": 60 },
  "config_help": { "interval": "间隔秒数" }
}
```

`main.py`：

```python
import json, os, time, urllib.request
url = os.environ["WORKBENCH_URL"]
cfg = json.loads(os.environ["WORKBENCH_PLUGIN_CONFIG"])
while True:
    body = {"source": os.environ["WORKBENCH_PLUGIN_ID"],
            "items": [{"fingerprint": str(int(time.time())), "title": "来自 hello 的测试条目"}]}
    req = urllib.request.Request(url + "/api/intake", json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).close()
    time.sleep(cfg["interval"])
```

然后打开 **扩展与来源 → 插件**，打开开关即可。

## 清单字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 与文件夹同名；小写字母、数字、连字符 |
| `api` | 是 | 固定 `workbench-plugin/1`。以后接口有不兼容变化会升到 `/2`，旧插件会被明确拒绝而不是悄悄出错 |
| `kind` | 是 | `source` 数据源 / `notifier` 通知 / `panel` 只提供页面 |
| `name` `version` `description` | 否 | 显示用 |
| `entry` | 否 | 启动命令数组，工作目录是插件文件夹；首项 `python` 会换成工作台自己的解释器 |
| `page` | 否 | 插件自带页面，访问地址 `/plugins/<id>/<page>`，与工作台同源，可直接调用下面的接口。只开放 page 所在目录（写 `web/index.html` 就只开放 `web/`），插件的服务端代码不会被当静态文件读走。启用后工作台左侧导航「插件」分区出现入口 |
| `proxy` | 否 | `{"port": "<config 里的端口键>"}`：插件自带 HTTP 服务时用。插件启用时工作台把 `/api/<id>/*` 转发到 `127.0.0.1:<端口>/api/<id>/*`（只在核心接口都没命中时才转发，插件遮不住核心接口）；插件关掉这些接口就 404。例：`plugins/arena` |
| `wake` | 否 | `/api/<id>/` 下的子路径（需同时声明 `proxy`）：插件能把唤醒语直接发进 Agent 会话时声明。工作台「唤醒」按钮先 `GET` 它取能自动发送的 Agent（返回 `{"bound": [peer…]}`），再 `POST {peer, text}`；返回 `bound: true` 表示由插件负责发送，否则工作台退回「复制唤醒语」。例：`plugins/arena`（经中继管理的会话） |
| `config` | 否 | 默认配置。界面上修改后存在本机 `plugins.local.json`，插件自动重启生效 |
| `config_help` | 否 | 每个配置项的中文说明，显示在配置表单里 |

## 工作台注入的环境变量

| 变量 | 内容 |
|---|---|
| `WORKBENCH_URL` | 工作台地址，如 `http://127.0.0.1:8765` |
| `WORKBENCH_PLUGIN_ID` | 插件 id |
| `WORKBENCH_PLUGIN_CONFIG` | 合并后的配置（JSON 字符串） |
| `WORKBENCH_PLUGIN_DATA` | 插件私有数据目录 `.runtime/plugins/<id>/`（不入库），状态文件放这里 |
| `WORKBENCH_PARENT_PID` | 工作台进程号 |

插件的标准输出和错误输出写到 `log/plugins/<id>.log`，界面上「查看日志」可看。

## 插件能用的接口

| 接口 | 用途 |
|---|---|
| `POST /api/intake` `{source, items:[{fingerprint, title, text?, url?, due?, tags?, origin?}]}` | 数据源送候选条目。同一 `source + fingerprint` 只收一次；每批最多 200 条 |
| `GET /api/event-log?cursor=&limit=` | 通知类插件增量读事件。返回 `{events, cursor}`，下次带上 cursor |
| `GET /api/state` | 全部项目与决策（只读） |
| `GET /api/ping` | 健康检查。插件应在工作台长时间不可达时自行退出 |

读事件时 `cursor=now` 表示"只要从现在起的新事件"，通知类插件首次启动用它，避免补发最近 7 天的历史。

## 来源插件的辅助任务

本人在来源插件中安排的资料整理、分析任务，可以使用通用插件任务接口。来源插件维护原始资料、批注和本人验收，协作台负责项目团队队列、成员领取与回复。

接口沿用可信本机插件的访问边界。`source` 由调用方提供，用于归属和请求去重；身份认证与插件间权限隔离遵循下方安全边界。

| 接口 | 用途 |
|---|---|
| `GET /api/plugin-jobs/options?source=…` | 读取可选项目、项目团队与自定义团队、资料协作模式、默认设置和成员状态 |
| `GET /api/plugin-jobs/agents` | 读取 Agent 名称、模型标识、在线与待命状态；字段不包含凭据 |
| `POST /api/plugin-jobs` | 传入已启用插件的 `source`、稳定 `request_id`、`project`、`team`、`question`、可选 `mode`、`peers`、`title` 和 `ttl_min`；默认模式为 `race` |
| `GET /api/plugin-jobs?source=…&request_id=…` | 核对同一次派发；返回 `submitted`、`unknown`、`cancelled` 或 `missing` |
| `POST /api/plugin-jobs/cancel` | 传入 `source` 与 `request_id`，取消尚无人领取的队列任务；已领取时返回冲突 |
| `GET /api/task?id=…` | 使用派发得到的任务编号读取进度和原始回复 |

同一请求内容重复提交返回原任务，内容改变返回冲突。`submitted` 表示已投递，成果由来源插件读取后送本人审阅。中断后的请求会按持久化标记寻找原任务，无法确认时保持 `unknown`，不会再创建执行。

成员范围来自协作台项目团队；`peers` 可以指定团队内的子集，省略时取整个团队。成员名单在提交时固定，重复提交保持原任务的范围。抢单由一位成员执行；其他资料模式收集所有选定成员的回复。静态团队可包含离线成员，执行期限从领取时计算。项目任务记录保留来源、团队和模式，返回值提供直接进入任务队列的链接。

普通任务的自动改派、完成合并提醒和自动收尾跳过外部任务。来源插件接收原始回复并负责本人审阅。单一 `peer` 的既有调用协议继续处理已经安排的任务。`plugins/level-design` 提供完整面板示例。

## 安全边界

- **插件进程拿到的环境变量是白名单**（PATH、TEMP、用户目录、代理等），工作台自己的其它环境变量不会传过去。
- **`panel` 类插件的页面和工作台同源**，技术上能调用全部接口（包括 `/api/command`）。"只召回不决定"靠约定遵守，所以只装自己信得过的插件。
- **工作台不会按 pid 文件杀进程。** 工作台被强杀后遗留的插件，要靠插件自己检测"工作台不可达"并退出（见下方约定）。

## 约定

- **只召回，不决定。** 数据源把条目送进收件箱，由本人确认后才变成 Leader 指令；不要直接调 `/api/command`。
- **首次启用只打基线。** 避免一启用就灌进几十条历史项（2026-09 旧 adapter 的教训）。
- **幂等。** 同一条目重复送没关系，指纹一致就会被跳过；指纹要稳定（不要用列表顺序号）。
- **不写别的项目的文件。** 需要什么数据就读别人已经导出的结果，或者请对方也发事件。
- **私人路径放配置里。** 仓库里的 `plugin.json` 只放通用默认值。

## 现有插件

| id | 类型 | 说明 |
|---|---|---|
| `daily-assistant` | 数据源 | 读个人助理采集的小米待办/笔记、飞书、四象限，把新增未完成条目送进收件箱。见 [plugins/daily-assistant/README.md](../plugins/daily-assistant/README.md) |
| `desktop-notify` | 通知 | 待拍板、施工落地、收件箱新增、插件崩溃时弹 Windows 通知；合并批量、每小时上限、免打扰时段；默认关闭。见 [plugins/desktop-notify/README.md](../plugins/desktop-notify/README.md) |
| `level-design` | 面板 | 关卡工作台、指定 Agent 辅助任务与成果回传。见 [plugins/level-design/README.md](../plugins/level-design/README.md) |
