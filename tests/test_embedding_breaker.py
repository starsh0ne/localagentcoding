"""embedding 熔断 + 长期记忆 NameError 回归测试（不联网）。

背景:
- embedding 服务不可用时, 每条检索都要等 SDK 退避重试(~4s)才降级 FTS;
  熔断后首次失败即短路, 窗口内不再发请求。
- memory/store.py 曾误用未定义的 `_get_embedder()`(真实 NameError),
  长期记忆落盘/检索一触发即崩; 此处用回归测试钉死。
"""
import time

from agent_pro.embeddings import (embedding_disabled, safe_embed_query,
                                  mark_embedding_failure)
from agent_pro.memory.store import get_memory_db


class _BoomEmbedder:
    """每次调用都失败(模拟 502 / 连接拒绝)。"""

    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        raise RuntimeError("502 Bad Gateway")


class _OkEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return [0.1, 0.2, 0.3]


def _reset_breaker():
    import agent_pro.embeddings as emb
    emb._breaker_until = 0.0
    emb._breaker_opened = False


def test_breaker_short_circuits_after_first_failure():
    """首次失败后, 窗口内不再发起任何请求。"""
    _reset_breaker()
    boom = _BoomEmbedder()
    assert safe_embed_query(boom, "a") is None, "失败应返回 None(降级 FTS)"
    assert embedding_disabled() is True, "失败后熔断应打开"
    calls_after_first = boom.calls
    assert safe_embed_query(boom, "b") is None
    assert safe_embed_query(boom, "c") is None
    assert boom.calls == calls_after_first, (
        f"熔断中仍发起了 {boom.calls - calls_after_first} 次请求(未短路)")
    _reset_breaker()


def test_breaker_recovers_after_cooldown():
    """冷却窗口过后自动半开探测, 成功则关闭熔断。"""
    _reset_breaker()
    import agent_pro.embeddings as emb
    safe_embed_query(_BoomEmbedder(), "a")  # 打开熔断
    emb._breaker_until = time.monotonic() - 0.001  # 模拟冷却已过 → 半开
    ok = _OkEmbedder()
    assert safe_embed_query(ok, "b") == [0.1, 0.2, 0.3]
    assert embedding_disabled() is False, "成功后熔断应关闭"
    _reset_breaker()


def test_disabled_embedder_returns_none_without_call():
    """未启用 embedding(False)时直接短路, 不发请求。"""
    _reset_breaker()
    boom = _BoomEmbedder()
    assert safe_embed_query(False, "x") is None
    assert boom.calls == 0


def test_mark_failure_opens_breaker():
    _reset_breaker()
    assert embedding_disabled() is False
    mark_embedding_failure(RuntimeError("boom"))
    assert embedding_disabled() is True
    _reset_breaker()


def test_long_term_memory_paths_do_not_nameerror():
    """回归: 曾误用未定义的 _get_embedder() → add_memory/search_memories 崩溃。"""
    db = get_memory_db()
    db.add_memory("fact", "测试长期记忆内容-熔断回归", "s-test")
    out = db.search_memories("长期记忆")
    assert isinstance(out, list)
