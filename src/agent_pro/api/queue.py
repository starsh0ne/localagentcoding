# ============================================================
# api/queue.py | v1.0 诞生（可插拔 turn 队列: Memory / Redis 双后端）
# 作用: 解耦"提交"与"消费"。v0.9 前是无界 asyncio.Queue(进程内存);
#       v1.0 支持 Redis 后端(重启可恢复、可跨进程), 无 Redis 时降级内存。
# 契约: put/get 均以 dict 传输(worker 负责 Turn<->dict 转换)。
# ============================================================
import asyncio
import json
import threading
from typing import Any, Optional

from agent_pro.config import get_settings
from agent_pro.observability import get_logger

log = get_logger("queue")

QUEUE_KEY = "agent_pro:turn_queue"


class QueueFullError(Exception):
    """队列已达上限(背压): 提交方应据此返回 429。"""


class TurnQueue:
    """turn 队列抽象。put/get 传 dict。"""

    async def put(self, item: dict) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    async def get(self) -> dict:  # pragma: no cover - 接口
        raise NotImplementedError


class MemoryTurnQueue(TurnQueue):
    """进程内 asyncio.Queue（可设上限做背压）。重启即丢。"""

    def __init__(self, maxsize: int = 0) -> None:
        self._q: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: dict) -> None:
        try:
            self._q.put_nowait(item)
        except asyncio.QueueFull:
            raise QueueFullError("队列已满，请稍后重试")

    async def get(self) -> dict:
        return await self._q.get()


class RedisTurnQueue(TurnQueue):
    """Redis list 队列: put=rpush, get=brpop(阻塞, 线程内)。重启可恢复。"""

    def __init__(self, redis_client: Any, key: str = QUEUE_KEY,
                 maxsize: int = 0) -> None:
        self._r = redis_client
        self._key = key
        self._maxsize = maxsize

    async def put(self, item: dict) -> None:
        payload = json.dumps(item, ensure_ascii=False)
        if self._maxsize > 0:
            # 长度检查在线程内做, 与 rpush 之间有竞态但仅用于背压(非精确)
            cur = await asyncio.to_thread(self._r.llen, self._key)
            if cur >= self._maxsize:
                raise QueueFullError("队列已满，请稍后重试")
        await asyncio.to_thread(self._r.rpush, self._key, payload)

    async def get(self) -> dict:
        # brpop 阻塞; 在线程内跑, 不占事件循环 (N 个消费者 = N 个线程等待)
        raw = await asyncio.to_thread(self._r.brpop, self._key, 0)
        if raw is None:  # 超时(0=永不超时, 理论不触发); 防御性返回
            return await self.get()
        _key, value = raw
        return json.loads(value)


def _build_redis() -> Optional[Any]:
    try:
        import redis  # 软依赖
        s = get_settings()
        url = s.redis_url or "redis://localhost:6379"
        r = redis.Redis.from_url(
            url, socket_connect_timeout=1.5, socket_timeout=1.5,
            decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        log.warning("队列后端: Redis 不可用·降级内存队列", extra={
            "event_kind": "turn_queue", "backend": "memory", "error": str(e)})
        return None


_queue: Optional[TurnQueue] = None
_qlock = threading.Lock()


def get_turn_queue() -> TurnQueue:
    """单例。queue_backend: auto(默认, Redis 可用则用) / redis / memory。"""
    global _queue
    if _queue is not None:
        return _queue
    with _qlock:
        if _queue is not None:
            return _queue
        s = get_settings()
        backend = (s.queue_backend or "auto").lower()
        maxsize = max(0, s.queue_maxsize)
        if backend == "memory":
            _queue = MemoryTurnQueue(maxsize=maxsize)
            log.info("队列后端: 内存(asyncio.Queue)", extra={
                "event_kind": "turn_queue", "backend": "memory", "maxsize": maxsize})
            return _queue
        # auto / redis 都尝试 Redis
        r = _build_redis() if backend in ("auto", "redis") else None
        if r is not None:
            _queue = RedisTurnQueue(r, maxsize=maxsize)
            log.info("队列后端: Redis(持久化+重启可恢复)", extra={
                "event_kind": "turn_queue", "backend": "redis", "key": QUEUE_KEY})
        else:
            if backend == "redis":
                log.warning("queue_backend=redis 但 Redis 不可用, 退回内存",
                            extra={"event_kind": "turn_queue"})
            _queue = MemoryTurnQueue()
        return _queue
