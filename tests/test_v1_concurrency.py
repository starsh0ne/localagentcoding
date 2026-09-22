# tests/test_v1_concurrency.py | v1.0 并发改造验证
# 注意: 必须在导入 agent_pro 之前设好 DATA_DIR 等(Settings 为 lru_cache)。
import asyncio
import os
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="agentpro_test_")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("LOG_DIR", _TMP)
os.environ.setdefault("WORKSPACE_DIR", _TMP)
os.environ.setdefault("REDIS_URL", "")  # 强制内存后端, 避免依赖外部 Redis

from agent_pro.api import worker as wmod
from agent_pro.api.worker import TurnWorker
from agent_pro.api.queue import MemoryTurnQueue, get_turn_queue
from agent_pro.memory.store import get_memory_db


def test_queue_memory_order():
    q = MemoryTurnQueue()
    a, b = {"x": 1}, {"x": 2}

    async def go():
        await q.put(a)
        await q.put(b)
        return await q.get(), await q.get()

    first, second = asyncio.run(go())
    assert first is a and second is b


def test_worker_pool_parallel():
    events = []

    def fake_execute(self, turn):
        s = time.monotonic()
        events.append(("start", s))
        time.sleep(0.3)
        events.append(("end", time.monotonic()))
        turn.result = "ok"

    wmod.TurnWorker._execute = fake_execute

    async def go():
        w = TurnWorker()
        assert isinstance(get_turn_queue(), MemoryTurnQueue)
        t = asyncio.create_task(w.run(concurrency=3))
        for i in range(3):
            await w.submit(f"s{i}", f"m{i}")
        await asyncio.sleep(1.3)
        t.cancel()

    asyncio.run(go())
    starts = [ts for k, ts in events if k == "start"]
    ends = [ts for k, ts in events if k == "end"]
    assert len(starts) == 3 and len(ends) == 3
    concurrent = sum(1 for ts in starts if ts < min(ends))
    assert concurrent >= 2, f"worker 池未并行: concurrent={concurrent}"


def test_memory_store_per_thread_conn():
    """v1.0: 每线程独立连接, 多线程并发读写不报错(连接复用改造)。"""
    db = get_memory_db()

    def worker_thread(i):
        sid = f"t-session-{i}"
        db.ensure_session(sid)
        db.append_user(sid, f"hello {i}")
        db.append_assistant(sid, f"world {i}")
        rows = db.active_messages(sid)
        assert any(r["role"] == "user" for r in rows)

    threads = [__import__("threading").Thread(target=worker_thread, args=(i,))
               for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
