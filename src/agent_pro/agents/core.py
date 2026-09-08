"""Agent 核心装配 (v0.2)

提供 create_agent 作为主装配入口，将 LLM、提示词 (prompts.py)、
工具 (tools/registry.py, v0.3)、记忆 (memory/session.py, v0.4)、
RAG (rag/retrieve.py, v0.5) 等按配置组装为一个可执行 Agent。

基于 LangChain：可用 langchain.agents 或 LCEL Runnable 组合。
"""

# TODO(v0.2): 实现 create_agent(settings) -> Runnable/ AgentExecutor
#   - 加载 LLM（langchain_openai.ChatOpenAI）
#   - 加载系统提示词（agents.prompts）
#   - 注册工具（tools.registry, v0.3）
#   - 挂载记忆（memory.session, v0.4）
#   - 注入 RAG retriever（rag.retrieve, v0.5）
# TODO(v0.7): 暴露为可被 api/server.py 调用的 callable。
