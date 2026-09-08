# ============================================================
# agents/coder.py | v0.2诞生 → v0.3真实工作区能力
# 变更: 工具从空集 → 工作区文件+依赖安装；提示词重写为工作流约束
# ============================================================
from langchain.agents import create_agent
from agent_pro.agents.base import AgentSpec
from agent_pro.config import build_model
from agent_pro.tools import CODER_TOOLS

CODER_SYSTEM_PROMPT = """你是资深软件工程师，在工作区内以真实文件交付复杂代码任务。
你无法反问用户，需求不明确时先列出假设再继续，不要停下。
工作流程（严格遵守）:
1. 规划: 先在回复中写出完整文件结构规划
2. 实现: 逐文件调用 write_file 全量写入（工具为覆盖语义，修改=全量重写）
3. 依赖: 生成 requirements.txt；需要第三方库时调用 pip_install 安装
4. 交付: 末尾列出最终文件树、运行步骤、验证方法
规则:
- 每个文件一次写入完整内容，禁止用 "..." 省略
- 路径一律使用工作区相对路径（如 app/main.py）
- 工具返回错误时，读取错误信息修正后重试；连续失败3次则如实报告已完成部分与失败原因
- 绝不声称"已写入"除非工具返回确认——以工具返回为准，禁止编造
"""


def build_coder_agent():
    return create_agent(
        build_model(),
        tools=CODER_TOOLS,
        system_prompt=CODER_SYSTEM_PROMPT,
    )


CODER_SPEC = AgentSpec(
    name="coder",
    description=(
        "代码生成任务：根据用户需求在工作区生成真实可运行的代码文件，"
        "包括单文件应用、命令行工具(CLI)、脚本以及多文件项目。"
        "例如'帮我写一个Python命令行待办应用'、'写个爬虫'、'实现一个带持久化的CLI'。"
        "只要用户要求产出代码文件（而非单纯解释概念或单函数片段问答），"
        "都应当调度本专家来真实写入文件。"
    ),
    build=build_coder_agent,
)