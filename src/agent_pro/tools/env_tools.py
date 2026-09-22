# ============================================================
# env_tools.py | v0.3 诞生（受控依赖安装）
# 安全设计: ① subprocess列表参数(无shell注入面)
#           ② 包名正则白名单(拦截 -e / --index-url 等pip参数滥用)
#           ③ 数量上限 + 超时 + 输出截断
# 注意: 优先uv（uv创建的venv默认无pip模块）；uv run不会裁剪
#       非声明包，但手动 uv sync 会移除，重装即可
# ============================================================
import re
import shutil
import subprocess
import sys
import threading
from langchain.tools import tool

_SPEC_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?"  # 包名
    r"(\[[A-Za-z0-9,._-]+\])?"  # [extras]
    r"((==|>=|<=|!=|~=|>|<)[A-Za-z0-9.+*!,-]+)?$"  # 版本约束
)
PIP_TIMEOUT_S = 300
MAX_PKGS = 20
MAX_OUTPUT_CHARS = 2000
# v1.0: 共享 venv 下, 串行化安装以防火并行 coder 互相污染/损坏环境
# (真隔离留作 per-task venv 债务)
_PIP_LOCK = threading.Lock()


@tool
def pip_install(packages: str) -> str:
    """为当前Python环境安装第三方依赖。多个包用空格分隔，如 "requests flask>=3.0"。
    已安装的包会自动跳过；安装大包可能耗时数分钟。"""
    pkgs = packages.split()
    if not pkgs:
        return "错误: 未提供包名"
    if len(pkgs) > MAX_PKGS:
        return f"错误: 单次最多安装{MAX_PKGS}个包"
    bad = [p for p in pkgs if not _SPEC_RE.match(p)]
    if bad:
        return f"拒绝: 非法包规格(仅支持包名与版本约束，不允许pip参数): {bad}"
    cmd = (
        ["uv", "pip", "install", *pkgs]
        if shutil.which("uv")
        else [sys.executable, "-m", "pip", "install", *pkgs]
    )
    with _PIP_LOCK:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT_S
            )
            out = (proc.stdout + "\n" + proc.stderr).strip()
            if len(out) > MAX_OUTPUT_CHARS:
                out = "…" + out[-MAX_OUTPUT_CHARS:]
            return f"exit_code={proc.returncode}\n{out}"
        except subprocess.TimeoutExpired:
            return f"错误: 安装超时(>{PIP_TIMEOUT_S}s)，可重试或检查包名/网络"
        except OSError as e:
            return f"错误: 无法执行安装命令: {type(e).__name__}: {e}"