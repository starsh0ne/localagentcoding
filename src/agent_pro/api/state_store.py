# ============================================================
# api/state_store.py | v0.9 诞生（状态持久化后端: Redis 优先 + 内存降级）
# 作用: turn 注册表 / 审批元数据 落 Redis, 进程重启可恢复;
#       Redis 不可用时降级进程内内存(dict), 不阻断启动(history 兜底)。
# ============================================================
import json
import threading
from typing import Any, Optional

from agent_pro.observability import get_logger

log = get_logger("state")

DEFAULT_REDIS_URL = "redis://localhost:6379"
TURN_TTL_S = 60 * 60 * 24 * 7        # turn 留存 7 天
APPROVAL_TTL_S = 60 * 60             # 审批占位/元数据留存 1 小时


class StateStore:
    """统一状态后端。Redis 可用时持久化, 否则降级内存。线程安全。"""

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = url or DEFAULT_REDIS_URL
        self._redis = None
        self.available = False
        self._lock = threading.Lock()
        self._mem: dict[str, dict] = {}
        self._connect()

    def _connect(self) -> None:
        try:
            import redis  # 软依赖: 未装则降级, 不阻断启动
            r = redis.Redis.from_url(
                self.url, socket_connect_timeout=1.5,
                socket_timeout=1.5, decode_responses=True)
            r.ping()
            self._redis = r
            self.available = True
            log.info("状态后端: Redis 可用·持久化开启", extra={
                "event_kind": "state_store", "backend": "redis", "url": self.url})
        except Exception as e:
            self.available = False
            log.warning("状态后端: Redis 不可用·降级内存态(重启将丢)",
                        extra={"event_kind": "state_store", "backend": "memory",
                               "error": str(e)})

    # ---------------- Turn 持久化 ----------------
    def put_turn(self, turn: dict) -> None:
        self._write(f"turn:{turn['turn_id']}", turn, TURN_TTL_S)

    def get_turn(self, turn_id: str) -> Optional[dict]:
        return self._read(f"turn:{turn_id}")

    def update_turn(self, turn_id: str, **fields) -> None:
        key = f"turn:{turn_id}"
        cur = self._read(key) or {}
        cur.update(fields)
        self._write(key, cur, TURN_TTL_S)

    # ---------------- Approval 持久化(元数据 + 原子防双批) ----------------
    def set_approval_meta(self, turn_id: str, meta: dict) -> None:
        self._write(f"approval:{turn_id}", meta, APPROVAL_TTL_S)

    def update_approval_meta(self, turn_id: str, **fields) -> None:
        key = f"approval:{turn_id}"
        cur = self._read(key) or {}
        cur.update(fields)
        self._write(key, cur, APPROVAL_TTL_S)

    def claim_approval(self, turn_id: str) -> bool:
        """原子占位防双批: Redis SET NX; 降级用内存占位。成功返回 True。"""
        if self._redis:
            try:
                return bool(self._redis.set(
                    f"approval:{turn_id}:claimed", "1", nx=True,
                    ex=APPROVAL_TTL_S))
            except Exception as e:
                log.warning("approval claim Redis 失败·降级内存",
                            extra={"error": str(e)})
        with self._lock:
            k = f"approval:{turn_id}:claimed"
            if k in self._mem:
                return False
            self._mem[k] = 1
            return True

    def clear_approval(self, turn_id: str) -> None:
        if self._redis:
            try:
                self._redis.delete(f"approval:{turn_id}")
                self._redis.delete(f"approval:{turn_id}:claimed")
            except Exception:
                pass
        with self._lock:
            self._mem.pop(f"approval:{turn_id}", None)
            self._mem.pop(f"approval:{turn_id}:claimed", None)

    # ---------------- 底层读写 ----------------
    def _write(self, key: str, value: dict, ttl: int) -> None:
        if self._redis:
            try:
                self._redis.hset(key, mapping={
                    k: json.dumps(v, ensure_ascii=False) for k, v in value.items()})
                self._redis.expire(key, ttl)
                return
            except Exception as e:
                log.warning("状态写 Redis 失败·降级内存", extra={"error": str(e)})
        with self._lock:
            self._mem[key] = dict(value)

    def _read(self, key: str) -> Optional[dict]:
        if self._redis:
            try:
                raw = self._redis.hgetall(key)
                if raw:
                    return {k: json.loads(v) for k, v in raw.items()}
            except Exception:
                pass
        with self._lock:
            return self._mem.get(key)


_store: Optional["StateStore"] = None


def get_state_store() -> StateStore:
    global _store
    if _store is None:
        from agent_pro.config import get_settings
        _store = StateStore(get_settings().redis_url or None)
    return _store
