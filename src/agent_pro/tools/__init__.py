# ============================================================
# tools/__init__.py | v0.2诞生 → v0.3工具集按Agent分组导出
# 轻量注册中心: 新增工具 = 定义 → 加入对应工具集
# （完整权限元数据注册中心延至v0.8安全加固）
# ============================================================
from agent_pro.tools.builtin import calculator, get_current_time
from agent_pro.tools.env_tools import pip_install
from agent_pro.tools.workspace_files import (
    delete_file,
    list_workspace,
    read_file,
    write_file,
)
# 通用Agent基础工具
BUILTIN_TOOLS = [get_current_time, calculator]
# Coder专家工具（工作区文件 + 依赖安装）
CODER_TOOLS = [list_workspace, read_file, write_file, delete_file, pip_install]
__all__ = [
    "get_current_time", "calculator", "pip_install",
    "list_workspace", "read_file", "write_file", "delete_file",
    "BUILTIN_TOOLS", "CODER_TOOLS",
]