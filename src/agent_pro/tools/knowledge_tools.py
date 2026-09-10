# ============================================================
# tools/knowledge_tools.py | v0.6 诞生
# ★铁律: 何时检索的判据全部写在本工具描述中——
#   模型(通用agent与coder)是否调用只看这里
# ============================================================
from langchain.tools import tool
from agent_pro.kb.store import get_kb_store


@tool
def search_knowledge(query: str) -> str:
    """检索用户的私有知识库(Markdown笔记、txt文档等)，返回带来源
    (文件 › 标题路径)的相关原文片段。
    何时调用:
    1. 问题涉及用户个人/组织的私有文档知识——个人笔记、项目背景、
       积累的经验总结，而非通用常识
    2. 实现任务前，需要查询用户成文的规范、标准、约定(如编码规范、
       依赖管理约定、项目约定)并遵照执行
    何时不要调用: 通用常识问题；当前对话已明确的内容；
    实时信息(知识库是导入时的静态快照)。"""
    results = get_kb_store().search(query, limit=5)
    if not results:
        return "知识库中未找到相关内容"
    return "\n\n".join(
        f"[{i}] 来源: {r['source']} › {r['heading_path']}\n{r['content']}"
        for i, r in enumerate(results, 1))