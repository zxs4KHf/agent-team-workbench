# -*- coding: utf-8 -*-
"""Codex 同 logical turn 的 turn-state 透传（内存 / first-write-wins / auth 绑定）。

任务 20260929-154823-69f3，落地 20260928-200817-6334 合并稿的收窄方案。

**状态：未上线。** 本模块是独立、默认不接线的状态机，仓库里没有任何正式的 egress 数据面
调用它（见同目录 README.md 的「定位结论」）。它不发真实网络、不读本机凭据 / 配置、
不写 Excel / 日志 / session JSON / 导出文件。

已被合并稿否决、本模块**刻意不提供**的能力：
  * 跨 logical turn 的注入与复用；
  * 跨 auth 的复用；
  * 任何基于 state 长度的判定、分级、徽标、观测（社区那套长度白名单）；
  * 任何把 state 原值写到进程之外的路径。
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

HEADER_NAME = "x-codex-turn-state"
# 超长忽略的上限；只用于「忽略 / 不忽略」这一个二元决定，绝不用于判定风控、质量或账号状态。
DEFAULT_MAX_LEN = 8192
# 允许透传的上游标识；其它上游一律不发送。
CODEX_UPSTREAM = "codex"


class TurnStateConfig:
    """透传开关。默认 off —— 调用方必须显式开启。

    这里只提供未来接线所需的 flag 接口，不读取任何 Excel / 配置文件。
    """

    __slots__ = ("enabled", "max_len", "upstream")

    def __init__(self, enabled: bool = False, max_len: int = DEFAULT_MAX_LEN, upstream: str = CODEX_UPSTREAM) -> None:
        self.enabled = bool(enabled)
        self.max_len = int(max_len) if int(max_len) > 0 else DEFAULT_MAX_LEN
        self.upstream = str(upstream or "")


def _is_usable_id(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _normalize_value(value: Any, max_len: int) -> Optional[str]:
    """空 / 空白 / 非字符串 / 含控制字符 / 超长 → None（忽略，不中断响应）。"""
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("ascii")
        except Exception:
            return None
    if not isinstance(value, str):
        return None
    if value.strip() == "":
        return None
    if len(value) > max_len:
        return None
    # 畸形：请求头里不能出现控制字符 / 换行（拆头攻击）
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return None
    return value


class TurnStateStore:
    """内存 Map<(auth_id, turn_id) -> value>，first-write-wins。

    * 新 logical turn 无状态开始（没有条目就是没有）。
    * turn 结束 / auth 切换 / 进程重启清空（重启由「只存在内存里」天然保证）。
    * auth_id 或 turn_id 缺失：既不缓存也不发送。
    * 并发 first-write 原子：同一把锁内 setdefault。
    """

    def __init__(self, config: Optional[TurnStateConfig] = None) -> None:
        self._config = config or TurnStateConfig()
        self._lock = threading.Lock()
        self._values: Dict[Tuple[str, str], str] = {}
        self._current_auth: Optional[str] = None

    # ---- 配置 ----
    @property
    def config(self) -> TurnStateConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ---- 生命周期 ----
    def begin_turn(self, auth_id: Any, turn_id: Any) -> None:
        """新 logical turn 开始：确保该 (auth, turn) 从无状态起步。"""
        if not (_is_usable_id(auth_id) and _is_usable_id(turn_id)):
            return
        with self._lock:
            self._values.pop((auth_id, turn_id), None)

    def end_turn(self, auth_id: Any, turn_id: Any) -> None:
        if not (_is_usable_id(auth_id) and _is_usable_id(turn_id)):
            return
        with self._lock:
            self._values.pop((auth_id, turn_id), None)

    def switch_auth(self, auth_id: Any) -> None:
        """auth 切换：把旧 auth 的全部状态清掉（CLIProxyAPI #5985 那类跨账号泄漏的直接对策）。"""
        with self._lock:
            new_auth = auth_id if _is_usable_id(auth_id) else None
            if self._current_auth is not None and new_auth != self._current_auth:
                for key in [k for k in self._values if k[0] == self._current_auth]:
                    self._values.pop(key, None)
            self._current_auth = new_auth

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._current_auth = None

    # ---- 读写 ----
    def capture(self, auth_id: Any, turn_id: Any, value: Any) -> bool:
        """记录同轮首个可用值。返回 True 表示本次写入生效（first write）。

        后续不同值不覆盖；非法值忽略；缺 id 不缓存。
        """
        if not self._config.enabled:
            return False
        if not (_is_usable_id(auth_id) and _is_usable_id(turn_id)):
            return False
        v = _normalize_value(value, self._config.max_len)
        if v is None:
            return False
        key = (auth_id, turn_id)
        with self._lock:
            if key in self._values:
                return False
            self._values[key] = v
            if self._current_auth is None:
                self._current_auth = auth_id
            return True

    def replay(self, auth_id: Any, turn_id: Any) -> Optional[str]:
        """同 turn 的 sampling / tool continuation / inline compaction 回放。跨轮跨 auth 一律拿不到。"""
        if not self._config.enabled:
            return None
        if not (_is_usable_id(auth_id) and _is_usable_id(turn_id)):
            return None
        with self._lock:
            return self._values.get((auth_id, turn_id))

    def size(self) -> int:
        with self._lock:
            return len(self._values)

    # ---- 日志接口：默认无值 ----
    def log_fields(self, auth_id: Any, turn_id: Any, with_digest: bool = False) -> Dict[str, Any]:
        """只给 present(bool)；显式要求时再给不可逆摘要。绝不返回原值，也不返回精确长度。"""
        v = self.replay(auth_id, turn_id)
        out: Dict[str, Any] = {"turn_state_present": v is not None}
        if with_digest and v is not None:
            out["turn_state_digest"] = hashlib.sha256(v.encode("utf-8")).hexdigest()[:12]
            out["turn_state_len_bucket"] = length_bucket(len(v))
        return out


