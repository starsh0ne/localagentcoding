# ============================================================
# sandbox/wsl_exec.py | v0.8 诞生（WSL2 隔离执行器）
# 设计: agent(尤其 coder 自验/跑测试)生成的代码, 在 WSL2 子环境执行,
#       而非宿主机任意跑。隔离强度=中等(防误用+进程/目录隔离),
#       不防针对性恶意代码读 Windows 盘(WSL 默认挂 /mnt/c), 见方案 §7。
# 降级策略: WSL 不可用时返回明确错误, 绝不静默回退宿主机执行。
# ============================================================
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# 执行输出上限(防超大输出撑爆内存/日志)
_MAX_STDOUT = 20000
_MAX_STDERR = 8000


def wsl_available() -> bool:
    """探测 wsl.exe 是否可用(Windows 才有, WSL 子环境自然有)。"""
    return shutil.which("wsl.exe") is not None


WSL_AVAILABLE = wsl_available()


def _win_to_wsl(win_path: str) -> str:
    """Windows 绝对路径 → WSL 路径。
    C:\\Users\\foo\\bar → /mnt/c/Users/foo/bar
    无盘符(相对路径)原样返回。"""
    p = Path(win_path)
    if not getattr(p, "drive", ""):
        return win_path
    letter = p.drive.rstrip(":").lower()
    rest = "/".join(part for part in p.parts[1:] if part not in ("\\", "/"))
    return f"/mnt/{letter}/{rest}"


@dataclass
class SandboxResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None  # 基础设施错误(WSL缺失/异常), 非命令退出码
    duration_s: float = 0.0

    def to_text(self) -> str:
        out = []
        if self.error:
            out.append(f"[沙箱错误] {self.error}")
        out.append(f"(exit={self.returncode}, 耗时{self.duration_s:.1f}s)")
        if self.stdout:
            out.append("--- stdout ---\n" + self.stdout[:_MAX_STDOUT])
        if self.stderr:
            out.append("--- stderr ---\n" + self.stderr[:_MAX_STDERR])
        return "\n".join(out)


def run_in_wsl(command: str, cwd_win: str | None = None,
               timeout: int = 120) -> SandboxResult:
    """在 WSL2 子环境执行 shell 命令(隔离)。

    - command: 完整 bash 命令(可多行)。
    - cwd_win: 命令运行的 Windows 工作区绝对路径(映射到 /mnt/x/...)。
    - timeout: 硬上限秒; 超时杀进程并返回 timed_out=True。
    返回 SandboxResult(含退出码/截断输出/耗时)。
    """
    if not WSL_AVAILABLE:
        return SandboxResult(returncode=-1, stdout="", stderr="",
                             error="WSL 不可用: 拒绝在宿主机直接执行")
    wsl_cwd = _win_to_wsl(cwd_win) if cwd_win else None
    # 确保 UTF-8 输出; 工作根锁定为传入目录
    full = "export LANG=C.UTF-8 LC_ALL=C.UTF-8;"
    if wsl_cwd:
        full += f" cd {shlex.quote(wsl_cwd)} &&"
    full += f" {command}"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["wsl.exe", "bash", "-lc", full],
            capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace")
        return SandboxResult(returncode=proc.returncode,
                              stdout=proc.stdout or "",
                              stderr=proc.stderr or "",
                              duration_s=round(time.monotonic() - started, 2))
    except subprocess.TimeoutExpired as e:
        return SandboxResult(returncode=-1,
                             stdout=(e.stdout or ""),
                             stderr=(e.stderr or ""),
                             timed_out=True,
                             duration_s=round(time.monotonic() - started, 2),
                             error=f"超时({timeout}s)已终止")
    except Exception as e:  # 基础设施异常(非命令本身)
        return SandboxResult(returncode=-1, stdout="", stderr="",
                             duration_s=round(time.monotonic() - started, 2),
                             error=f"沙箱执行异常: {e}")
