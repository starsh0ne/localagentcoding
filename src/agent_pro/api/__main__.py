# api/__main__.py | v0.5 诞生（启动入口: uv run python -m agent_pro.api）
import uvicorn
from agent_pro.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    # 注意: workers必须为1——队列与事件总线在进程内存中
    uvicorn.run("agent_pro.api.server:app", host=s.api_host, port=s.api_port)