# ============================================================
# memory/store.py | v0.4 诞生（SQLite存储层: 会话/消息/摘要/长期记忆）
# 长期记忆双检索: embedding余弦优先 → FTS5降级 → LIKE兜底
# 注意: 多线程安全未处理(单写者) → v0.5 API并发时解决
# ============================================================
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from agent_pro.config import get_settings

KINDS = ("preference", "fact", "entity")  # 偏好/事实/实体关系 三类分库
_embedder = None  # None=未初始化 / False=禁用


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_embedder():
    global _embedder
    if _embedder is None:
        s = get_settings()
        if not s.embedding_model:
            _embedder = False
        else:
            from langchain_openai import OpenAIEmbeddings
            _embedder = OpenAIEmbeddings(
                model=s.embedding_model,
                api_key=s.embedding_api_key or s.api_key,
                base_url=s.embedding_base_url,
            )
    return _embedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


class MemoryDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
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

    # ---------- 会话与消息 ----------
    def ensure_session(self, sid: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions(id, created_at, updated_at) VALUES(?,?,?)",
            (sid, _now(), _now()))
        self.conn.commit()

    def get_summary(self, sid: str) -> str:
        row = self.conn.execute("SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
        return row["summary"] if row else ""

    def set_summary(self, sid: str, summary: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET summary=?, updated_at=? WHERE id=?", (summary, _now(), sid))
        self.conn.commit()

    def append_user(self, sid: str, content: str) -> None:
        r = self.conn.execute(
            "SELECT COALESCE(MAX(round_no),0)+1 AS r FROM messages WHERE session_id=?",
            (sid,)).fetchone()["r"]
        self._insert_msg(sid, r, "user", content)

    def append_assistant(self, sid: str, content: str) -> None:
        r = self.conn.execute(
            "SELECT COALESCE(MAX(round_no),1) AS r FROM messages WHERE session_id=?",
            (sid,)).fetchone()["r"]
        self._insert_msg(sid, r, "assistant", content)

    def _insert_msg(self, sid, round_no, role, content) -> None:
        self.conn.execute(
            "INSERT INTO messages(session_id, round_no, role, content, active, created_at)"
            " VALUES(?,?,?,?,1,?)", (sid, round_no, role, content, _now()))
        self.conn.commit()

    def active_messages(self, sid: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? AND active=1 ORDER BY id",
            (sid,)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def max_active_round(self, sid: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(round_no),0) AS m FROM messages"
            " WHERE session_id=? AND active=1", (sid,)).fetchone()
        return row["m"]

    def messages_upto(self, sid: str, cutoff: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages"
            " WHERE session_id=? AND active=1 AND round_no<=? ORDER BY id",
            (sid, cutoff)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def deactivate_upto(self, sid: str, cutoff: int) -> None:
        self.conn.execute(
            "UPDATE messages SET active=0 WHERE session_id=? AND round_no<=?", (sid, cutoff))
        self.conn.commit()

    # ---------- 长期记忆 ----------
    def add_memory(self, kind: str, content: str, source_session: str | None = None) -> None:
        if kind not in KINDS or not content.strip():
            return
        dup = self.conn.execute(
            "SELECT 1 FROM long_term_memories WHERE kind=? AND content=?",
            (kind, content)).fetchone()
        if dup:  # 精确去重；语义级去重记为债务
            return
        emb = None
        embedder = _get_embedder()
        if embedder:
            try:
                emb = json.dumps(embedder.embed_query(content))
            except Exception:
                emb = None  # embedding服务不可用→仅存FTS，检索自动降级
        cur = self.conn.execute(
            "INSERT INTO long_term_memories(kind, content, embedding, source_session, created_at)"
            " VALUES(?,?,?,?,?)", (kind, content, emb, source_session, _now()))
        self.conn.execute(
            "INSERT INTO memory_fts(content, memory_id) VALUES(?,?)", (content, cur.lastrowid))
        self.conn.commit()

    def list_memories(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT kind, content FROM long_term_memories"
            + (" WHERE kind=?" if kind else "")
            + " ORDER BY id DESC LIMIT ?",
            (*([kind] if kind else []), limit)).fetchall()
        return [dict(r) for r in rows]

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, kind, content, embedding FROM long_term_memories").fetchall()
        if not rows:
            return []
        # 优先: 向量余弦
        embedder = _get_embedder()
        if embedder:
            try:
                q_emb = embedder.embed_query(query)
                with_emb = [(r, json.loads(r["embedding"])) for r in rows if r["embedding"]]
                if with_emb:
                    ranked = sorted(with_emb, key=lambda x: -_cosine(q_emb, x[1]))
                    return [{"kind": r["kind"], "content": r["content"]} for r, _ in ranked[:limit]]
            except Exception:
                pass
        # 降级: FTS5关键词
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)[:8]
        if tokens:
            try:
                fts_q = " OR ".join(f'"{t}"' for t in tokens)
                hits = self.conn.execute(
                    "SELECT m.kind, m.content FROM memory_fts f"
                    " JOIN long_term_memories m ON m.id=f.memory_id"
                    " WHERE memory_fts MATCH ? ORDER BY f.rank LIMIT ?", (fts_q, limit)).fetchall()
                if hits:
                    return [dict(r) for r in hits]
            except sqlite3.OperationalError:
                pass
        # 兜底: LIKE
        hits = self.conn.execute(
            "SELECT kind, content FROM long_term_memories"
            " WHERE content LIKE ? LIMIT ?", (f"%{query[:40]}%", limit)).fetchall()
        return [dict(r) for r in hits]


@lru_cache
def get_memory_db() -> MemoryDB:
    return MemoryDB(get_settings().data_dir / "agent_pro.db")