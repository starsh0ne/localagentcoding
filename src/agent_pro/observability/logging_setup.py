# ============================================================
# observability/logging_setup.py | v0.7 诞生（结构化 JSON 日志）
# 设计: stdlib-only，零新依赖; 上下文用 contextvars 自动注入;
#       双写 stdout(INFO) + logs/agent_pro.log(轮转, UTF-8)。
# ============================================================
import json
import logging
import logging.handlers
import sys
import threading
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# ---- 上下文(全链路自动带 session/turn/request/agent/model) ----
_ctx_session: ContextVar[Optional[str]] = ContextVar("obs_session", default=None)
_ctx_turn: ContextVar[Optional[str]] = ContextVar("obs_turn", default=None)
_ctx_request: ContextVar[Optional[str]] = ContextVar("obs_request", default=None)
_ctx_agent: ContextVar[Optional[str]] = ContextVar("obs_agent", default=None)
_ctx_model: ContextVar[Optional[str]] = ContextVar("obs_model", default=None)
# ---- 当前轮次的 cost handler(供子 Agent 透传累加) ----
_ctx_cost: ContextVar[object] = ContextVar("obs_cost", default=None)

_SERVICE = "agent-pro"


def set_context(session_id: str | None = None, turn_id: str | None = None,
                request_id: str | None = None, agent: str | None = None,
                model: str | None = None) -> list:
    """一次性设置上下文字段，返回 token 列表(供需要时 reset)。"""
    tokens = []
    if session_id is not None:
        tokens.append(_ctx_session.set(session_id))
    if turn_id is not None:
        tokens.append(_ctx_turn.set(turn_id))
    if request_id is not None:
        tokens.append(_ctx_request.set(request_id))
    if agent is not None:
        tokens.append(_ctx_agent.set(agent))
    if model is not None:
        tokens.append(_ctx_model.set(model))
    return tokens


def reset_context(tokens: list) -> None:
    for t in reversed(tokens):
        try:
            _ctx_session.reset(t)
        except (ValueError, LookupError):
            pass


class _Ctx:
    """上下文管理器: 进入时设置、退出时还原(支持嵌套与异步)。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.tokens: list = []

    def __enter__(self):
        self.tokens = set_context(**self.kwargs)
        return self

    def __exit__(self, *exc):
        reset_context(self.tokens)
        return False

    async def __aenter__(self):
        self.tokens = set_context(**self.kwargs)
        return self

    async def __aexit__(self, *exc):
        reset_context(self.tokens)
        return False


def context(**kwargs) -> _Ctx:
    """with observe.context(session_id=..., agent=...): 作用域内注入上下文。"""
    return _Ctx(**kwargs)


def set_cost_handler(handler) -> object:
    return _ctx_cost.set(handler)


def reset_cost_handler(token) -> None:
    try:
        _ctx_cost.reset(token)
    except (ValueError, LookupError):
        pass


def get_cost_handler():
    return _ctx_cost.get()


class JsonFormatter(logging.Formatter):
    """输出单行 JSON: ts/level/logger/service/event + 上下文 + 任意 extra。"""

    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "service": _SERVICE,
            "event": record.getMessage(),
        }
        # 上下文字段
        if (v := _ctx_session.get()) is not None:
            payload["session_id"] = v
        if (v := _ctx_turn.get()) is not None:
            payload["turn_id"] = v
        if (v := _ctx_request.get()) is not None:
            payload["request_id"] = v
        if (v := _ctx_agent.get()) is not None:
            payload["agent"] = v
        if (v := _ctx_model.get()) is not None:
            payload["model"] = v
        # 自定义 extra(排除 logging 标准字段)
        for key, val in record.__dict__.items():
            if key not in self._RESERVED and key not in payload:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = threading.Lock()
_configured_flag = False


def setup_logging(level: str = "INFO", log_dir: str | Path = "./logs") -> None:
    """配置根日志: 幂等(重复调用不叠加 handler)。"""
    global _configured_flag
    with _CONFIGURED:
        if _configured_flag:
            return
        _configured_flag = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = JsonFormatter()
    # 控制台
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.set_name("agent_pro_console")
    root.addHandler(sh)

    # 文件(轮转)
    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_path / "agent_pro.log", maxBytes=10 * 1024 * 1024,
            backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.set_name("agent_pro_file")
        root.addHandler(fh)
    except Exception as e:  # 日志初始化失败绝不拖垮主程序
        root.warning("日志文件初始化失败，仅控制台输出: %s", e)

    # 第三方库降噪，避免刷屏
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
