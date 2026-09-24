# ============================================================
# tests/test_verify_guard.py | 自验执行目录守卫回归
# 背景: request_verification 的 cwd_win 由 LLM 给出 → 一律视为不可信输入。
#       原先只靠提示词约束, 实际可在任意目录执行 WSL 命令。
# 设计: 纯单元, 不触发 WSL 执行(越界在审批/执行之前就被拒), 不依赖网络
# ============================================================
import os

import pytest

from agent_pro.config import get_settings
from agent_pro.tools.sandbox import SandboxViolation
from agent_pro.tools.verify_tools import request_verification, resolve_sandbox_cwd


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """把工作区指向临时目录, 避免污染真实 ./workspace(守卫会 mkdir)"""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_missing_cwd_defaults_to_absolute_workspace_root():
    """缺省 cwd_win → 工作区根; 必须是绝对路径(WSL 映射需要盘符)"""
    root = resolve_sandbox_cwd(None)
    assert os.path.isabs(root)


def test_blank_cwd_treated_as_missing():
    """空白串按缺省处理, 不应被当成相对路径"""
    assert resolve_sandbox_cwd("   ") == resolve_sandbox_cwd(None)


def test_cwd_inside_workspace_allowed():
    """工作区内的子目录应通过守卫"""
    root = resolve_sandbox_cwd(None)
    got = resolve_sandbox_cwd(os.path.join(root, "proj", "sub"))
    assert os.path.isabs(got)


def test_cwd_outside_workspace_raises():
    """工作区外的绝对路径必须抛 SandboxViolation"""
    parent = os.path.dirname(resolve_sandbox_cwd(None))
    with pytest.raises(SandboxViolation):
        resolve_sandbox_cwd(parent)


def test_tool_rejects_outside_cwd_before_execution():
    """越界应在审批/执行之前被拒 —— 不依赖 WSL 是否存在"""
    outside = os.path.dirname(resolve_sandbox_cwd(None))
    out = request_verification.invoke(
        {"command": "python hello.py", "cwd_win": outside})
    assert "越界" in out
    # 关键: 绝不能带着"沙箱执行结果"回来(那意味着真的执行了)
    assert "沙箱执行结果" not in out
