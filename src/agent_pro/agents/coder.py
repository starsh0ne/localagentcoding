# ============================================================
# agents/coder.py | v0.2诞生 → v0.3(三防) → v0.4(铁律修复+TaskContext)
#                → v0.5: ① handoff由invoke改stream——coder内部每次
#                  write_file/pip_install实时推送到task:{task_id}话题
#                  ② 收编原base.py的TaskContext/AgentSpec/AgentRegistry
#                  (现为本包唯一实现源，base.py仅再导出)
# 铁律不变: 动态描述必须走@tool(description=...)参数
# ============================================================
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from langchain.agents import create_agent
from langchain_core.runnables import Runnable
from langchain.tools import tool
from langgraph.errors import GraphRecursionError
from agent_pro.api.bus import get_event_bus
from agent_pro.config import build_model, get_settings
from agent_pro.observability import context, get_cost_handler, get_logger
from agent_pro.tools.env_tools import pip_install
from agent_pro.tools.workspace_files import make_workspace_tools
from agent_pro.tools.knowledge_tools import search_knowledge

log = get_logger("agent.coder")

@dataclass(frozen=True)
class TaskContext:
    """调度上下文: 子Agent据此绑定任务专属沙箱与共享状态"""
    task_id: str
    title: str
    workspace_dir: Path


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str  # 判据正文(三维量化)，被handoff描述引用
    build: Callable[[TaskContext | None], Runnable]


class AgentRegistry:
    def __init__(self,
                 context_resolver: Callable[[str], TaskContext | None] | None = None):
        self._specs: dict[str, AgentSpec] = {}
        self._resolve = context_resolver

    def register(self, spec: AgentSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Agent重复注册: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> AgentSpec:
        return self._specs[name]

    def all(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def as_handoff_tools(self) -> list:
        return [self._make_handoff_tool(spec) for spec in self._specs.values()]

    def _make_handoff_tool(self, spec: AgentSpec):
        tool_name = f"dispatch_to_{spec.name}"
        description = (
            f"将任务交给{spec.name}专家Agent执行并返回结果。\n"
            f"【何时调用——满足任一维度即调用】\n{spec.description}\n"
            f"【参数要求】task_description必须自包含(子Agent看不到当前对话历史，"
            f"需完整转述目标、约束与已知信息)；若已用create_task_plan创建计划书，"
            f"必须同时传入task_id。"
        )

        @tool(tool_name, description=description)
        def handoff(task_description: str, task_id: str | None = None) -> str:
            context = None
            if task_id:
                if not self._resolve:
                    return "错误: 当前环境不支持任务上下文"
                context = self._resolve(task_id)
                if context is None:
                    return f"错误: 任务不存在: {task_id}，请用read_task_progress核对task_id"
            agent = spec.build(context)
            limit = get_settings().agent_recursion_limit
            started = time.monotonic()
            topic = f"task:{task_id}" if task_id else None
            bus = get_event_bus()
            collected = []
            # v0.7: 透传当前 turn 的 cost handler，使子Agent token 累加进同一笔账
            cost_handler = get_cost_handler()
            callbacks = [cost_handler] if cost_handler is not None else None
            try:
                # v0.5: stream替代invoke——子Agent内部动作实时外推
                with context(agent="coder"):
                    for update in agent.stream(
                            {"messages": [{"role": "user", "content": task_description}]},
                            config={"recursion_limit": limit, "callbacks": callbacks},
                            stream_mode="updates"):
                        for payload in update.values():
                            chunk = (payload.get("messages", [])
                                     if isinstance(payload, dict) else [])
                            if not isinstance(chunk, list):
                                chunk = [chunk]
                            for m in chunk:
                                collected.append(m)
                                if topic:
                                    for tc in getattr(m, "tool_calls", None) or []:
                                        bus.publish(topic, "tool_call", {
                                            "name": tc.get("name"),
                                            "args_brief": str(tc.get("args"))[:120]})
                                    if getattr(m, "type", "") == "tool":
                                        bus.publish(topic, "tool_result", {
                                            "name": getattr(m, "name", ""),
                                            "brief": str(m.content)[:120]})
            except GraphRecursionError:
                return (f"错误: {spec.name}任务超过最大步数({limit})被中断。"
                        f"建议缩小任务范围或拆分为多段分别调度。")
            duration = time.monotonic() - started
            final_text = str(collected[-1].content) if collected else "(无输出)"
            tag = f"，任务{task_id}" if task_id else ""
            return f"[{spec.name}完成{tag}，耗时{duration:.0f}s]\n{final_text}"

        return handoff


# ============================================================
# coder 专家 Agent: 在工作区生成真实可运行代码文件
# ============================================================
CODER_SYSTEM_PROMPT = """你是 coder 专家Agent，负责在工作区生成真实可运行的代码文件。
工作规则：
1. 必须用 write_file 将代码落地为文件，禁止仅以文本返回代码；
   每次 write_file 必须写入完整内容（覆盖语义）。
2. 文件相对路径基于当前任务工作区；多文件项目自行组织目录结构。
3. 需要第三方依赖时用 pip_install 安装（包名+版本约束，多个用空格分隔）。
4. 交付时简要说明文件结构与运行方式；除非已真实运行验证，
   否则不要附"运行示例"输出，避免编造未验证的结果。
5. 任务描述已自包含（含目标、约束、已知信息），直接执行即可。

"""


def build_coder_agent(context: TaskContext | None = None) -> Runnable:
    settings = get_settings()
    root = context.workspace_dir if context else settings.workspace_dir
    tools = make_workspace_tools(root, include_write=True) + [pip_install]
    return create_agent(
        build_model(),
        tools=tools,
        system_prompt=CODER_SYSTEM_PROMPT,
    )


CODER_SPEC = AgentSpec(
    name="coder",
    description=(
        "代码生成任务：在工作区生成真实可运行的程序/应用代码文件"
        "（单文件或标准项目结构均可），可安装第三方依赖。"
        "判断标准不是任务难度，而是是否要产出可运行的代码文件。"
        "注意：纯解释/片段级代码问题不要调度，由通用Agent直接回答。"
    ),
    build=build_coder_agent,
)