# ============================================================
# agents/general.py | v0.2 诞生（通用Agent — 意图识别+调度中枢）
# 演进自: v0.1 main.py 的 build_agent
# ============================================================
from langchain.agents import create_agent
from agent_pro.agents.base import AgentRegistry
from agent_pro.config import build_model
from agent_pro.tools.builtin import calculator, get_current_time

GENERAL_SYSTEM_PROMPT = """你是通用助理，同时是多Agent系统的调度中枢。
判断规则（按优先级）：
1. 直接回答类：常识问答、闲聊、时间/计算查询，以及"解释/片段级"代码问题
   （如"如何用Python读JSON"、"写个快排函数"、"这段报错什么意思"）
   → 直接回答或调用基础工具，禁止调度专家
2. 调度类：任何"构建 / 生成 / 实现完整程序或应用代码"的请求
   （如"帮我写一个Python命令行待办应用"、"写个爬虫项目"、"实现带持久化的CLI"）
   → 必须调用 dispatch_to_coder 调度给专家，即使只有单文件、即使需求看起来简单。
   判断标准不是"任务难不难"，而是"是否要产出可运行的代码文件"。
3. 调度时 task_description 必须自包含：完整转述用户需求与已知上下文，
   子Agent看不到对话历史
4. 专家返回后，整理为清晰的最终答案；专家失败时如实转告，不得编造
"""


def build_general_agent(registry: AgentRegistry, extra_tools: list | None = None):
    tools = [get_current_time, calculator, *registry.as_handoff_tools()]
    if extra_tools:
        tools.extend(extra_tools)
    return create_agent(
        build_model(),
        tools=tools,
        system_prompt=GENERAL_SYSTEM_PROMPT,
    )