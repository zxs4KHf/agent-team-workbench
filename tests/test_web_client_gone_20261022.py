"""工作台 HTTP 服务：客户端中途断开（关页、SSE 重连、代理超时）不再整段打堆栈，其它异常照常打印。

2026-10-22 体检：console/workbench_8765.log 两万多行里 1180 多段堆栈都是 ConnectionAbortedError，
而且 do_GET 的通用 except 还会往已断开的连接上再写一次 400，报两遍错，把真故障埋掉。
"""
import contextlib
import io
import os
import socket
import sys
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bus_web as W  # noqa: E402


def _handle_error_output(exc):
    srv = W.QuietServer.__new__(W.QuietServer)   # 只测 handle_error，不绑端口
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            raise exc
        except Exception:
            srv.handle_error(None, ("127.0.0.1", 1))
    return buf.getvalue()


class ClientGone(unittest.TestCase):
    def test_disconnects_are_silent(self):
        for exc in (ConnectionAbortedError(10053, "aborted"), ConnectionResetError(10054, "reset"), BrokenPipeError(32, "pipe")):
            self.assertEqual(_handle_error_output(exc), "", type(exc).__name__)

    def test_real_errors_still_print(self):
        out = _handle_error_output(ValueError("真故障"))
        self.assertIn("ValueError", out)
        self.assertIn("真故障", out)

    def test_server_survives_abrupt_close(self):
        srv = W.QuietServer(("127.0.0.1", 0), W.Handler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            for _ in range(5):   # 发完请求立刻硬断（RST），模拟关页
                s = socket.create_connection(("127.0.0.1", port))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                s.sendall(b"GET /api/settings HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                s.close()
            time.sleep(0.5)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/index.html", timeout=10) as r:
                self.assertEqual(r.status, 200, "硬断之后服务照常应答")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
