# ============================================================
# agents/coder.py | v0.2 诞生（Coder Agent — 骨架版）
# 当前能力: 代码专家提示词，可生成完整代码方案（纯文本交付）
# v0.3计划: 文件读写工具 + 代码执行沙箱，实现真实多文件项目生成
# ============================================================
from langchain.agents import create_agent

from agent_pro.agents.base import AgentSpec
from agent_pro.config import build_model

CODER_SYSTEM_PROMPT = """你是资深软件工程师，负责处理复杂代码任务。
你的输入是调度方转述的自包含任务描述，你无法反问用户，因此：
1. 需求不明确时，先列出你的理解和假设，再基于假设给方案
2. 多文件任务先给出文件结构规划，再逐文件完整实现
3. 代码必须完整可运行，禁止用 "..." 省略
4. 方案末尾说明：如何运行、如何验证结果
"""


def build_coder_agent():
    return create_agent(
        build_model(),
        tools=[],  # v0.3: 文件读写 + 代码执行沙箱
        system_prompt=CODER_SYSTEM_PROMPT,
    )


CODER_SPEC = AgentSpec(
    name="coder",
    description=(
        "复杂代码任务：预估成本大、涉及多文件、需求不完全明确的开发任务，"
        "如'帮我写一个完整的爬虫项目'、'实现一个带持久化的CLI应用'。"
        "注意：简单代码片段、语法问题、单函数问题不要调度，通用Agent直接回答。"
    ),
    build=build_coder_agent,
)