"""命令行 MCP 客户端：给没有原生 MCP 支持的 Agent 用，调 bus_wait_task / bus_reply。

    py -3 bus_mcp_client.py wait [timeout_sec]
    py -3 bus_mcp_client.py reply <task_id> <answer_file>

地址含令牌，不入库：优先读环境变量 BUS_MCP_URL，否则读 .runtime/mcp_client_url.txt（已在 .gitignore）。
身份用 BUS_MCP_PEER / BUS_MCP_MODEL 覆盖，默认 gemini1 / gemini-3.8-flash。
"""
import json
import os
import sys
import time
import http.client
import urllib.request
import urllib.error

_URL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime", "mcp_client_url.txt")
PEER = os.environ.get("BUS_MCP_PEER", "gemini1")
MODEL = os.environ.get("BUS_MCP_MODEL", "gemini-3.8-flash")


def _load_url() -> str:
    url = os.environ.get("BUS_MCP_URL", "").strip()
    if not url and os.path.exists(_URL_FILE):
        with open(_URL_FILE, "r", encoding="utf-8") as f:
            url = f.read().strip()
    return url


MCP_URL = _load_url()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json"
}

_SESSION_ID = None

def _rpc_call(payload: dict, timeout: int = 600, retries: int = 3):
    global _SESSION_ID
    for attempt in range(retries):
        h = dict(HEADERS)
        if _SESSION_ID:
            h["mcp-session-id"] = _SESSION_ID
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode("utf-8"), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sess = resp.headers.get("mcp-session-id")
                if sess:
                    _SESSION_ID = sess
                raw = resp.read().decode("utf-8", errors="replace")
                return _parse_response(raw)
        except http.client.IncompleteRead as e:
            raw = e.partial.decode("utf-8", errors="replace")
            parsed = _parse_response(raw)
            if parsed is not None:
                return parsed
        except (urllib.error.URLError, TimeoutError, ConnectionResetError) as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            return {"error": str(e)}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            return {"error": f"HTTP {e.code}: {err_body}"}
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            return {"error": str(e)}
    return {"error": "max_retries_exceeded"}

def _parse_response(raw: str):
    for line in raw.splitlines():
        if line.startswith("data: "):
            data_str = line[6:].strip()
            try:
                return json.loads(data_str)
            except Exception:
                pass
    try:
        return json.loads(raw)
    except Exception:
        pass
    if ": ping" in raw or "IDLE" in raw:
        return {"result": {"content": [{"type": "text", "text": "IDLE. Call bus_wait_task again."}]}}
    return {"raw": raw}

def ensure_initialized():
    global _SESSION_ID
    if _SESSION_ID:
        return True
    for attempt in range(5):
        init_res = _rpc_call({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": PEER, "version": "1.0.0"}
            }
        }, timeout=20)
        if init_res and "error" not in init_res:
            _rpc_call({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
            }, timeout=20)
            return True
        time.sleep(2)
    return False

def wait_task(peer=PEER, model=MODEL, timeout_sec=25):
    global _SESSION_ID
    if not ensure_initialized():
        _SESSION_ID = None
        if not ensure_initialized():
            return "ERROR: failed to initialize MCP session"
    res = _rpc_call({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "bus_wait_task",
            "arguments": {
                "peer": peer,
                "model": model,
                "timeout_sec": timeout_sec
            }
        }
    }, timeout=timeout_sec + 30)
    if "result" in res and "content" in res["result"]:
        content = res["result"]["content"]
        if isinstance(content, list) and len(content) > 0 and "text" in content[0]:
            return content[0]["text"]
    if "raw" in res and "IDLE" in res["raw"]:
        return "IDLE. Call bus_wait_task again."
    return json.dumps(res, ensure_ascii=False)

def reply(task_id, peer=PEER, answer=""):
    if not ensure_initialized():
        return "ERROR: failed to initialize MCP session"
    res = _rpc_call({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "bus_reply",
            "arguments": {
                "task_id": task_id,
                "peer": peer,
                "answer": answer
            }
        }
    }, timeout=60)
    if "result" in res and "content" in res["result"]:
        content = res["result"]["content"]
        if isinstance(content, list) and len(content) > 0 and "text" in content[0]:
            return content[0]["text"]
    return json.dumps(res, ensure_ascii=False)

if __name__ == "__main__":
    if not MCP_URL:
        sys.exit("未配置 MCP 地址：设置环境变量 BUS_MCP_URL，或写入 " + _URL_FILE)
    action = sys.argv[1] if len(sys.argv) > 1 else "wait"
    if action == "wait":
        timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        print(wait_task(timeout_sec=timeout))
    elif action == "reply":
        task_id = sys.argv[2]
        ans_file = sys.argv[3]
        with open(ans_file, "r", encoding="utf-8") as f:
            ans = f.read()
        print(reply(task_id=task_id, answer=ans))
