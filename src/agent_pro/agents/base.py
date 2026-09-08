# ============================================================
# agents/base.py | v0.2诞生 → v0.3(+三防) → v0.4
# ★★缺陷修复(用户实测发现, 记入铁律):
#   1. 动态工具描述必须走 @tool(description=...) 参数。
#      f-string docstring在Python中不是docstring(必须是字符串字面量)，
#      __doc__为None → 工具描述为空 → 模型无法判断是否调度。
#   2. 调度判据唯一来源=工具描述(模型选工具只看描述)，
#      spec.description即判据正文，由handoff的description=引用。
# 演进: +TaskContext(任务子目录沙箱绑定) +handoff带task_id参数
# ============================================================
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from langchain_core.runnables import Runnable
from langchain.tools import tool
from langgraph.errors import GraphRecursionError
from agent_pro.config import get_settings


@dataclass(frozen=True)
class TaskContext:
    """调度上下文: 子Agent据此绑定任务专属沙箱与共享状态"""
    task_id: str
    title: str
    workspace_dir: Path


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str  # 判据正文(多维量化, 正负边界), 被handoff描述引用
    build: Callable[[TaskContext | None], Runnable]  # v0.4: 接受任务上下文


class AgentRegistry:
    def __init__(self, context_resolver: Callable[[str], TaskContext | None] | None = None):
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
        # ★铁律落地: 动态描述走description=参数，绝不写f-string docstring
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
            try:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": task_description}]},
                    config={"recursion_limit": limit})
            except GraphRecursionError:
                return (f"错误: {spec.name}任务超过最大步数({limit})被中断。"
                        f"建议缩小任务范围或拆分为多段分别调度。")
            duration = time.monotonic() - started
            tag = f"，任务{task_id}" if task_id else ""
            return f"[{spec.name}完成{tag}，耗时{duration:.0f}s]\n{result['messages'][-1].content}"

        return handoff