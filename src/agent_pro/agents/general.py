# ============================================================
# agents/general.py | v0.2 诞生（通用Agent — 意图识别+调度中枢）
# 演进自: v0.1 main.py 的 build_agent
# ============================================================
from langchain.agents import create_agent
from agent_pro.agents.base import AgentRegistry
from agent_pro.config import build_model
from agent_pro.tools.builtin import calculator, get_current_time

GENERAL_SYSTEM_PROMPT = """你是通用助理，同时是多Agent系统的调度中枢。
判断规则：
1. 简单问题（常识问答、闲聊、时间查询、计算、单函数级代码问题）
   → 直接回答或调用基础工具，禁止调度专家
2. 复杂代码任务（预估成本大、多文件、需求不明确）
   → 调用 dispatch_to_coder 调度给专家
3. 调度时 task_description 必须自包含：完整转述用户需求与已知上下文，
   子Agent看不到对话历史
4. 专家返回后，整理为清晰的最终答案；专家失败时如实转告，不得编造
"""


def build_general_agent(registry: AgentRegistry):
    return create_agent(
        build_model(),
        tools=[get_current_time, calculator, *registry.as_handoff_tools()],
        system_prompt=GENERAL_SYSTEM_PROMPT,
    )