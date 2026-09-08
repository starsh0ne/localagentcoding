# ============================================================
# api/worker.py | v0.5 诞生（串行worker + 轮次注册表）
# 并发模型(用户决策): chat可并发到达，排队串行执行。
# 串行的硬约束: ①SQLite单写者 ②pip装同一venv(并行coder互相污染依赖)
# 并行化前提: 多worker + 每任务独立venv(记为债务)
# ============================================================
import asyncio
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from agent_pro.agents import create_app_agent
from agent_pro.api.bus import get_event_bus
from agent_pro.memory.manager import SessionManager


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
        return turn

    def get(self, turn_id: str) -> Turn | None:
        return self.turns.get(turn_id)

    async def run(self) -> None:
        while True:
            turn = await self.queue.get()
            turn.status = "running"
            try:
                await asyncio.to_thread(self._execute, turn)
                turn.status = "done"
            except Exception as e:  # 兜底: worker永不退出
                turn.status = "error"
                turn.error = f"{type(e).__name__}: {e}"
                get_event_bus().publish(f"turn:{turn.turn_id}", "error",
                                        {"error": turn.error})
            finally:
                self.queue.task_done()

    def _execute(self, turn: Turn) -> None:
        """同步执行一个轮次（工作线程内）: 记忆重建→agent流式→事件推送→压缩"""
        bus = get_event_bus()
        topic = f"turn:{turn.turn_id}"
        session = SessionManager(turn.session_id)
        session.append_user(turn.message)
        messages = session.build_messages()
        final = None
        for update in get_app_agent().stream({"messages": messages},
                                             stream_mode="updates"):
            for payload in update.values():
                chunk = payload.get("messages", []) if isinstance(payload, dict) else []
                if not isinstance(chunk, list):
                    chunk = [chunk]
                for m in chunk:
                    if getattr(m, "type", "") == "ai":
                        final = m
                    for tc in getattr(m, "tool_calls", None) or []:
                        bus.publish(topic, "tool_call", {
                            "name": tc.get("name"),
                            "args_brief": str(tc.get("args"))[:150]})
                    if getattr(m, "type", "") == "tool":
                        bus.publish(topic, "tool_result", {
                            "name": getattr(m, "name", ""),
                            "brief": str(m.content)[:150]})
        text = str(final.content) if final and getattr(final, "content", None) else "(无输出)"
        turn.result = text
        session.append_assistant(text)
        session.maybe_compact()
        bus.publish(topic, "final", {"content": text})


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