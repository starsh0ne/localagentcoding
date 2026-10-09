"""eval 单条超时与 contextvars 跨线程的回归测试（不联网、不调 LLM）。"""
import contextvars
import time
import types

import pytest

from agent_pro.eval.runner import _invoke_with_timeout

_MARKER = contextvars.ContextVar("eval_marker", default="none")
_SETTINGS = types.SimpleNamespace(agent_recursion_limit=10)


class _FakeAgent:
    """最小 agent 替身: 可选 sleep 模拟卡住, 并记录线程内看到的 contextvar。"""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.seen_marker: str | None = None

    def invoke(self, payload, config=None):
        if self.delay:
            time.sleep(self.delay)
        self.seen_marker = _MARKER.get()
        return {"messages": []}


def test_invoke_times_out_and_returns_fast():
    """单条卡死时抛 TimeoutError, 且不会被整个 sleep 拖住。"""
    agent = _FakeAgent(delay=5)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _invoke_with_timeout(agent, "q", _SETTINGS, None, timeout_s=1)
    elapsed = time.monotonic() - started
    # 软超时应在 ~1s 返回, 而不是等满 5s
    assert elapsed < 3, f"超时未及时返回, 耗时 {elapsed:.1f}s"


def test_contextvars_propagate_into_thread():
    """contextvars 必须显式 copy_context 带入新线程, 否则日志上下文(agent/model)丢失。"""
    agent = _FakeAgent()
    token = _MARKER.set("ctx-ok")
    try:
        _invoke_with_timeout(agent, "q", _SETTINGS, None, timeout_s=5)
    finally:
        _MARKER.reset(token)
    assert agent.seen_marker == "ctx-ok", (
        f"线程内未读到 contextvar: {agent.seen_marker!r}")
