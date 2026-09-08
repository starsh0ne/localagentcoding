"""调试用：非交互驱动通用Agent，验证 coder 是否真的写文件到工作区。
用临时工作区(import 前设 WORKSPACE_DIR)，避免触碰已存在目录。"""
import os
import tempfile

_TMP = os.path.join(tempfile.gettempdir(), "agent_pro_test_ws")
os.environ["WORKSPACE_DIR"] = _TMP

from agent_pro.agents import create_app_agent  # noqa: E402
from agent_pro.config import get_settings  # noqa: E402

WS = get_settings().workspace_dir
print(">>> 测试工作区:", WS)

agent = create_app_agent()
prompt = "这是一个复杂任务，帮我写一个Python命令行待办应用，单文件todo.py，支持add/list/done，JSON持久化"

print(">>> 发送:", prompt)
result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})

msgs = result["messages"]
dispatched = any(
    getattr(m, "type", "") == "ai"
    and any(str(tc.get("name", "")).startswith("dispatch_to_") for tc in (getattr(m, "tool_calls", None) or []))
    for m in msgs
)
print("\n>>> 是否调用 dispatch_to_coder:", dispatched)

todo = WS / "todo.py"
print(">>> workspace/todo.py 是否存在:", todo.exists())
if todo.exists():
    print(">>> todo.py 前12行:\n" + "".join([f"    {l}\n" for l in todo.read_text(encoding="utf-8").splitlines()[:12]]))
else:
    print(">>> 工作区内容:", [p.name for p in WS.rglob("*")] if WS.exists() else "(无工作区)")
