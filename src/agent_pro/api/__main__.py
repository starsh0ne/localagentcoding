# api/__main__.py | v0.5诞生（启动入口: uv run python -m agent_pro.api） → v0.7(+日志)
import uvicorn
from agent_pro.config import get_settings
from agent_pro.observability import setup_logging


if __name__ == "__main__":
    s = get_settings()
    setup_logging(level=s.log_level, log_dir=s.log_dir)
    # 注意: workers必须为1——队列与事件总线在进程内存中
    uvicorn.run("agent_pro.api.server:app", host=s.api_host, port=s.api_port)
