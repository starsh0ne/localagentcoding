# ============================================================
# agents/base.py | 兼容垫片 (compat shim)
# v0.5 重构: TaskContext / AgentSpec / AgentRegistry 的定义与
#   v0.5 stream + 事件总线 handoff 已统一迁入 coder.py。
#   本文件仅做再导出，保持历史 import 不变
#   (tasks/store.py、general.py、agents/__init__.py)。
#   ★ 真正的单一实现见 coder.py，勿在此重复定义类。
# ============================================================
from agent_pro.agents.coder import AgentRegistry, AgentSpec, TaskContext  # noqa: F401

__all__ = ["AgentRegistry", "AgentSpec", "TaskContext"]
