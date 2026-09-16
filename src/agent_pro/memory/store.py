# ============================================================
# memory/store.py | v0.4诞生 → v0.5变更(线程安全)
#   +check_same_thread=False +RLock
#   +embedding网络调用移出锁外(防SSE读阻塞)
# ============================================================
import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from agent_pro.config import get_settings
from agent_pro.embeddings import cosine, get_embedder


KINDS = ("preference", "fact", "entity")
_embedder = None  # None=未初始化 / False=禁用


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class MemoryDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
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
            embedding TEXT, source_session TEXT, created_at TEXT NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, memory_id UNINDEXED);
        """)
        self.conn.commit()

    # ---------- 会话与消息（每方法持锁，快速进出） ----------
    def ensure_session(self, sid: str) -> bool:
        """确保会话存在; 返回是否「本次新建」(v0.9 供惰性创建日志使用)。"""
        with self._lock:
            exists = self.conn.execute(
                "SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None
            if not exists:
                self.conn.execute(
                    "INSERT INTO sessions(id, created_at, updated_at)"
                    " VALUES(?,?,?)", (sid, _now(), _now()))
                self.conn.commit()
            return not exists

    def get_summary(self, sid: str) -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
            return row["summary"] if row else ""

    def set_summary(self, sid: str, summary: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE sessions SET summary=?, updated_at=? WHERE id=?",
                (summary, _now(), sid))
            self.conn.commit()

    def append_user(self, sid: str, content: str) -> None:
        with self._lock:
            r = self.conn.execute(
                "SELECT COALESCE(MAX(round_no),0)+1 AS r FROM messages"
                " WHERE session_id=?", (sid,)).fetchone()["r"]
            self.conn.execute(
                "INSERT INTO messages(session_id, round_no, role, content,"
                " active, created_at) VALUES(?,?,?,?,1,?)",
                (sid, r, "user", content, _now()))
            self.conn.commit()

    def append_assistant(self, sid: str, content: str) -> None:
        with self._lock:
            r = self.conn.execute(
                "SELECT COALESCE(MAX(round_no),1) AS r FROM messages"
                " WHERE session_id=?", (sid,)).fetchone()["r"]
            self.conn.execute(
                "INSERT INTO messages(session_id, round_no, role, content,"
                " active, created_at) VALUES(?,?,?,?,1,?)",
                (sid, r, "assistant", content, _now()))
            self.conn.commit()

    def active_messages(self, sid: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages WHERE session_id=?"
                " AND active=1 ORDER BY id", (sid,)).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def max_active_round(self, sid: str) -> int:
        with self._lock:
            return self.conn.execute(
                "SELECT COALESCE(MAX(round_no),0) AS m FROM messages"
                " WHERE session_id=? AND active=1", (sid,)).fetchone()["m"]

    def messages_upto(self, sid: str, cutoff: int) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages WHERE session_id=?"
                " AND active=1 AND round_no<=? ORDER BY id",
                (sid, cutoff)).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    def deactivate_upto(self, sid: str, cutoff: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE messages SET active=0 WHERE session_id=? AND round_no<=?",
                (sid, cutoff))
            self.conn.commit()

    # ---------- 长期记忆 ----------
    def add_memory(self, kind: str, content: str,
                   source_session: str | None = None) -> None:
        if kind not in KINDS or not content.strip():
            return
        embedder = _get_embedder()
        emb = None
        if embedder:  # 网络调用在锁外
            try:
                emb = json.dumps(embedder.embed_query(content))
            except Exception:
                emb = None
        with self._lock:
            if self.conn.execute(
                    "SELECT 1 FROM long_term_memories WHERE kind=? AND content=?",
                    (kind, content)).fetchone():
                return  # 精确去重; 语义级去重记为债务
            cur = self.conn.execute(
                "INSERT INTO long_term_memories(kind, content, embedding,"
                " source_session, created_at) VALUES(?,?,?,?,?)",
                (kind, content, emb, source_session, _now()))
            self.conn.execute(
                "INSERT INTO memory_fts(content, memory_id) VALUES(?,?)",
                (content, cur.lastrowid))
            self.conn.commit()

    def list_memories(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT kind, content FROM long_term_memories"
                + (" WHERE kind=?" if kind else "")
                + " ORDER BY id DESC LIMIT ?",
                (*([kind] if kind else []), limit)).fetchall()
            return [dict(r) for r in rows]

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        embedder = _get_embedder()  # 锁外
        q_emb = None
        if embedder:
            try:
                q_emb = embedder.embed_query(query)
            except Exception:
                q_emb = None
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, kind, content, embedding FROM long_term_memories"
            ).fetchall()
            if not rows:
                return []
            if q_emb:
                with_emb = [(r, json.loads(r["embedding"]))
                            for r in rows if r["embedding"]]
                if with_emb:
                    ranked = sorted(with_emb, key=lambda x: -_cosine(q_emb, x[1]))
                    return [{"kind": r["kind"], "content": r["content"]}
                            for r, _ in ranked[:limit]]
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)[:8]
            if tokens:
                try:
                    hits = self.conn.execute(
                        "SELECT m.kind, m.content FROM memory_fts f"
                        " JOIN long_term_memories m ON m.id=f.memory_id"
                        " WHERE memory_fts MATCH ? ORDER BY f.rank LIMIT ?",
                        (" OR ".join(f'"{t}"' for t in tokens), limit)).fetchall()
                    if hits:
                        return [dict(r) for r in hits]
                except sqlite3.OperationalError:
                    pass
            hits = self.conn.execute(
                "SELECT kind, content FROM long_term_memories"
                " WHERE content LIKE ? LIMIT ?", (f"%{query[:40]}%", limit)).fetchall()
            return [dict(r) for r in hits]


@lru_cache
def get_memory_db() -> MemoryDB:
    return MemoryDB(get_settings().data_dir / "agent_pro.db")