# Changelog

## [0.1.0] - 最小 Agent
- 新增 `main.py` 最小可用 Agent（薄入口原型的起点）。

## [0.2.0] - 工程化骨架
- 新增 `pyproject.toml`、`.env.example`、`Makefile`，建立项目工程化基础。
- 新增 `src/agent_pro/config.py`：配置集中管理（pydantic-settings + .env）。
- 新增 `src/agent_pro/agent/core.py`：提供 `create_agent` 主装配入口；
  `main.py` 退役为薄入口。
- 新增 `src/agent_pro/agent/prompts.py`：系统提示词集中管理。

## [0.3.0] - 工具系统
- 新增 `src/agent_pro/tools/registry.py`：工具注册中心。
- 新增 `src/agent_pro/tools/errors.py`：工具调用错误分类处理。

## [0.4.0] - 会话记忆
- 新增 `src/agent_pro/memory/session.py`：会话管理与历史压缩。

## [0.5.0] - RAG
- 新增 `src/agent_pro/rag/ingest.py`：文档摄取与入库。
- 新增 `src/agent_pro/rag/retrieve.py`：检索与上下文拼接。

## [0.6.0] - 评估
- 新增 `src/agent_pro/eval/runner.py`：评估运行器。
- 新增 `tests/`：测试目录。

## [0.7.0] - API 服务
- 新增 `src/agent_pro/api/server.py`：FastAPI 服务暴露 Agent。

## [0.8.0] - 可观测性
- 新增 `src/agent_pro/observability/tracing.py`：链路追踪与埋点。
