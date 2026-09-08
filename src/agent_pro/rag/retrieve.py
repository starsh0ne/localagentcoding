"""检索与上下文拼接 (v0.5)

封装 retriever：按查询召回相关片段，并将结果格式化为可注入提示词的
上下文，供 agents.core.create_agent 在回答时引用（含来源标记，利于评估）。
"""

# TODO(v0.5): 实现检索
#   def build_retriever() -> BaseRetriever:  从 vectorstore 构建
#   def retrieve(query, k) -> list[Document]:  召回
#   def format_context(docs) -> str:  拼接为带来源标记的上下文字符串
