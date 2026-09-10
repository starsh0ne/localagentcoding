# ============================================================
# kb/chunking.py | v0.6 诞生(结构感知分块)
# 策略: 小节合并(节标题内嵌保留溯源) / 超长节段落硬切+重叠
# 尺寸: 上限1200字, 重叠150字(中文自用规模经验值)
# ============================================================
from dataclasses import dataclass
from agent_pro.kb.parsers import ParsedDoc

MAX_CHARS = 1200
OVERLAP = 150


@dataclass
class Chunk:
    heading_path: str
    content: str


def _split_long(text: str) -> list[str]:
    """超长内容: 按段落聚合, 单段超长则字符硬切(带重叠)"""
    pieces, buf, size = [], [], 0
    for para in (p for p in text.split("\n\n") if p.strip()):
        plen = len(para)
        if plen > MAX_CHARS:
            if buf:
                pieces.append("\n\n".join(buf))
                buf, size = [], 0
            step = MAX_CHARS - OVERLAP
            for i in range(0, plen, step):
                pieces.append(para[i:i + MAX_CHARS])
            continue
        if size + plen + 2 > MAX_CHARS and buf:
            pieces.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += plen + 2
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces


def chunk_document(doc: ParsedDoc) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf_parts, buf_heading, size = [], "", 0

    def flush():
        nonlocal buf_parts, buf_heading, size
        if buf_parts:
            chunks.append(Chunk(buf_heading, "\n\n".join(buf_parts)))
        buf_parts, buf_heading, size = [], "", 0

    for sec in doc.sections:
        # 合并块内嵌节标题行 → 溯源信息随内容走
        rendered = (sec.content
                    if sec.heading_path in ("(前言)", "(正文)")
                    else f"## {sec.heading_path}\n{sec.content}")
        if len(sec.content) > MAX_CHARS:
            flush()
            for piece in _split_long(sec.content):
                chunks.append(Chunk(sec.heading_path, piece))
            continue
        if size + len(rendered) + 2 > MAX_CHARS and buf_parts:
            flush()
        if not buf_parts:
            buf_heading = sec.heading_path
        buf_parts.append(rendered)
        size += len(rendered) + 2
    flush()
    return chunks