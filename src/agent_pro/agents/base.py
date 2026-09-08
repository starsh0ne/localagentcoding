# ============================================================
# agents/base.py | v0.2诞生 → v0.3变更: 长任务三防
#   ① 步数上限（LangGraph默认25步，多文件任务必爆）
#   ② GraphRecursionError捕获 → 结构化错误，供通用Agent决策
#   ③ 耗时上报
# 模式不变: Agent-as-Tool，子Agent不持有调度工具
# ============================================================
import time
from dataclasses import dataclass
from typing import Callable
from langchain_core.runnables import Runnable
from langchain.tools import tool
from langgraph.errors import GraphRecursionError
from agent_pro.config import get_settings


@dataclass(frozen=True)
class AgentSpec:
    """子Agent注册元数据：name唯一标识 / description能力边界（正负边界都要写，
    是通用Agent意图识别的唯一依据）/ build惰性构造工厂"""
    name: str
    description: str
    build: Callable[[], Runnable]


class AgentRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, AgentSpec] = {}

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

        @tool(
            tool_name,
            description=(
                f"调度代码生成任务给 {spec.name} 专家Agent执行。\n"
                f"适用场景: {spec.description}\n"
                "Args:\n"
                "    task_description: 自包含的任务描述。子Agent看不到当前对话"
                "历史，必须完整转述用户需求，包括目标、约束、已知信息。"
            ),
        )
        def handoff(task_description: str) -> str:
            agent = spec.build()
            limit = get_settings().agent_recursion_limit
            started = time.monotonic()
            try:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": task_description}]},
                    config={"recursion_limit": limit},
                )
            except GraphRecursionError:
                return (
                    f"错误: {spec.name} 任务超过最大步数({limit})被中断。"
                    f"建议缩小任务范围后重新调度，或把任务拆成多段分别下达。"
                )
            duration = time.monotonic() - started
            return f"[{spec.name}完成，耗时{duration:.0f}s]\n{result['messages'][-1].content}"

        return handoff