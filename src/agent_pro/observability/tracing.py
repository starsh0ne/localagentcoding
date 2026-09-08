"""链路追踪与埋点 (v0.8)

统一采集 Agent 运行轨迹：LLM 调用、工具调用、RAG 检索、token 成本等，
对接 LangSmith (config.LANGCHAIN_TRACING_V2) 或 OTLP (config.OTLP_ENDPOINT)。
"""

# TODO(v0.8): 实现可观测性
#   - 配置 LangChain 回调 (LangSmithTracer / OpenTelemetryCallbackHandler)
#   - 定义采集点：agents 调用、tool 调用、retriever 调用
#   - 暴露 get_tracer() 供各模块在 create_agent 时挂载 callbacks
