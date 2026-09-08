"""FastAPI 服务 (v0.7)

将 agent.core.create_agent 装配出的 Agent 通过 HTTP 暴露：
会话入口、流式回答、健康检查等，对接 config.API_HOST / API_PORT。
"""

# TODO(v0.7): 实现 FastAPI 应用
#   app = FastAPI()
#   @app.post("/chat")  -> 调用 create_agent() 产出 agent 处理请求
#   @app.get("/health")
#   启动：uvicorn agent_pro.api.server:app --host ... --port ...
