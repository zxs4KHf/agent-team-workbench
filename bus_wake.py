"""唤醒掉线的 Agent（2026-10-02 用户：「MCP 对话接入的 Agent 有时会掉线，掉线后能不能按一下按钮就把它拉起来」）。

掉线多半是宿主那边的原因（单轮工具调用次数到顶、上下文满、会话被结束、MCP 连接被宿主断开），
工作台拦不住，能做的是让「拉起来」只要一步：
- 给每个 Agent 生成一段短的唤醒语：说明它是谁、立刻调用 bus_wait_task 回到待命循环；万一宿主已经把 MCP 服务器丢了，附上重新接入的地址。
- 经 Arena 中继管理的会话（Arena 插件里已绑定到这个 Agent 名字的），由插件把唤醒语直接发进会话；
  其它宿主（WorkBuddy、Qoder、Claude 桌面、网页版等）工作台够不着对话框，前端复制唤醒语，用户粘贴发送。
本模块只拼文字、列名单，不做网络请求；链接与令牌由 bus_web 在本机回环请求里给。"""
import json
import urllib.request

import bus_core as B


def _http(url: str, body=None, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def auto_peers(targets, http=None) -> dict:
    """能由插件直接发进会话的 Agent：{peer: 插件 id}。插件没响应就跳过（不影响复制通道）。"""
    out, http = {}, http or _http
    for pid, url in targets:
        try:
            for peer in http(url, timeout=3).get("bound") or []:
                out.setdefault(peer, pid)
        except Exception:  # noqa: BLE001
            continue
    return out


def send_via_plugins(peer: str, text: str, targets, http=None) -> dict:
    """按顺序问声明了 wake 的插件；第一个说「这个 Agent 有我管的会话」的负责发。都不管返回 sent=False（前端复制）。"""
    errors, http = [], http or _http
    for pid, url in targets:
        try:
            r = http(url, {"peer": peer, "text": text}, timeout=10)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}：{str(e)[:80]}")
            continue
        if r.get("bound"):
            return {"sent": True, "via": pid, "duplicate": bool(r.get("duplicate")), "status": r.get("status", "")}
    return {"sent": False, "errors": errors}


def wake_text(peer: str, model: str = "", link: str = "", label: str = "") -> str:
    """一段能直接发进该 Agent 对话的唤醒语。工具参数一律用真名 peer，显示名只进称呼。"""
    who = f"「{label}」（协议名 {peer}）" if label and label != peer else f"「{peer}」"
    args = f"peer={json.dumps(peer, ensure_ascii=False)}" + (f", model={json.dumps(model, ensure_ascii=False)}" if model else "")
    lines = [
        f"【Agent 协作台 · 唤醒】你是协作台里的 Agent{who}，之前在待命，但工作台已经有一段时间没收到你的心跳。",
        f"请现在调用 MCP 工具 bus_wait_task({args}) 回到待命循环：",
        "- 收到任务就按任务说明做，做完用 bus_reply 交回，再调用 bus_wait_task；",
        "- 返回 IDLE 时不要输出任何文字，直接再次调用 bus_wait_task；",
        "- 不要主动结束这一轮对话，也不要等我回复。",
    ]
    if link:
        lines.append(f"如果你这里已经找不到 bus_wait_task 工具（MCP 连接被宿主断开了），先把这个地址重新添加为 MCP 服务器，再按上面做：{link}")
    return "\n".join(lines)

