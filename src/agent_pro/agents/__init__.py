# ============================================================
# agents/__init__.py | v0.2诞生 → v0.4(接线任务系统+只读验收工具)
# ============================================================
from agent_pro.agents.base import AgentRegistry, AgentSpec, TaskContext  # noqa: F401
from agent_pro.agents.coder import CODER_SPEC
from agent_pro.agents.general import build_general_agent
from agent_pro.config import get_settings
from agent_pro.tasks.store import get_task_store
from agent_pro.tools.workspace_files import make_workspace_tools


def create_app_agent():
    settings = get_settings()
    task_store = get_task_store()
    registry = AgentRegistry(context_resolver=task_store.resolve_task_context)
    registry.register(CODER_SPEC)
    # 通用agent: 全局工作区只读工具(验收用, 无写入/删除/安装权)
    readonly_tools = make_workspace_tools(settings.workspace_dir, include_write=False)
    return build_general_agent(registry, extra_tools=readonly_tools)