# ============================================================
# api/bus.py | v0.5 诞生（进程内事件总线: SSE推送+回放）
# 线程模型: worker线程publish → call_soon_threadsafe → loop线程投递
# 回放: 每话题保留最近500条 → 晚连接/重连不丢事件
# 防泄漏: TTL清理(30分钟无订阅且无新事件的话题回收)
# CLI模式: 未bind_loop时publish仅写回放缓冲，不崩溃
# ============================================================
import asyncio
import threading
import time
from collections import deque

REPLAY_SIZE = 500
TOPIC_TTL_S = 1800


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._replay: dict[str, deque] = {}
        self._last_pub: dict[str, float] = {}
        self._seq = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, topic: str, event_type: str, data: dict) -> None:
        with self._lock:  # 回放追加与订阅者快照同锁 → 无丢失窗口
            self._seq += 1
            evt = {"seq": self._seq, "type": event_type, "data": data}
            self._replay.setdefault(topic, deque(maxlen=REPLAY_SIZE)).append(evt)
            self._last_pub[topic] = time.time()
            subs = list(self._subs.get(topic, ()))
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._dispatch, subs, evt)

    def _dispatch(self, subs, evt) -> None:
        for q in subs:
            q.put_nowait(evt)

    def subscribe(self, topic: str, last_seq: int = 0, replay: bool = True):
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            history = ([e for e in self._replay.get(topic, ())
                        if e["seq"] > last_seq] if replay else [])
            self._subs.setdefault(topic, set()).add(q)
        return q, history

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(topic)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subs.pop(topic, None)

    def cleanup(self, max_age_s: int = TOPIC_TTL_S) -> None:
        now = time.time()
        with self._lock:
            stale = [t for t, ts in self._last_pub.items()
                     if now - ts > max_age_s and not self._subs.get(t)]
            for t in stale:
                self._replay.pop(t, None)
                self._last_pub.pop(t, None)


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus