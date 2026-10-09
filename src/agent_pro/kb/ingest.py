# ============================================================
# kb/ingest.py | v0.6 诞生(导入管道: 扫描→解析→分块→嵌入→入库)
# 语义: import=按source全量替换(不删库内已有); reindex=清空重建
# 债务: 目录监听增量更新(用户决策: 后续版本实现)
# ============================================================
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from agent_pro.embeddings import get_embedder, safe_embed_query
from agent_pro.kb.chunking import chunk_document
from agent_pro.kb.parsers import parse_file, supported_extensions
from agent_pro.kb.store import get_kb_store


@dataclass
class IngestReport:
    files: int = 0
    chunks: int = 0
    embedded: int = 0
    failed: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def __str__(self):
        return (f"导入完成: {self.files}个文档, {self.chunks}个块"
                f"(向量覆盖{self.embedded}), 耗时{self.duration_s:.1f}s"
                + (f"; 失败: {self.failed}" if self.failed else ""))


def ingest_dir(kb_dir: Path, purge: bool = False) -> IngestReport:
    store = get_kb_store()
    if purge:
        store.purge_all()
    embedder = get_embedder()
    exts = set(supported_extensions())
    files = sorted(p for p in kb_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in exts)
    rep = IngestReport(duration_s=-time.monotonic())
    for f in files:
        rel = f.relative_to(kb_dir).as_posix()
        try:
            doc = parse_file(f, rel)
            payload = []
            for c in chunk_document(doc):
                emb = None
                if embedder:
                    vec = safe_embed_query(embedder, c.content)
                    if vec is not None:
                        emb = json.dumps(vec)
                        rep.embedded += 1
                    # 失败或熔断中 → emb=None: 单块降级FTS, 不阻塞导入
                payload.append((c.heading_path, c.content, emb))
            store.replace_source(rel, doc.title, payload)
            rep.chunks += len(payload)
            rep.files += 1
        except Exception as e:
            rep.failed.append(f"{rel}: {type(e).__name__}: {e}")
    rep.duration_s += time.monotonic()
    return rep