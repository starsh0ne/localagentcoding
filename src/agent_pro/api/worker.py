# ============================================================
# api/worker.py | v0.5 诞生(串行worker) → v1.0(并发 worker 池 + 可插拔队列)
# 并发模型(v1.0): 单进程 N 个并发消费者协程, 每个 turn 在独立线程跑, 真正并行;
#   队列可插拔(Memory/Redis, 见 api/queue.py)。HITL/沙箱阻塞仅占 1/N 槽。
# 共享 venv: pip_install 已加全局锁防并发污染(真隔离留债务)。
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
from agent_pro.api.queue import QueueFullError, TurnQueue, get_turn_queue
from agent_pro.api.state_store import get_state_store
from agent_pro.memory.manager import SessionManager
from agent_pro.observability import (context, get_logger, reset_cost_handler,
                                     set_cost_handler, TokenUsageCallbackHandler)

log = get_logger("worker")


def client_safe_error(turn_id: str, e: Exception) -> str:
    """对外(客户端/SSE)安全的错误文案: 不泄露内部路径/堆栈/模块名等细节,
    仅留追踪码供服务端日志定位。详情已通过 exc_info 落日志。"""
    if isinstance(e, asyncio.TimeoutError):
        return f"turn 超时(>{get_settings().turn_timeout_s}s)"
    return f"任务执行失败（追踪码 {turn_id}，详情见服务端日志）"


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
        self.turns: dict[str, Turn] = {}
        self._turns_lock = threading.Lock()  # v1.0: turns 字典跨线程(提交/查询/回收)保护

    async def submit(self, session_id: str, message: str) -> Turn:
        turn = Turn(
            turn_id=f"turn-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}",
            session_id=session_id, message=message)
        with self._turns_lock:
            self.turns[turn.turn_id] = turn
        try:
            await get_turn_queue().put(asdict(turn))  # v1.0: 可插拔队列(内存/Redis)
        except Exception as e:  # 队列满(背压)等 → 转 429 由 server 捕获
            with self._turns_lock:
                self.turns.pop(turn.turn_id, None)
            raise e
        get_state_store().put_turn(asdict(turn))  # v0.9 持久化
        return turn

    def get(self, turn_id: str) -> Turn | None:
        with self._turns_lock:
            t = self.turns.get(turn_id)
        if t is not None:
            return t
        d = get_state_store().get_turn(turn_id)  # v0.9 重启恢复
        return Turn(**d) if d else None

    def _archive(self, turn_id: str) -> None:
        """完成后从内存剔除(历史改查 state_store), 防长服务内存泄漏。"""
        with self._turns_lock:
            self.turns.pop(turn_id, None)

    async def run(self, concurrency: int = 1) -> None:
        """v1.0: 起 N 个并发消费者协程, 每个 turn 在独立线程跑, 真正并行执行
        多 Agent 任务(此前为串行 1 个)。run 内部 gather 永不返回。"""
        n = max(1, concurrency)
        log.info("worker 启动", extra={"event_kind": "worker_start",
                                       "concurrency": n})
        consumers = [asyncio.create_task(self._consume(), name=f"worker-{i}")
                     for i in range(n)]
        await asyncio.gather(*consumers)

    async def _consume(self) -> None:
        """自愈外层: 消费者意外退出(如队列后端瞬时故障)后自动重启, 不丢整体服务。"""
        queue = get_turn_queue()
        while True:
            try:
                await self._consume_inner(queue)
            except Exception as e:
                log.error("消费者异常退出·1s 后重启", extra={
                    "event_kind": "worker_crash", "error": str(e)}, exc_info=e)
                await asyncio.sleep(1)

    async def _consume_inner(self, queue: TurnQueue) -> None:
        while True:
            try:
                d = await queue.get()
            except Exception as e:  # 队列后端瞬时故障(如 Redis 断连)
                log.error("队列读取异常·重试", extra={
                    "event_kind": "queue_get_error", "error": str(e)}, exc_info=e)
                await asyncio.sleep(1)
                continue
            try:
                turn = Turn(**d)
            except Exception:
                continue  # 畸形条目丢弃, 不卡消费者
            with self._turns_lock:
                self.turns.setdefault(turn.turn_id, turn)
            await self._run_one(turn)
            self._archive(turn.turn_id)  # 完成即回收内存

    async def _run_one(self, turn: Turn) -> None:
        turn.status = "running"
        try:  # 前置状态写失败不应击杀消费者(降级为日志告警)
            get_state_store().update_turn(turn.turn_id, status="running")
        except Exception as e:
            log.warning("状态更新失败(running)", extra={
                "turn_id": turn.turn_id, "error": str(e)}, exc_info=e)
        log.info("turn 开始", extra={"turn_id": turn.turn_id,
                                     "session_id": turn.session_id,
                                     "event_kind": "turn_start"})
        try:
            timeout = get_settings().turn_timeout_s
            await asyncio.wait_for(asyncio.to_thread(self._execute, turn),
                                  timeout=timeout)
            turn.status = "done"
            get_state_store().update_turn(turn.turn_id, status="done",
                                         result=turn.result)
            log.info("turn 完成", extra={"turn_id": turn.turn_id,
                                         "session_id": turn.session_id,
                                         "event_kind": "turn_done",
                                         "status": "done"})
        except asyncio.TimeoutError:
            turn.status = "error"
            turn.error = client_safe_error(turn.turn_id, asyncio.TimeoutError())
            get_state_store().update_turn(turn.turn_id, status="error",
                                         error=turn.error)
            log.error("turn 超时", extra={"turn_id": turn.turn_id,
                                          "event_kind": "turn_error",
                                          "error": turn.error})
            get_event_bus().publish(f"turn:{turn.turn_id}", "error",
                                    {"error": turn.error})
        except Exception as e:  # 兜底: 单个 turn 异常不退出消费者
            turn.status = "error"
            turn.error = client_safe_error(turn.turn_id, e)  # 对外脱敏
            get_state_store().update_turn(turn.turn_id, status="error",
                                         error=turn.error)
            log.error("turn 异常", extra={"turn_id": turn.turn_id,
                                          "session_id": turn.session_id,
                                          "event_kind": "turn_error",
                                          "error": str(e)},
                      exc_info=e)  # 原始异常仅落日志
            get_event_bus().publish(f"turn:{turn.turn_id}", "error",
                                    {"error": turn.error})

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