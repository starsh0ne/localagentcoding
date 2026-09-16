# ============================================================
# api/worker.py | v0.5 诞生（串行worker + 轮次注册表）
# 并发模型(用户决策): chat可并发到达，排队串行执行。
# 串行的硬约束: ①SQLite单写者 ②pip装同一venv(并行coder互相污染依赖)
# 并行化前提: 多worker + 每任务独立venv(记为债务)
# ============================================================
import asyncio
import secrets
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime

from agent_pro.agents import create_app_agent
from agent_pro.api.bus import get_event_bus
from agent_pro.config import get_settings
from agent_pro.api.state_store import get_state_store
from agent_pro.memory.manager import SessionManager
from agent_pro.observability import (context, get_logger, reset_cost_handler,
                                     set_cost_handler, TokenUsageCallbackHandler)

log = get_logger("worker")


@dataclass
class Turn:
    turn_id: str
    session_id: str
    message: str
    status: str = "queued"  # queued/running/done/error
    result: str | None = None  # 内存态; 重启丢失→history接口兜底
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class TurnWorker:
    def __init__(self):
        self.queue: asyncio.Queue[Turn] = asyncio.Queue()
        self.turns: dict[str, Turn] = {}

    def submit(self, session_id: str, message: str) -> Turn:
        turn = Turn(
            turn_id=f"turn-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}",
            session_id=session_id, message=message)
        self.turns[turn.turn_id] = turn
        self.queue.put_nowait(turn)
        get_state_store().put_turn(asdict(turn))  # v0.9 持久化
        return turn

    def get(self, turn_id: str) -> Turn | None:
        t = self.turns.get(turn_id)
        if t is not None:
            return t
        d = get_state_store().get_turn(turn_id)  # v0.9 重启恢复
        return Turn(**d) if d else None

    async def run(self) -> None:
        while True:
            turn = await self.queue.get()
            turn.status = "running"
            get_state_store().update_turn(turn.turn_id, status="running")
            log.info("turn 开始", extra={"turn_id": turn.turn_id,
                                         "session_id": turn.session_id,
                                         "event_kind": "turn_start"})
            try:
                await asyncio.to_thread(self._execute, turn)
                turn.status = "done"
                get_state_store().update_turn(turn.turn_id, status="done",
                                             result=turn.result)
                log.info("turn 完成", extra={"turn_id": turn.turn_id,
                                             "session_id": turn.session_id,
                                             "event_kind": "turn_done",
                                             "status": "done"})
            except Exception as e:  # 兜底: worker永不退出
                turn.status = "error"
                turn.error = f"{type(e).__name__}: {e}"
                get_state_store().update_turn(turn.turn_id, status="error",
                                             error=turn.error)
                log.error("turn 异常", extra={"turn_id": turn.turn_id,
                                              "session_id": turn.session_id,
                                              "event_kind": "turn_error",
                                              "error": turn.error},
                          exc_info=e)
                get_event_bus().publish(f"turn:{turn.turn_id}", "error",
                                        {"error": turn.error})
            finally:
                self.queue.task_done()

    def _execute(self, turn: Turn) -> None:
        """同步执行一个轮次（工作线程内）: 记忆重建→agent流式→事件推送→压缩"""
        settings = get_settings()
        bus = get_event_bus()
        topic = f"turn:{turn.turn_id}"
        # v0.7: 本 turn 独立成本 handler，挂入上下文供子Agent(coder)透传累加
        cost = TokenUsageCallbackHandler(default_model=settings.model_name)
        cost_token = set_cost_handler(cost)
        started = time.monotonic()
        with context(session_id=turn.session_id, turn_id=turn.turn_id,
                     agent="general", model=settings.model_name):
            session = SessionManager(turn.session_id)
            session.append_user(turn.message)
            messages = session.build_messages()
            final = None
            for update in get_app_agent().stream(
                    {"messages": messages}, stream_mode="updates",
                    config={"callbacks": [cost]}):
                for payload in update.values():
                    chunk = payload.get("messages", []) if isinstance(payload, dict) else []
                    if not isinstance(chunk, list):
                        chunk = [chunk]
                    for m in chunk:
                        if getattr(m, "type", "") == "ai":
                            final = m
                        for tc in getattr(m, "tool_calls", None) or []:
                            name = tc.get("name", "")
                            bus.publish(topic, "tool_call", {
                                "name": name,
                                "args_brief": str(tc.get("args"))[:150]})
                            if name.startswith("dispatch_to_"):
                                log.info("调度决策", extra={
                                    "event_kind": "dispatch",
                                    "target": name,
                                    "task_brief": str(tc.get("args"))[:200]})
                        if getattr(m, "type", "") == "tool":
                            bus.publish(topic, "tool_result", {
                                "name": getattr(m, "name", ""),
                                "brief": str(m.content)[:150]})
        text = str(final.content) if final and getattr(final, "content", None) else "(无输出)"
        turn.result = text
        session.append_assistant(text)
        session.maybe_compact()
        duration = time.monotonic() - started
        # v0.7: 结构化成本汇总(供 grep/日志审计)
        summary = cost.summary(session_id=turn.session_id, turn_id=turn.turn_id)
        summary["duration_s"] = round(duration, 2)
        log.info("turn 成本汇总", extra={"event_kind": "cost_summary", **summary})
        bus.publish(topic, "final", {"content": text})
        reset_cost_handler(cost_token)


_agent_instance = None
_agent_lock = threading.Lock()


def get_app_agent():
    """应用级单例: agent无状态(messages传入)，构建一次全局复用"""
    global _agent_instance
    if _agent_instance is None:
        with _agent_lock:
            if _agent_instance is None:
                _agent_instance = create_app_agent()
    return _agent_instance


_worker: TurnWorker | None = None


def get_worker() -> TurnWorker:
    global _worker
    if _worker is None:
        _worker = TurnWorker()
    return _worker