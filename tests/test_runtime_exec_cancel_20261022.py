"""runtime_exec.run 被连续取消两次（MCP 会话断开时常见：工具调用先被取消，收尾途中会话终止又取消一次）：

- 不能留下「_GatheringFuture exception was never retrieved」（2026-10-22 体检：mcp_service_8791.log 里 100 多段）；
- 收尾不能被第二次取消打断：子进程照样回收，取消照样往上抛。
"""
import asyncio
import gc
import os
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import runtime_exec as RX  # noqa: E402

PID_FILE_SCRIPT = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True).stdout or b""
        return f" {pid} ".encode() in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DoubleCancel(unittest.TestCase):
    def test_double_cancel_is_clean(self):
        pid_file = os.path.join(ROOT, "tmp", f"rx-cancel-{os.getpid()}.pid")
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        complaints = []

        async def main():
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _l, ctx: complaints.append(ctx.get("message", "")))
            task = asyncio.create_task(RX.run([sys.executable, "-c", PID_FILE_SCRIPT, pid_file], cwd=ROOT, timeout=60))
            for _ in range(100):
                if os.path.exists(pid_file) and open(pid_file).read().strip():
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.3)     # pump 被换成读不完的假实现：收尾一定停在「等 pump 读完」那 3 秒里
            self.assertFalse(task.done(), "要在收尾途中第二次取消")
            task.cancel()                # 收尾途中再取消一次
            try:
                await task
                raise AssertionError("取消要往上抛")
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.2)
            gc.collect()                 # 没人取的异常在回收时经 loop 报出来
            await asyncio.sleep(0.05)

        real_pump = RX._pump

        async def stuck_pump(stream, tail):   # 模拟输出管道迟迟不关（孙进程还握着句柄之类）
            await asyncio.Event().wait()

        RX._pump = stuck_pump
        try:
            asyncio.run(main())
        finally:
            RX._pump = real_pump
        gc.collect()
        pid = int(open(pid_file).read().strip())
        os.remove(pid_file)
        deadline = time.time() + 5
        while _alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(_alive(pid), "被取消的子进程要回收")
        self.assertFalse([c for c in complaints if "never retrieved" in c], complaints)


if __name__ == "__main__":
    unittest.main()
