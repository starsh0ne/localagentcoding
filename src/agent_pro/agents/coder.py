# ============================================================
# agents/coder.py | v0.2诞生 → v0.3(工作区) → v0.4
# ★缺陷修复: description按三维量化重写——判据是"完整度"而非文件数,
#   "单文件完整应用"不再被误判为简单任务(用户实测踩坑点)
# 演进: build(context)绑定任务子目录; +read_task_plan/update_progress
# ============================================================
from pathlib import Path
from langchain.agents import create_agent
from agent_pro.agents.base import AgentSpec, TaskContext
from agent_pro.config import build_model, get_settings
from agent_pro.tools.env_tools import pip_install
from agent_pro.tools.task_tools import make_task_tools
from agent_pro.tools.workspace_files import make_workspace_tools

CODER_SYSTEM_PROMPT = """你是资深软件工程师，在专属工作区内以真实文件交付代码任务。
你无法反问用户：需求不明时先列出假设并继续，不要停下。
【任务模式】(工具列表含read_task_plan时，即本次为计划书任务):
0. 先调用read_task_plan获取计划书，严格按计划实现；合理调整须在进度中说明
1. 逐文件write_file全量写入(覆盖语义)；生成requirements.txt
2. 需要第三方库时调用pip_install安装
3. 每完成一个阶段调用update_progress记录(做了什么/发现/下一步)
4. 全部完成且自检通过后调用update_progress(finished=True)，
   并在最终回复给出: 文件树、运行步骤、验证方法
【自由模式】(无read_task_plan): 规划 → 逐文件实现 → 依赖 → 交付说明
通用规则:
- 每个文件一次写入完整内容，禁止"..."省略；路径用工作区相对路径
- 工具报错时读取错误信息修正后重试；连续失败3次则如实报告已完成部分与原因
- 绝不声称"已写入/已完成"，除非工具返回确认——以工具返回为准，禁止编造"""


def build_coder_agent(context: TaskContext | None = None):
    root: Path = context.workspace_dir if context else get_settings().workspace_dir
    tools = (make_workspace_tools(root)
             + [pip_install]
             + make_task_tools(context.task_id if context else None))
    return create_agent(build_model(), tools=tools, system_prompt=CODER_SYSTEM_PROMPT)


CODER_SPEC = AgentSpec(
    name="coder",
    # ★三维量化判据——这是模型决定是否调度的唯一依据
    description=(
        "①交付形态: 用户需要可运行的程序交付物——判据是完整度而非文件数量: "
        "多命令/多模块/持久化/交互流程中具备两项以上即适用(单文件完整应用同样在范围内);\n"
        "②技术跨度: 涉及第三方依赖安装、数据持久化、多组件组合，或需分步规划实现;\n"
        "③明确指令: 用户要求'完整实现/做成项目/交付可运行程序'。\n"
        "不调用情形: 概念或语法问答、单个函数级片段、用户明确表示只要思路或简单示例。"
    ),
    build=build_coder_agent,
)