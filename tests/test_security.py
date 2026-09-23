# ============================================================
# tests/test_security.py | v1.0 补充(P0 加固的回归测试)
# 覆盖: 文件沙箱穿越拦截 / pip 包名白名单 / API 鉴权 401 / 队列背压 / 错误脱敏 / turn 回收
# 设计: 纯单元, 不依赖网络/LLM; 用 monkeypatch 隔离外部副作用
# ============================================================
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from agent_pro.api.queue import MemoryTurnQueue, QueueFullError
from agent_pro.api.worker import Turn, TurnWorker, client_safe_error
from agent_pro.config import get_settings
from agent_pro.tools.env_tools import pip_install
from agent_pro.tools.sandbox import SandboxViolation, WorkspaceSandbox


# ---------- 文件沙箱 ----------
def test_sandbox_blocks_absolute_path():
    with tempfile.TemporaryDirectory() as d:
        sb = WorkspaceSandbox(Path(d))
        with pytest.raises(SandboxViolation):
            sb.resolve_safe("/etc/passwd")


def test_sandbox_blocks_traversal():
    with tempfile.TemporaryDirectory() as d:
        sb = WorkspaceSandbox(Path(d))
        with pytest.raises(SandboxViolation):
            sb.resolve_safe("../../etc/passwd")


def test_sandbox_allows_inside():
    with tempfile.TemporaryDirectory() as d:
        sb = WorkspaceSandbox(Path(d))
        p = sb.resolve_safe("sub/dir/file.txt")
        assert p.is_relative_to(sb.root)


# ---------- pip 安装白名单 ----------
@pytest.mark.parametrize("bad", [
    "-e git+https://example.com/x",
    "--index-url https://evil.com",
    "requests; rm -rf /",
    "pkg -i https://evil.com",
    "../../evil",
])
def test_pip_install_rejects_malicious(bad):
    out = pip_install.invoke(bad)
    assert "拒绝" in out, f"应拒绝非法规格: {bad}"


def test_pip_install_accepts_valid_without_exec():
    fake = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("agent_pro.tools.env_tools.subprocess.run", return_value=fake):
        out = pip_install.invoke("requests flask>=3.0")
        assert "exit_code=0" in out


# ---------- API 鉴权 ----------
def test_api_key_required(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "secret123")
    get_settings.cache_clear()
    try:
        # 缺头
        with pytest.raises(HTTPException) as e1:
            asyncio.run(_call_require(None))
        assert e1.value.status_code == 401
        # 错头
        with pytest.raises(HTTPException) as e2:
            asyncio.run(_call_require("wrong"))
        assert e2.value.status_code == 401
        # 正确
        assert asyncio.run(_call_require("secret123")) is None
    finally:
        get_settings.cache_clear()


async def _call_require(key):
    # 延迟导入, 避免测试模块加载即触发重型依赖(可选)
    from agent_pro.api.server import require_api_key
    return await require_api_key(key)


# ---------- 队列背压 ----------
def test_queue_full_raises():
    q = MemoryTurnQueue(maxsize=1)
    asyncio.run(q.put({"a": 1}))
    with pytest.raises(QueueFullError):
        asyncio.run(q.put({"b": 2}))


# ---------- 错误脱敏 ----------
def test_client_safe_error_sanitized():
    out = client_safe_error("turn-x", RuntimeError("secret /home/foo/key"))
    assert "secret" not in out
    assert "turn-x" in out
    assert "服务端日志" in out


# ---------- turn 内存回收 ----------
def test_turn_eviction():
    w = TurnWorker()
    w.turns["t1"] = Turn(turn_id="t1", session_id="s1", message="hi")
    w._archive("t1")
    assert "t1" not in w.turns
