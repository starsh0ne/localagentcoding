# ============================================================
# main.py | v0.1诞生 → v0.2薄CLI → v0.3流式进度(长任务可视化)
# 变更: invoke → stream(updates)，工具调用实时可见
# 已知债务: ① 历史无限增长无压缩 → v0.4
#           ② 会话无持久化 → v0.4
#           ③ 嵌套子Agent内部进度不可见(只见dispatch整体) → v0.7观测性
# ============================================================
from agent_pro.agents import create_app_agent


def _print_progress(msg) -> None:
    """流式打印工具执行进度——长任务不再无声等待"""
    for tc in getattr(msg, "tool_calls", None) or []:
        brief = str(tc.get("args", ""))[:70].replace("\n", " ")
        print(f"    ⚙ {tc.get('name')}({brief}…)")
        if str(tc.get("name", "")).startswith("dispatch_to_"):
            print("    ⏳ 专家执行中（长任务，可能需要数分钟）…")
    if getattr(msg, "type", "") == "tool":
        brief = str(msg.content)[:70].replace("\n", " ")
        print(f"    ✔ {msg.name}: {brief}")


def main():
    agent = create_app_agent()
    history = []
    print("Agent Pro v0.3（Coder工作区模式）— 输入 exit 退出")
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        history.append({"role": "user", "content": user_input})
        new_msgs = []
        # updates模式: 每完成一个节点(模型/工具)即产出增量
        for update in agent.stream({"messages": history}, stream_mode="updates"):
            for payload in update.values():
                chunk = payload.get("messages", []) if isinstance(payload, dict) else []
                if not isinstance(chunk, list):
                    chunk = [chunk]
                for m in chunk:
                    new_msgs.append(m)
                    _print_progress(m)
        history.extend(new_msgs)  # 回填完整轨迹
        if new_msgs:
            print(f"\nAgent: {new_msgs[-1].content}")


if __name__ == "__main__":
    main()