# ============================================================
# kb/store.py | v0.6 诞生(kb.db: 块存储+FTS5+RRF混合检索)
# 线程模型沿用项目规范: RLock + embedding网络调用在锁外
# 检索: 向量top20 + FTS5 top20 → RRF融合(k=60) → top-N
# 规模边界: 内存余弦, 自用规模(万级块内)适用; 更大→迁移向量库(债务)
# ============================================================
import json
import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from agent_pro.config import get_settings
from agent_pro.embeddings import cosine, get_embedder

RRF_K = 60
CANDIDATES = 20


class KBStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS kb_chunks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL, title TEXT NOT NULL,
            heading_path TEXT NOT NULL, chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL, embedding TEXT);
        CREATE INDEX IF NOT EXISTS idx_kb_src ON kb_chunks(source);
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
            content, title, heading_path, chunk_id UNINDEXED);
        """)
        self.conn.commit()

    def replace_source(self, source: str, title: str,
                       chunks: list[tuple[str, str, str | None]]) -> int:
        """单文档全量替换(旧块删除后插入), 幂等可重跑; 返回块数"""
        with self._lock:
            for r in self.conn.execute(
                    "SELECT id FROM kb_chunks WHERE source=?", (source,)).fetchall():
                self.conn.execute("DELETE FROM kb_fts WHERE chunk_id=?", (r["id"],))
            self.conn.execute("DELETE FROM kb_chunks WHERE source=?", (source,))
            for idx, (heading, content, emb) in enumerate(chunks):
                cur = self.conn.execute(
                    "INSERT INTO kb_chunks(source, title, heading_path, chunk_index,"
                    " content, embedding) VALUES(?,?,?,?,?,?)",
                    (source, title, heading, idx, content, emb))
                self.conn.execute(
                    "INSERT INTO kb_fts(content, title, heading_path, chunk_id)"
                    " VALUES(?,?,?,?)", (content, title, heading, cur.lastrowid))
            self.conn.commit()
            return len(chunks)

    def purge_all(self) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM kb_chunks")
            self.conn.execute("DELETE FROM kb_fts")
            self.conn.commit()

    def stats(self) -> dict:
        with self._lock:
            docs = self.conn.execute(
                "SELECT COUNT(DISTINCT source) AS n FROM kb_chunks").fetchone()["n"]
            total = self.conn.execute(
                "SELECT COUNT(*) AS n FROM kb_chunks").fetchone()["n"]
            emb = self.conn.execute(
                "SELECT COUNT(*) AS n FROM kb_chunks"
                " WHERE embedding IS NOT NULL").fetchone()["n"]
        return {"documents": docs, "chunks": total, "embedded_chunks": emb,
                "retrieval_mode": "hybrid(vector+fts)" if emb else "fts-keyword"}

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """混合检索: 向量+FTS5各取候选 → RRF融合 → top-limit"""
        embedder = get_embedder()
        q_emb = None
        if embedder:
            try:
                q_emb = embedder.embed_query(query)  # 网络调用在锁外
            except Exception:
                q_emb = None
        with self._lock:
            vec_ids: list[int] = []
            if q_emb:
                rows = self.conn.execute(
                    "SELECT id, embedding FROM kb_chunks"
                    " WHERE embedding IS NOT NULL").fetchall()
                scored = [(r["id"], cosine(q_emb, json.loads(r["embedding"])))
                          for r in rows]
                vec_ids = [cid for cid, _ in
                           sorted(scored, key=lambda x: -x[1])[:CANDIDATES]]
            fts_ids: list[int] = []
            tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)[:8]
            if tokens:
                try:
                    fts_q = " OR ".join(f'"{t}"' for t in tokens)
                    fts_ids = [r["chunk_id"] for r in self.conn.execute(
                        "SELECT chunk_id FROM kb_fts WHERE kb_fts MATCH ?"
                        " ORDER BY rank LIMIT ?",
                        (fts_q, CANDIDATES)).fetchall()]
                except sqlite3.OperationalError:
                    pass
            if not vec_ids and not fts_ids:
                return []
            fused: dict[int, float] = {}
            for rank, cid in enumerate(vec_ids):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            for rank, cid in enumerate(fts_ids):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            result = []
            for cid, _ in sorted(fused.items(), key=lambda x: -x[1])[:limit]:
                row = self.conn.execute(
                    "SELECT source, title, heading_path, content"
                    " FROM kb_chunks WHERE id=?", (cid,)).fetchone()
                if row:
                    result.append(dict(row))
            return result


@lru_cache
def get_kb_store() -> KBStore:
    return KBStore(get_settings().data_dir / "kb.db")