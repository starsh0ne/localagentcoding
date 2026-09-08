"""配置集中管理 (v0.2)

基于 pydantic-settings 读取 .env，集中管理 LLM / 工具 / 记忆 / RAG / API /
可观测性等各版本模块的运行时配置，避免散落在代码中。
"""

# TODO(v0.2): 定义 Settings（pydantic-settings.BaseSettings），从 .env 加载：
#   - llm: model_name, base_url, api_key
#   - tools: enabled 列表
#   - memory: window_size
#   - rag: embedding_model, vector_store_path
#   - api: host, port
#   - observability: langchain_tracing_v2, otlp_endpoint
# 并提供全局 settings = Settings() 单例。
