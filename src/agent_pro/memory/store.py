# ============================================================
# memory/store.py | v0.4诞生 → v0.5变更(线程安全)
#   +check_same_thread=False +RLock
#   +embedding网络调用移出锁外(防SSE读阻塞)
# v1.1变更(记忆系统完善):
#   - 作用域隔离: sessions/long_term_memories 加 user_id/project_id/agent_id,
#     所有长期记忆读写必须带作用域条件; source_session 仅作溯源不参与隔离
#   - 检索: 由「有向量只用向量/否则只用FTS」的互斥降级 → 对齐 kb/store.py 的
#     向量top20 + FTS5 top20 → RRF(k=60)融合(顺带修掉 _cosine NameError)
#   - 有效期: expires_at + purge_expired(), 检索时顺带清理, 过期条目不召回
# ============================================================
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from agent_pro.config import get_settings
from agent_pro.embeddings import cosine, get_embedder, safe_embed_query


KINDS = ("preference", "fact", "entity")
CANDIDATES = 20  # 单路候选条数(与 kb/store.py 一致)
SCOPE_DEFAULT = "default"
# 有效期过滤条件(过期条目一律不召回); LIVE_M 为带表别名版本(FTS JOIN 用)
LIVE = ("user_id=? AND project_id=? AND agent_id=?"
        " AND (expires_at IS NULL OR expires_at>?)")
LIVE_M = ("m.user_id=? AND m.project_id=? AND m.agent_id=?"
          " AND (m.expires_at IS NULL OR m.expires_at>?)")


class Scope(NamedTuple):
    """记忆隔离作用域(v1.1): user + project + agent 三层, 默认全 'default'。

    NamedTuple 便于直接展开进 SQL 参数元组: (..., *scope, ...)
    """
    user_id: str = SCOPE_DEFAULT
    project_id: str = SCOPE_DEFAULT
    agent_id: str = SCOPE_DEFAULT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_after(ttl_s: int) -> str:
    """now + ttl_s 的 ISO 串(与 _now() 同格式, 可直接字典序比较)。"""
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


class MemoryDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()  # v1.0: 每线程持有一个连接(连接复用)

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, round_no INTEGER NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_msg ON messages(session_id, round_no);
        CREATE TABLE IF NOT EXISTS long_term_memories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, content TEXT NOT NULL,
            embedding TEXT, source_session TEXT, created_at TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT NOT NULL DEFAULT 'default',
            agent_id TEXT NOT NULL DEFAULT 'default',
            expires_at TEXT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, memory_id UNINDEXED);
        """)
        # 旧库迁移: 三列 NOT NULL DEFAULT 'default' → 历史数据自动落到 default 作用域
        _add_column_if_missing(conn, "sessions", "user_id",
                               "user_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "sessions", "project_id",
                               "project_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "sessions", "agent_id",
                               "agent_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "long_term_memories", "user_id",
                               "user_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "long_term_memories", "project_id",
                               "project_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "long_term_memories", "agent_id",
                               "agent_id TEXT NOT NULL DEFAULT 'default'")
        _add_column_if_missing(conn, "long_term_memories", "expires_at",
                               "expires_at TEXT NULL")
        # 索引必须在迁移之后建: 旧库里这些列还不存在
        conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_ltm_scope
            ON long_term_memories(user_id, project_id, agent_id, kind);
        CREATE INDEX IF NOT EXISTS idx_session_scope
            ON sessions(user_id, project_id, agent_id);
        """)
        conn.commit()
        return conn

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._new_conn()
            self._local.conn = c
        return c

    # ---------- 会话与消息（每方法持锁，快速进出） ----------
    def ensure_session(self, sid: str, scope: Scope | None = None) -> bool:
        """确保会话存在; 返回是否「本次新建」(v0.9 供惰性创建日志使用)。

        v1.1: 新建时写入作用域; 已存在则不动(会话作用域以创建时为准)。
        """
        scope = scope or Scope()
        with self._lock:
            exists = self._conn().execute(
                "SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None
            if not exists:
                self._conn().execute(
                    "INSERT INTO sessions(id, user_id, project_id, agent_id,"
                    " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                    (sid, *scope, _now(), _now()))
                self._conn().commit()
            return not exists

    def get_scope(self, sid: str) -> Scope | None:
        """读会话创建时的作用域; 会话不存在返回 None。"""
        with self._lock:
            row = self._conn().execute(
                "SELECT user_id, project_id, agent_id FROM sessions WHERE id=?",
                (sid,)).fetchone()
            return Scope(row["user_id"], row["project_id"],
                         row["agent_id"]) if row else None

    def get_summary(self, sid: str) -> str:
        with self._lock:
            row = self._conn().execute(
                "SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
            return row["summary"] if row else ""

    def set_summary(self, sid: str, summary: str) -> None:
        with self._lock:
            self._conn().execute(
                "UPDATE sessions SET summary=?, updated_at=? WHERE id=?",
                (summary, _now(), sid))
            self._conn().commit()

    def append_user(self, sid: str, content: str) -> None:
        with self._lock:
            r = self._conn().execute(
                "SELECT COALESCE(MAX(round_no),0)+1 AS r FROM messages"
                " WHERE session_id=?", (sid,)).fetchone()["r"]
            self._conn().execute(
                "INSERT INTO messages(session_id, round_no, role, content,"
                " active, created_at) VALUES(?,?,?,?,1,?)",
                (sid, r, "user", content, _now()))
            self._conn().commit()

    def append_assistant(self, sid: str, content: str) -> None:
        with self._lock:
            r = self._conn().execute(
                "SELECT COALESCE(MAX(round_no),1) AS r FROM messages"
                " WHERE session_id=?", (sid,)).fetchone()["r"]
            self._conn().execute(
                "INSERT INTO messages(session_id, round_no, role, content,"
                " active, created_at) VALUES(?,?,?,?,1,?)",
                (sid, r, "assistant", content, _now()))
            self._conn().commit()

    def active_messages(self, sid: str) -> list[dict]:
        with self._lock:
            rows = self._conn().execute(
                "SELECT role, content FROM messages WHERE session_id=?"
                " AND active=1 ORDER BY id", (sid,)).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def max_active_round(self, sid: str) -> int:
        with self._lock:
            return self._conn().execute(
                "SELECT COALESCE(MAX(round_no),0) AS m FROM messages"
                " WHERE session_id=? AND active=1", (sid,)).fetchone()["m"]

    def messages_upto(self, sid: str, cutoff: int) -> list[dict]:
        with self._lock:
            rows = self._conn().execute(
                "SELECT role, content FROM messages WHERE session_id=?"
                " AND active=1 AND round_no<=? ORDER BY id",
                (sid, cutoff)).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def deactivate_upto(self, sid: str, cutoff: int) -> None:
        with self._lock:
            self._conn().execute(
                "UPDATE messages SET active=0 WHERE session_id=? AND round_no<=?",
                (sid, cutoff))
            self._conn().commit()

    # ---------- 长期记忆 ----------
    def add_memory(self, kind: str, content: str,
                   source_session: str | None = None,
                   scope: Scope | None = None,
                   ttl_s: int | None = None) -> None:
        """写入长期记忆。

        scope: 隔离作用域(默认 'default'); ttl_s: 有效期秒数, None=取配置
        memory_default_ttl_s, <=0 表示永不过期。source_session 仅作溯源。
        """
        if kind not in KINDS or not content.strip():
            return
        scope = scope or Scope()
        ttl = get_settings().memory_default_ttl_s if ttl_s is None else ttl_s
        expires_at = (_expiry_after(ttl) if ttl and ttl > 0 else None)
        # 修正: 此前误用未定义的 _get_embedder(真实 NameError, 长期记忆落盘会崩)
        embedder = get_embedder()
        emb = None
        if embedder:  # 网络调用在锁外(带熔断, 失败/熔断中返回 None)
            vec = safe_embed_query(embedder, content)
            if vec is not None:
                emb = json.dumps(vec)
        with self._lock:
            if self._conn().execute(
                    "SELECT 1 FROM long_term_memories WHERE kind=? AND content=?"
                    " AND user_id=? AND project_id=? AND agent_id=?",
                    (kind, content, *scope)).fetchone():
                return  # 精确去重(作用域内); 语义级去重记为债务
            cur = self._conn().execute(
                "INSERT INTO long_term_memories(kind, content, embedding,"
                " source_session, created_at, user_id, project_id, agent_id,"
                " expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (kind, content, emb, source_session, _now(), *scope, expires_at))
            self._conn().execute(
                "INSERT INTO memory_fts(content, memory_id) VALUES(?,?)",
                (content, cur.lastrowid))
            self._conn().commit()

    def list_memories(self, kind: str | None = None, limit: int = 50,
                      scope: Scope | None = None) -> list[dict]:
        scope = scope or Scope()
        with self._lock:
            self._purge_expired_locked()
            rows = self._conn().execute(
                "SELECT kind, content FROM long_term_memories"
                f" WHERE {LIVE}"
                + (" AND kind=?" if kind else "")
                + " ORDER BY id DESC LIMIT ?",
                (*scope, _now(), *([kind] if kind else []), limit)).fetchall()
            return [dict(r) for r in rows]

    def purge_expired(self) -> int:
        """清理已过期的长期记忆(含 FTS 索引); 返回删除条数。"""
        with self._lock:
            return self._purge_expired_locked()

    def _purge_expired_locked(self) -> int:
        now = _now()
        ids = [r["id"] for r in self._conn().execute(
            "SELECT id FROM long_term_memories"
            " WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()]
        if not ids:
            return 0
        for mid in ids:
            self._conn().execute(
                "DELETE FROM memory_fts WHERE memory_id=?", (mid,))
        self._conn().execute(
            "DELETE FROM long_term_memories"
            " WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
        self._conn().commit()
        return len(ids)

    def search_memories(self, query: str, limit: int = 5,
                        scope: Scope | None = None) -> list[dict]:
        """混合检索(v1.1): 向量top20 + FTS5 top20 → RRF融合 → top-limit。

        与 kb/store.py 对齐; 两路各自独立取候选, 任一不可用(未配 embedding /
        熔断中 / FTS 无命中)仍由另一路返回结果(不再是互斥降级)。
        """
        scope = scope or Scope()
        rrf_k = max(1, get_settings().memory_rrf_k)
        # 修正: 同上, _get_embedder 未定义 → 用 get_embedder; 锁外做网络调用
        embedder = get_embedder()
        q_emb = safe_embed_query(embedder, query) if embedder else None
        with self._lock:
            self._purge_expired_locked()
            now = _now()
            vec_ids: list[int] = []
            if q_emb:
                rows = self._conn().execute(
                    "SELECT id, embedding FROM long_term_memories"
                    f" WHERE {LIVE} AND embedding IS NOT NULL",
                    (*scope, now)).fetchall()
                # _cosine → cosine: 修掉配置 embedding 后必抛的 NameError
                scored = [(r["id"], cosine(q_emb, json.loads(r["embedding"])))
                          for r in rows]
                vec_ids = [mid for mid, _ in
                           sorted(scored, key=lambda x: -x[1])[:CANDIDATES]]
            fts_ids: list[int] = []
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)[:8]
            if tokens:
                try:
                    fts_ids = [r["memory_id"] for r in self._conn().execute(
                        "SELECT f.memory_id FROM memory_fts f"
                        " JOIN long_term_memories m ON m.id=f.memory_id"
                        f" WHERE memory_fts MATCH ? AND {LIVE_M}"
                        " ORDER BY f.rank LIMIT ?",
                        (" OR ".join(f'"{t}"' for t in tokens),
                         *scope, now, CANDIDATES)).fetchall()]
                except sqlite3.OperationalError:
                    pass
            if not vec_ids and not fts_ids:
                # 兜底: 纯子串匹配(FTS5 对整串中文常无命中, 保留 v1.0 行为)
                if not query.strip():
                    return []
                rows = self._conn().execute(
                    "SELECT kind, content FROM long_term_memories"
                    f" WHERE {LIVE} AND content LIKE ?"
                    " ORDER BY id DESC LIMIT ?",
                    (*scope, now, f"%{query[:40]}%", limit)).fetchall()
                return [dict(r) for r in rows]
            fused: dict[int, float] = {}
            for rank, mid in enumerate(vec_ids):
                fused[mid] = fused.get(mid, 0.0) + 1.0 / (rrf_k + rank + 1)
            for rank, mid in enumerate(fts_ids):
                fused[mid] = fused.get(mid, 0.0) + 1.0 / (rrf_k + rank + 1)
            result = []
            for mid, _ in sorted(fused.items(), key=lambda x: -x[1])[:limit]:
                row = self._conn().execute(
                    "SELECT kind, content FROM long_term_memories"
                    f" WHERE id=? AND {LIVE}", (mid, *scope, now)).fetchone()
                if row:
                    result.append(dict(row))
            return result


@lru_cache
def get_memory_db() -> MemoryDB:
    return MemoryDB(get_settings().data_dir / "agent_pro.db")
