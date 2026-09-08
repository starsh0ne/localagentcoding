# ============================================================
# main.py | v0.1诞生 → v0.2重构为薄CLI入口
# 变更: 业务逻辑全部迁入src/agent_pro，本文件只剩交互循环
# 已知债务: ① 历史无限增长无压缩 → v0.4
#           ② 会话无持久化 → v0.4
#           ③ CLI形态 → v0.5 API服务化
# ============================================================
from agent_pro.agents import create_app_agent


def main():
    agent = create_app_agent()
    history = []
    print("Agent Pro v0.2（多Agent调度架构）— 输入 exit 退出")
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        history.append({"role": "user", "content": user_input})
        result = agent.invoke({"messages": history})
        history = result["messages"]  # 回填完整轨迹（含调度调用）
        print(f"\nAgent: {history[-1].content}")


if __name__ == "__main__":
    main()