"""会话管理 + 压缩 (v0.4)

维护多轮对话历史，按窗口大小裁剪或调用 LLM 对历史做摘要压缩，
控制上下文长度与成本，并为 RAG (v0.5) 与评估 (v0.6) 提供会话状态。
"""

# TODO(v0.4): 实现会话管理
#   class SessionMemory:
#     - append / get_history
#     - 滑动窗口裁剪（config.MEMORY_WINDOW_SIZE）
#     - 超长时 LLM 摘要压缩（summarize）
#   可对接 LangChain 的 ConversationBufferWindowMemory / 自定义。
