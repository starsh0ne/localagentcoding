# ============================================================
# workspace_files.py | v0.3诞生 → v0.4工厂化重构
# 变更: 模块级单例沙箱 → make_workspace_tools(root)工厂
#       ★支撑: coder绑定任务子目录 / 通用agent全局只读
# include_write=False → 只读工具集(通用agent验收用)
# ============================================================
from pathlib import Path
from langchain.tools import tool
from agent_pro.tools.sandbox import SandboxViolation, WorkspaceSandbox

MAX_READ_BYTES = 100_000


def _fmt_sandbox_error(e: SandboxViolation) -> str:
    return f"拒绝: {e}"


def make_workspace_tools(root: Path, include_write: bool = True) -> list:
    sandbox = WorkspaceSandbox(root)

    @tool
    def list_workspace(path: str = ".") -> str:
        """列出工作区内指定目录的文件与子目录（含大小），path为相对路径，默认根目录。"""
        try:
            target = sandbox.resolve_safe(path)
            if not target.exists():
                return f"错误: 路径不存在: {path}"
            if target.is_file():
                return f"错误: {path} 是文件而非目录"
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            if not entries:
                return "(空目录)"
            lines = []
            for e in entries:
                rel = e.relative_to(sandbox.root)
                lines.append(f"[DIR]  {rel}/" if e.is_dir()
                             else f"[FILE] {rel}  ({e.stat().st_size} bytes)")
            return "\n".join(lines)
        except SandboxViolation as e:
            return _fmt_sandbox_error(e)
        except OSError as e:
            return f"错误: {type(e).__name__}: {e}"

    @tool
    def read_file(path: str) -> str:
        """读取工作区内文本文件内容（相对路径）；超过100KB截断。"""
        try:
            target = sandbox.resolve_safe(path)
            if not target.exists():
                return f"错误: 文件不存在: {path}"
            if target.is_dir():
                return f"错误: {path} 是目录，请用 list_workspace 查看"
            data = target.read_bytes()
            content = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
            if len(data) > MAX_READ_BYTES:
                content += "\n…(内容超长已截断)"
            return content
        except SandboxViolation as e:
            return _fmt_sandbox_error(e)
        except OSError as e:
            return f"错误: {type(e).__name__}: {e}"

    tools = [list_workspace, read_file]
    if include_write:
        @tool
        def write_file(path: str, content: str) -> str:
            """在工作区写入文件（覆盖语义，已存在则整体替换；自动建父目录）。
            每次必须写入完整内容，禁止省略。"""
            try:
                target = sandbox.resolve_safe(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return f"已写入 {path} ({len(content.encode('utf-8'))} bytes)"
            except SandboxViolation as e:
                return _fmt_sandbox_error(e)
            except OSError as e:
                return f"错误: {type(e).__name__}: {e}"

        @tool
        def delete_file(path: str) -> str:
            """删除工作区内文件或空目录（仅限空目录）。"""
            try:
                target = sandbox.resolve_safe(path)
                if target == sandbox.root:
                    return "拒绝: 不能删除工作区根目录"
                if not target.exists():
                    return f"错误: 不存在: {path}"
                if target.is_dir():
                    if any(target.iterdir()):
                        return "错误: 目录非空，请先删除其中所有文件"
                    target.rmdir()
                    return f"已删除目录 {path}"
                target.unlink()
                return f"已删除文件 {path}"
            except SandboxViolation as e:
                return _fmt_sandbox_error(e)
            except OSError as e:
                return f"错误: {type(e).__name__}: {e}"

        tools += [write_file, delete_file]
    return tools