def length_bucket(n: int) -> str:
    """不可逆长度桶。所有非空长度落进同一个桶 —— 长度不得产生任何分类差异。"""
    return "nonempty" if n > 0 else "empty"


class TurnStateError(Exception):
    """异常信息里只允许出现 present / 桶，不允许出现原值。"""


# ---------------- HTTP adapter ----------------
class HttpTurnStateAdapter:
    """只透传允许的 x-codex-turn-state 请求 / 响应头。不发真实网络。"""

    def __init__(self, store: TurnStateStore) -> None:
        self.store = store

    def _allowed(self, upstream: Any) -> bool:
        return self.store.enabled and str(upstream or "") == self.store.config.upstream

    def request_headers(self, auth_id: Any, turn_id: Any, upstream: Any = CODEX_UPSTREAM) -> Dict[str, str]:
        """返回要附加到出站请求上的头。flag off / 非 Codex / 无状态 → 空 dict。"""
        if not self._allowed(upstream):
            return {}
        v = self.store.replay(auth_id, turn_id)
        return {HEADER_NAME: v} if v else {}

    def observe_response(self, auth_id: Any, turn_id: Any, headers: Optional[Mapping[str, Any]], upstream: Any = CODEX_UPSTREAM) -> bool:
        """从响应头捕获首个值。任何异常输入都只是「忽略」，不抛出、不中断响应。"""
        if not self._allowed(upstream):
            return False
        if not headers:
            return False
        value = None
        try:
            for k, v in dict(headers).items():
                if isinstance(k, str) and k.lower() == HEADER_NAME:
                    value = v
                    break
        except Exception:
            return False
        return self.store.capture(auth_id, turn_id, value)


# ---------------- WebSocket adapter ----------------
class WsTurnStateAdapter:
    """只在 response.create.client_metadata 发送、从 response.metadata 事件读取。"""

    def __init__(self, store: TurnStateStore) -> None:
        self.store = store

    def _allowed(self, upstream: Any) -> bool:
        return self.store.enabled and str(upstream or "") == self.store.config.upstream

    def decorate_response_create(self, event: Dict[str, Any], auth_id: Any, turn_id: Any, upstream: Any = CODEX_UPSTREAM) -> Dict[str, Any]:
        """在 response.create 事件上挂 client_metadata；其它事件类型原样返回。"""
        if not isinstance(event, dict) or event.get("type") != "response.create":
            return event
        if not self._allowed(upstream):
            return event
        v = self.store.replay(auth_id, turn_id)
        if not v:
            return event
        out = dict(event)
        response = dict(out.get("response") or {})
        meta = dict(response.get("client_metadata") or {})
        meta[HEADER_NAME] = v
        response["client_metadata"] = meta
        out["response"] = response
        return out

    def observe_event(self, event: Any, auth_id: Any, turn_id: Any, upstream: Any = CODEX_UPSTREAM) -> bool:
        """只认 response.metadata 事件里的值；别的位置一概不读。"""
        if not self._allowed(upstream):
            return False
        if not isinstance(event, dict):
            return False
        if event.get("type") != "response.metadata":
            return False
        meta = event.get("metadata")
        if not isinstance(meta, dict):
            return False
        return self.store.capture(auth_id, turn_id, meta.get(HEADER_NAME))


def scrub(text: Any, secrets: Iterable[Any] = ()) -> str:
    """给异常 / 日志用：把已知原值从文本里抹掉，避免值随报错外泄。"""
    s = "" if text is None else str(text)
    for sec in secrets:
        if isinstance(sec, str) and sec:
            s = s.replace(sec, "<redacted>")
    return s
