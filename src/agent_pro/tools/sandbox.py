# ============================================================
# tools/sandbox.py | v0.3 诞生（工作区路径沙箱）
# 职责: 所有文件工具的唯一路径守卫，LLM生成的路径一律视为不可信输入
# 防护: 绝对路径逃逸 / ../ 穿越 / 符号链接逃逸
# ============================================================
from pathlib import Path


class SandboxViolation(Exception):
    """路径越界 — 安全错误，直接拒绝，不进入重试"""


class WorkspaceSandbox:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_safe(self, rel_path: str) -> Path:
        """校验并解析工作区相对路径，返回绝对路径；越界抛SandboxViolation"""
        if not rel_path or not rel_path.strip():
            raise SandboxViolation("路径为空")
        # 关键1: 绝对路径传入时 (root / "/etc") 会直接替换为该路径 → 越界被拒
        # 关键2: resolve() 消解符号链接后再校验，防止链接指向外部
        candidate = (self.root / rel_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise SandboxViolation(f"路径越界(仅允许工作区内相对路径): {rel_path}")
        return candidate

    def resolve_abs_safe(self, abs_path: str | Path) -> Path:
        """校验并解析**绝对**路径，返回绝对 Path；越界抛 SandboxViolation。

        用于 cwd_win 这类由 LLM 直接给出的绝对执行目录(不像文件工具走相对路径,
        故不能复用 resolve_safe —— 那边 (root / 绝对路径) 会直接替换成入参)。
        """
        candidate = Path(abs_path).expanduser().resolve()
        if not candidate.is_relative_to(self.root):
            raise SandboxViolation(f"路径越界(仅允许工作区内): {abs_path}")
        return candidate