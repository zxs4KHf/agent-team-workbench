import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # 从 tests/ 运行时找到根目录模块
# workbench/bus/test_prompt_48cc.py
# 针对任务 20260921-052052-48cc 的真实函数自动化测试脚本

import os
import sys
import urllib.request
import urllib.parse
import hashlib

# 确保导入当前工作区的 bus_projects
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if CUR_DIR not in sys.path:
    sys.path.insert(0, CUR_DIR)

import bus_projects as P

print("=== [测试开始] 接入提示词 prompt_text 真实函数自动化校验 ===\n")

# 1. 真实函数首段与核心特征校验 (Controller)
ctrl_prompt = P.prompt_text("controller")
ctrl_lines = [line.strip() for line in ctrl_prompt.split("\n") if line.strip()]

assert len(ctrl_lines) > 0, "主控提示词不可为空"
first_paragraph_ctrl = ctrl_lines[0]
assert first_paragraph_ctrl.startswith("先连接这个 Streamable HTTP MCP 地址："), \
    f"主控提示词首段必须以连接 MCP 地址开头，实际首行：{first_paragraph_ctrl[:40]}"

assert "优先使用宿主环境自带的原生 MCP 连接器" in ctrl_prompt, "必须指明优先原生 MCP"
assert "initialize → notifications/initialized → tools/list" in ctrl_prompt, "必须包含标准握手步骤"
assert "远程 MCP 连接能力不同于本机 Windows 命令行执行能力" in ctrl_prompt, "必须辨析远程 MCP 与本地 Windows 命令"
assert "始终允许" not in ctrl_prompt, "必须删除'始终允许所有工具'的强制豁免要求"
assert "维持有界心跳与有效租约" in ctrl_prompt, "必须保留维持有界心跳与有效租约规则"
assert "bus_console(action=\"wait\")" in ctrl_prompt, "必须保留主控 wait 循环"
assert "ANSWERS_COMPLETE" in ctrl_prompt, "必须保留 ANSWERS_COMPLETE 规则"

print("[PASS] [1/5] 主控提示词验证通过：首段先连接 MCP，具备握手指导，删除强制放行，保留核心流转规则")

# 2. 真实函数首段与核心特征校验 (Peer / Teammate)
peer_prompt = P.prompt_text("peer", peer="astra1", model="gpt-6-astra")
peer_lines = [line.strip() for line in peer_prompt.split("\n") if line.strip()]

assert len(peer_lines) > 0, "Peer 提示词不可为空"
first_paragraph_peer = peer_lines[0]
assert first_paragraph_peer.startswith("先连接这个 Streamable HTTP MCP 地址："), \
    f"Peer 提示词首段必须以连接 MCP 地址开头，实际首行：{first_paragraph_peer[:40]}"

assert "astra1" in peer_prompt, "必须包含指定的 peer 名称"
assert "gpt-6-astra" in peer_prompt, "必须包含指定的 model 名称"
assert "bus_wait_task(peer=\"astra1\", model=\"gpt-6-astra\")" in peer_prompt, "必须包含标准 wait 循环参数"
assert "返回 IDLE 时不输出任何文字" in peer_prompt, "必须保留 IDLE 静默"
assert "astra1 已待命。" in peer_prompt, "必须保留待命回复规范"
assert "备选接入路径（仅限当前会话确系运行在宿主目标 Windows 工作区本地" in peer_prompt, "CLI 必须置于末尾备选并限制工作区环境"

print("[PASS] [2/5] Peer 提示词验证通过：首段先连接 MCP，角色后置，保留静默与循环，CLI 置于末尾备选")

# 3. 动态 URL 与特殊字符抗压测试
test_bridge_url = "https://custom-domain.example.com/mcp/sess_12345?auth=none&tag=v1#anchor"
orig_bridge_url = P.bridge_url

try:
    P.bridge_url = lambda: test_bridge_url
    dynamic_ctrl = P.prompt_text("controller")
    dynamic_peer = P.prompt_text("peer", peer="agent<test>&'special'", model="model:beta/v1.0")
    
    assert test_bridge_url in dynamic_ctrl, "主控提示词中必须包含动态登记的 URL"
    assert test_bridge_url in dynamic_peer, "Peer 提示词中必须包含动态登记的 URL"
    assert "agent<test>&'special'" in dynamic_peer, "特殊字符 peer 名称需完整保留"
    assert "model:beta/v1.0" in dynamic_peer, "特殊字符 model 名称需完整保留"
    print("[PASS] [3/5] 动态 URL 与特殊字符验证通过：动态读取 Bridge 地址，特殊符号完整透传")
finally:
    # Restore only this isolated test process; never write production bridge.json.
    P.bridge_url = orig_bridge_url

# 4. 无执行与网络能力降级提示测试
assert "若当前运行环境确实不具备任何网络访问或代码执行工具，请如实报告阻断并提示用户在宿主客户端配置中添加该 URL" in ctrl_prompt, \
    "主控提示词必须包含无执行能力的如实报告阻断与最小设置引导"
assert "不可假装成功或越权热加载" in ctrl_prompt, "必须明确禁止假装成功或越权热加载"
assert "若当前运行环境确实不具备任何网络访问或代码执行工具，请如实报告阻断并提示用户在宿主客户端配置中添加该 URL" in peer_prompt, \
    "Peer 提示词必须包含无执行能力的如实报告阻断与最小设置引导"
print("[PASS] [4/5] 降级提示验证通过：包含环境不具备工具时的如实报告与最小步骤指引")

# 5. 比较磁盘函数与当前运行中的 /api/prompt 接口
print("\n正在探测本机运行中的 Web 服务接口 (http://127.0.0.1:8765/api/prompt)...")
server_prompt_ctrl = None
server_prompt_peer = None
server_is_live = False

try:
    req_ctrl = urllib.request.Request("http://127.0.0.1:8765/api/prompt?kind=controller")
    with urllib.request.urlopen(req_ctrl, timeout=2) as resp:
        server_prompt_ctrl = resp.read().decode("utf-8")
    req_peer = urllib.request.Request("http://127.0.0.1:8765/api/prompt?kind=peer&peer=astra1&model=gpt-6-astra")
    with urllib.request.urlopen(req_peer, timeout=2) as resp:
        server_prompt_peer = resp.read().decode("utf-8")
    server_is_live = True
except Exception as e:
    print(f"提示：当前未能直接连接到 127.0.0.1:8765 上的服务 ({e})")

disk_prompt_ctrl = P.prompt_text("controller")
disk_prompt_peer = P.prompt_text("peer", peer="astra1", model="gpt-6-astra")

if server_is_live:
    ctrl_match = (server_prompt_ctrl == disk_prompt_ctrl)
    peer_match = (server_prompt_peer == disk_prompt_peer)
    print(f"运行中服务状态：已连通")
    print(f"- Controller 接口与磁盘函数一致性: {'一致' if ctrl_match else '不一致（运行进程持有旧模块缓存，需在授权后由运维/主控重启生效）'}")
    print(f"- Peer 接口与磁盘函数一致性: {'一致' if peer_match else '不一致（运行进程持有旧模块缓存，需在授权后由运维/主控重启生效）'}")
    if not ctrl_match:
        print("注意：根据任务约束，代码修改不等于实时生效，严禁未经授权强行重启生产服务进程。")
else:
    print("运行中服务状态：端口 8765 离线或不可达，磁盘代码已准确就绪。")

print("\n=== 全部自动化断言校验顺利完成！ ===")
