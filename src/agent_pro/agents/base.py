# ============================================================
# agents/base.py | v0.2 诞生（多Agent调度接口 — 本版核心）
# 模式: Agent-as-Tool
#   子Agent注册为AgentSpec → Registry包装为dispatch工具 → 挂给通用Agent
#   新增子Agent = 写Spec + 注册一行，通用Agent零改动
# 约束: 子Agent不持有调度工具（两级扁平，杜绝调度死循环）
# ============================================================
from dataclasses import dataclass
from typing import Callable
from langchain_core.runnables import Runnable
from langchain.tools import tool


@dataclass(frozen=True)
class AgentSpec:
    """子Agent注册元数据：调度接口的契约

    Attributes:
        name:        唯一标识，如 "coder"
        description: 能力边界描述（含"什么不该调度给我"），
                     这是通用Agent做意图识别的唯一依据，必须精确
        build:       惰性构造工厂，首次调度时才实例化
    """
    name: str
    description: str
    build: Callable[[], Runnable]


class AgentRegistry:
    """Agent注册中心：登记、发现、包装为调度工具"""

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
        """把每个子Agent包装为通用Agent可调用的调度工具"""
        return [self._make_handoff_tool(spec) for spec in self._specs.values()]

    def _make_handoff_tool(self, spec: AgentSpec):
        tool_name = f"dispatch_to_{spec.name}"

        @tool(tool_name)
        def handoff(task_description: str) -> str:
            """调度复杂任务给专家Agent执行。

            适用场景见各子Agent的 description。
            Args:
                task_description: 自包含的任务描述。子Agent看不到当前对话
                    历史，必须完整转述用户需求，包括目标、约束、已知信息。
            """
            agent = spec.build()  # 惰性构造
            result = agent.invoke(
                {"messages": [{"role": "user", "content": task_description}]}
            )
            # 子Agent完整轨迹折叠为一条Observation返回——上下文经济性取舍
            return str(result["messages"][-1].content)

        return handoff
