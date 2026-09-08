# ============================================================
# tools/builtin.py | v0.2诞生 → v0.3变更: calculator安全重写
# 债务清偿: v0.1的eval注入风险 → AST白名单求值，只放行
# 数字常量/四则运算/幂/一元正负号，幂指数设上限防资源耗尽
# ============================================================
import ast
import operator
from datetime import datetime
from langchain.tools import tool


@tool
def get_current_time() -> str:
    """获取当前系统时间，格式 YYYY-MM-DD HH:MM:SS。用户询问时间/日期时使用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
MAX_EXPONENT = 10_000


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        if isinstance(node.op, ast.Pow):
            exp = _eval_node(node.right)
            if isinstance(exp, (int, float)) and abs(exp) > MAX_EXPONENT:
                raise ValueError(f"指数绝对值超过{MAX_EXPONENT}，拒绝计算(防资源耗尽)")
        return _ALLOWED_BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"不支持的表达式元素: {type(node).__name__}(仅支持四则运算与幂)")


@tool
def calculator(expression: str) -> str:
    """计算纯数学表达式，如 '2**10 + 3*7'。支持 + - * / // % ** 和括号。"""
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval_node(tree.body))
    except (ValueError, SyntaxError, ZeroDivisionError,
            OverflowError, RecursionError) as e:
        return f"计算失败: {type(e).__name__}: {e}"