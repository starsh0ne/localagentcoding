# ============================================================
# agents/__init__.py | v0.2 诞生（组装根）
# 新增子Agent两步: 写Spec → 在此注册一行
# ============================================================
from agent_pro.agents.base import AgentRegistry, AgentSpec  # noqa: F401
from agent_pro.agents.coder import CODER_SPEC
from agent_pro.agents.general import build_general_agent


def create_app_agent():
    """组装入口：注册中心 + 通用Agent"""
    registry = AgentRegistry()
    registry.register(CODER_SPEC)
    # 未来扩展示例（两行接入一个新专家）:
    # from agent_pro.agents.search import SEARCH_SPEC
    # registry.register(SEARCH_SPEC)
    return build_general_agent(registry)
