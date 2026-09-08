# ============================================================
# memory_tools.py | v0.4 诞生（长期记忆检索工具 — 通用agent按需调用）
# ============================================================
from langchain.tools import tool
from agent_pro.memory.store import get_memory_db


@tool
def search_long_term_memory(query: str) -> str:
    """检索跨会话长期记忆（用户偏好/事实/实体关系）。当对话涉及过往内容、
    用户背景、之前的项目与决定，而当前上下文缺少这些信息时使用。"""
    results = get_memory_db().search_memories(query, limit=5)
    if not results:
        return "未找到相关长期记忆"
    return "\n".join(f"[{r['kind']}] {r['content']}" for r in results)