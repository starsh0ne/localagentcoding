# sandbox 包 | v0.8 诞生（代码执行隔离）
# 当前唯一隔离后端: WSL2 子环境(见 docs/plans/v0.8-execution-plan.md)。
# 强隔离(Docker/nsjail)留后续版本。
from agent_pro.sandbox.wsl_exec import (  # noqa: F401
    WSL_AVAILABLE,
    SandboxResult,
    run_in_wsl,
    wsl_available,
)

__all__ = ["run_in_wsl", "SandboxResult", "wsl_available", "WSL_AVAILABLE"]
