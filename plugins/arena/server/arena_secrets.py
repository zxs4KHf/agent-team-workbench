import base64
import ctypes
import ctypes.wintypes as wt
import sys

# 凭据落盘加密：Windows 用 DPAPI（CryptProtectData，当前用户范围），其他平台退化为标记后的 base64 以便本地测试
PREFIX_DPAPI = "dpapi:"
PREFIX_PLAIN = "b64:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    inp = _blob(data)
    out = _DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def seal(text: str) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        return PREFIX_DPAPI + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return PREFIX_PLAIN + base64.b64encode(raw).decode("ascii")


def unseal(token: str) -> str:
    if not token:
        return ""
    if token.startswith(PREFIX_DPAPI):
        return _dpapi(base64.b64decode(token[len(PREFIX_DPAPI):]), False).decode("utf-8")
    if token.startswith(PREFIX_PLAIN):
        return base64.b64decode(token[len(PREFIX_PLAIN):]).decode("utf-8")
    return token


def is_sealed(token: str) -> bool:
    return bool(token) and (token.startswith(PREFIX_DPAPI) or token.startswith(PREFIX_PLAIN))
