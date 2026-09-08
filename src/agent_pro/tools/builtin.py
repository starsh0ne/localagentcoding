# ============================================================
# tools/builtin.py | v0.2 诞生（自v0.1的main.py迁入，逻辑不变）
# 已知债务: ① calculator的eval存在注入风险 → v0.3沙箱修复
# ============================================================
from datetime import datetime
from langchain.tools import tool


@tool
def get_current_time() -> str:
    """获取当前系统时间，格式 YYYY-MM-DD HH:MM:SS。用户询问时间/日期时使用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def calculator(expression: str) -> str:
    """计算数学表达式，如 '2**10 + 3*7'。仅支持纯数学运算。"""
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"计算失败: {type(e).__name__}: {e}"