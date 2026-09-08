# ============================================================
# main.py | v0.1 诞生（最小智能体循环）
# 已知债务: ① eval存在注入风险 → v0.3沙箱修复
#           ② 历史无限增长无压缩 → v0.4升级
#           ③ 无会话持久化 → v0.4检查点
# ============================================================
import os
from datetime import datetime
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
load_dotenv()
@tool
def get_current_time() -> str:
    """获取当前系统时间，格式 YYYY-MM-DD HH:MM:SS。当用户询问时间/日期时使用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
@tool
def calculator(expression: str) -> str:
    """计算数学表达式，如 '2**10 + 3*7'。仅支持纯数学运算。"""
    # 注意: eval 是安全隐患，v0.3 将替换为受限求值
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"计算失败: {type(e).__name__}: {e}"  # 结构化错误，供模型自纠
def build_agent():
    model = ChatOpenAI(
        model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL") or None,
    )
    return create_agent(
        model,
        tools=[get_current_time, calculator],
        system_prompt=(
            "你是一个精准的助手。"
            "涉及时间或数学计算时，必须调用工具获取结果，禁止凭记忆回答。"
            "工具失败时，如实告知用户，不得编造结果。"
        ),
    )
def main():
    agent = build_agent()
    history = []  # v0.1: 裸列表，v0.4 将替换为 Session 管理
    print("Agent v0.1 已启动（输入 exit 退出）")
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        history.append({"role": "user", "content": user_input})
        result = agent.invoke({"messages": history})
        # 关键: 回填完整轨迹（含工具调用消息），
        # 否则下一轮模型不知道自己调过什么工具，会重复调用
        history = result["messages"]
        print(f"Agent: {history[-1].content}")
if __name__ == "__main__":
    main()