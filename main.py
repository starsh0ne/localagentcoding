# ============================================================
# main.py | v0.1诞生 → v0.3流式 → v0.4会话持久化接入
# 变更: 裸history → SessionManager(DB重建+压缩+偏好注入)
#       上下文每轮从DB重建 → 与v0.5 API无状态形态提前对齐
# 用法: uv run main.py --new | --session <id>
# ============================================================
import argparse
from datetime import datetime
from agent_pro.agents import create_app_agent
from agent_pro.memory.manager import SessionManager


def _print_progress(msg) -> None:
    for tc in getattr(msg, "tool_calls", None) or []:
        brief = str(tc.get("args", ""))[:70].replace("\n", " ")
        print(f"    ⚙ {tc.get('name')}({brief}…)")
        if str(tc.get("name", "")).startswith("dispatch_to_"):
            print("    ⏳ 专家执行中（长任务，可能需要数分钟）…")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="default", help="会话ID(续聊)")
    ap.add_argument("--new", action="store_true", help="开启新会话")
    args = ap.parse_args()
    sid = f"s-{datetime.now():%Y%m%d-%H%M%S}" if args.new else args.session
    session = SessionManager(sid)
    agent = create_app_agent()
    print(f"Agent Pro v0.4（记忆+任务系统）会话: {sid} — 输入exit退出")
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue
        session.append_user(user_input)
        messages = session.build_messages()  # 每轮从DB重建: 摘要+偏好+最近5轮
        final = None
        for update in agent.stream({"messages": messages}, stream_mode="updates"):
            for payload in update.values():
                chunk = payload.get("messages", []) if isinstance(payload, dict) else []
                if not isinstance(chunk, list):
                    chunk = [chunk]
                for m in chunk:
                    _print_progress(m)
                    if getattr(m, "type", "") == "ai":
                        final = m
        text = str(final.content) if final else "(无输出)"
        session.append_assistant(text)
        session.maybe_compact()  # 攒满10轮触发: 压缩+价值提取+过期删除
        print(f"\nAgent: {text}")


if __name__ == "__main__":
    main()