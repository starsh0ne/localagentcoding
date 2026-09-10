# ============================================================
# kb/parsers.py | v0.6 诞生(解析器注册表)
# 扩展方式: 新增@register_parser装饰的解析函数+对应依赖即可,
#           管道(分块/入库/检索)零改动 —— 为doc/docx/xlsx/csv/PDF预留
# 当前支持: .md .markdown .txt
# ============================================================
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Section:
    heading_path: str  # 如 "编码规范 > 类型注解"; 无标题文档为"(前言)"
    content: str


@dataclass
class ParsedDoc:
    source: str  # 相对knowledge根的posix路径
    title: str
    sections: list[Section]


PARSERS: dict[str, Callable[[Path, str], ParsedDoc]] = {}


def register_parser(*exts: str):
    def deco(fn):
        for e in exts:
            PARSERS[e.lower()] = fn
        return fn

    return deco


def supported_extensions() -> list[str]:
    return sorted(PARSERS)


def parse_file(path: Path, rel: str) -> ParsedDoc:
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"不支持的格式: {path.suffix}")
    return parser(path, rel)


@register_parser(".md", ".markdown")
def parse_markdown(path: Path, rel: str) -> ParsedDoc:
    """按ATX标题切节, 保留标题层级路径; 代码围栏内的#不误判"""
    text = path.read_text(encoding="utf-8", errors="replace")
    title, stack, buf = path.stem, [], []
    sections: list[Section] = []
    in_fence = False

    def flush():
        content = "\n".join(buf).strip()
        if content:
            sections.append(
                Section(" > ".join(stack) if stack else "(前言)", content))
        buf.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            level, htext = len(m.group(1)), m.group(2).strip()
            flush()
            if level == 1 and title == path.stem and not sections:
                title = htext  # 首个未占用的H1 → 文档标题
            stack = stack[: level - 1]
            stack.append(htext)
        else:
            buf.append(line)
    flush()
    return ParsedDoc(rel, title, sections)


@register_parser(".txt")
def parse_text(path: Path, rel: str) -> ParsedDoc:
    """纯文本: 整体单节, 交给分块器按窗口切"""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return ParsedDoc(rel, path.stem, [Section("(正文)", text)] if text else [])