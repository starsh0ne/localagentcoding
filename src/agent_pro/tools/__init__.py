# ============================================================
# tools/__init__.py | v0.2诞生 → v0.3工具集按Agent分组导出
# 轻量注册中心: 新增工具 = 定义 → 加入对应工具集
# （完整权限元数据注册中心延至v0.8安全加固）
# ============================================================
from agent_pro.tools.builtin import calculator, get_current_time
from agent_pro.tools.env_tools import pip_install
from agent_pro.tools.workspace_files import make_workspace_tools

BUILTIN_TOOLS = [get_current_time, calculator]
__all__ = ["get_current_time", "calculator", "pip_install",
           "make_workspace_tools", "BUILTIN_TOOLS"]