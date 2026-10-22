# ============================================================
# tests/test_v1_1_memory.py | v1.1 记忆系统完善(隔离/召回/RRF/TTL/API)
# 设计: 纯单元, 不联网不调 LLM; embedding 用假替身注入; DB 用 tmp_path 隔离
# ============================================================
import time

import pytest

from agent_pro import embeddings as emb_mod
from agent_pro.memory import store as store_mod
from agent_pro.memory.store import MemoryDB, Scope


# ---------- 假 embedding: 按关键词给出确定性向量 ----------
class _FakeEmbedder:
    """含 alpha/gamma → [1,0]; 含 beta → [0,1]; 其余 [0.5,0.5]。"""

    def embed_query(self, text: str):
        t = text.lower()
        if "alpha" in t or "gamma" in t:
            return [1.0, 0.0]
        if "beta" in t:
            return [0.0, 1.0]
        return [0.5, 0.5]


@pytest.fixture
def db(tmp_path):
    return MemoryDB(tmp_path / "mem.db")


@pytest.fixture
def with_embedding(monkeypatch):
    monkeypatch.setattr(store_mod, "get_embedder", lambda: _FakeEmbedder())
    yield
    emb_mod._breaker_until = 0.0
    emb_mod._breaker_opened = False


# ---------- 1. 隔离 ----------
def test_scope_isolation_user(db):
    db.add_memory("fact", "u1 的私有事实", "s-1", scope=Scope("u1"))
    db.add_memory("fact", "u2 的私有事实", "s-2", scope=Scope("u2"))
    assert [r["content"] for r in db.search_memories(
        "私有事实", limit=5, scope=Scope("u1"))] == ["u1 的私有事实"]
    assert [r["content"] for r in db.search_memories(
        "私有事实", limit=5, scope=Scope("u2"))] == ["u2 的私有事实"]
    assert db.search_memories("私有事实", limit=5, scope=Scope("u3")) == []


@pytest.mark.parametrize("field", ["user_id", "project_id", "agent_id"])
def test_scope_isolation_each_layer(db, field):
    """三层各自独立生效, 任一层不同即互不召回。"""
    a = Scope(**{field: "A"})
    b = Scope(**{field: "B"})
    db.add_memory("fact", "scope-A 内容", "s", scope=a)
    db.add_memory("fact", "scope-B 内容", "s", scope=b)
    assert [r["content"] for r in db.search_memories("内容", limit=5, scope=a)] \
        == ["scope-A 内容"]
    assert [r["content"] for r in db.list_memories(scope=b)] == ["scope-B 内容"]


def test_default_scope_backward_compatible(db):
    """不传作用域 → 'default', 行为与 v1.0 一致(向后兼容)。"""
    db.add_memory("preference", "默认作用域内容", "s-legacy")
    assert [r["content"] for r in db.list_memories()] == ["默认作用域内容"]
    assert db.search_memories("默认作用域", limit=5)
    assert db.search_memories("默认作用域", limit=5, scope=Scope())  # 显式等价


def test_legacy_db_migrates_to_default_scope(tmp_path):
    """旧库(v1.0 表结构)打开后: 三列补上 NOT NULL DEFAULT 'default', 数据不丢。"""
    import sqlite3
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE sessions(id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL, round_no INTEGER NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL);
    CREATE TABLE long_term_memories(id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL, content TEXT NOT NULL, embedding TEXT,
        source_session TEXT, created_at TEXT NOT NULL);
    CREATE VIRTUAL TABLE memory_fts USING fts5(content, memory_id UNINDEXED);
    INSERT INTO sessions(id, summary, created_at, updated_at)
        VALUES('s-old', '旧摘要', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
    INSERT INTO long_term_memories(kind, content, source_session, created_at)
        VALUES('preference', '旧库遗留偏好', 's-old', '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    db = MemoryDB(path)
    assert db.get_scope("s-old") == Scope()          # 旧会话落到 default
    assert db.get_summary("s-old") == "旧摘要"        # 数据未丢
    assert [r["content"] for r in db.list_memories()] == ["旧库遗留偏好"]
    db.add_memory("fact", "迁移后新写入", "s-old")     # 新写入照常工作
    assert len(db.list_memories()) == 2


def test_source_session_is_trace_only(db):
    """source_session 仅溯源: 不同会话写入同作用域仍共享召回。"""
    db.add_memory("fact", "跨会话共享事实", "s-1", scope=Scope("u1"))
    db.add_memory("fact", "另一条事实", "s-2", scope=Scope("u1"))
    got = db.search_memories("事实", limit=5, scope=Scope("u1"))
    assert len(got) == 2  # s-1/s-2 不互相隔离


def test_session_scope_persisted_and_read_back(db):
    db.ensure_session("s-scope", Scope("u9", "p9", "a9"))
    assert db.get_scope("s-scope") == Scope("u9", "p9", "a9")
    assert db.get_scope("s-none") is None
    # 已存在的会话不被后续 ensure_session 改作用域
    assert db.ensure_session("s-scope", Scope()) is False
    assert db.get_scope("s-scope") == Scope("u9", "p9", "a9")


def test_session_manager_scope_partial_override_and_fallback(db, monkeypatch):
    from agent_pro.memory import manager as manager_mod

    monkeypatch.setattr(manager_mod, "get_memory_db", lambda: db)
    sid = "s-scope-override"
    session_scope = Scope("u1", "p1", "a1")
    db.ensure_session(sid, session_scope)

    assert manager_mod.SessionManager(sid).scope == session_scope
    assert manager_mod.SessionManager(
        sid, Scope("u2", "", "")).scope == Scope("u2", "p1", "a1")
    assert db.get_scope(sid) == session_scope
    assert manager_mod.SessionManager(sid, None).scope == session_scope


# ---------- 2. 召回: 三类记忆可读 ----------
def test_recall_covers_fact_and_entity(db):
    """v1.0 只取 preference, fact/entity 读不到 → 此处钉死三类均可召回。"""
    db.add_memory("preference", "用户偏好: reply in Chinese", "s")
    db.add_memory("fact", "部署目标: Kubernetes cluster", "s")
    db.add_memory("entity", "Alice 是项目负责人", "s")
    got = {r["kind"] for r in db.search_memories("Chinese Kubernetes Alice",
                                                 limit=5)}
    assert got == {"preference", "fact", "entity"}


def test_recall_respects_top_k(db, monkeypatch):
    monkeypatch.setenv("MEMORY_RECALL_TOP_K", "2")
    from agent_pro.config import get_settings
    get_settings.cache_clear()
    try:
        assert get_settings().memory_recall_top_k == 2
        for i in range(5):
            db.add_memory("fact", f"第{i}条事实", "s")
        assert len(db.search_memories("事实", limit=2)) == 2
    finally:
        get_settings.cache_clear()


# ---------- 3. RRF 混合检索 ----------
def test_rrf_fuses_vector_and_fts_paths(db, with_embedding):
    """两路各命中一条互不相交的结果 → RRF 融合后两条都返回(非互斥降级)。"""
    db.add_memory("fact", "gamma delta 仅向量可命中", "s")   # 向量近, FTS 无重叠
    db.add_memory("fact", "keyword tokens 仅FTS可命中", "s")  # FTS 命中, 向量远
    got = [r["content"] for r in db.search_memories("alpha keyword", limit=5)]
    assert any("gamma" in c for c in got), f"向量路未生效: {got}"
    assert any("keyword" in c for c in got), f"FTS 路未生效: {got}"


def test_vector_path_works_when_embedding_configured(db, with_embedding):
    """回归: v1.0 向量分支调未定义的 _cosine → 配置 embedding 即 NameError。"""
    db.add_memory("fact", "alpha 向量文档", "s")
    db.add_memory("fact", "beta 不相关文档", "s")
    top = db.search_memories("alpha", limit=2)[0]["content"]
    assert "alpha" in top


def test_fts_path_returns_when_embedding_unavailable(db, monkeypatch):
    """未配置 embedding(FTS 单路)仍返回结果。"""
    monkeypatch.setattr(store_mod, "get_embedder", lambda: False)
    db.add_memory("fact", "不配 embedding 也要能被检索到", "s")
    got = db.search_memories("检索", limit=3)
    assert got and "检索" in got[0]["content"]


def test_fts_path_returns_while_circuit_open(db, with_embedding):
    """熔断中(embedding 短路)仍从 FTS 路径返回结果。"""
    db.add_memory("fact", "熔断中仍应被 FTS 召回", "s")
    emb_mod._breaker_until = time.monotonic() + 60  # 强制熔断
    try:
        got = db.search_memories("FTS 召回", limit=3)
    finally:
        emb_mod._breaker_until = 0.0
        emb_mod._breaker_opened = False
    assert got and "熔断" in got[0]["content"]


def test_vector_only_result_when_fts_misses(db, with_embedding):
    """FTS 无命中但向量有候选 → 仍返回(两路互为补充)。"""
    db.add_memory("fact", "gamma 语义相近但字面不同", "s")
    got = db.search_memories("zzz 无匹配词", limit=3)
    assert got and "gamma" in got[0]["content"]


# ---------- 4. TTL ----------
def test_expired_memory_not_recalled(db, monkeypatch):
    monkeypatch.setattr(store_mod, "_expiry_after",
                        lambda s: "2000-01-01T00:00:00+00:00")  # 已过期
    db.add_memory("fact", "已过期的记忆", "s", ttl_s=60)
    db.add_memory("fact", "未过期的记忆", "s")
    assert [r["content"] for r in db.search_memories("记忆", limit=5)] \
        == ["未过期的记忆"]
    assert [r["content"] for r in db.list_memories()] == ["未过期的记忆"]


def test_purge_expired_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(store_mod, "_expiry_after",
                        lambda s: "2000-01-01T00:00:00+00:00")
    db.add_memory("fact", "已过期的记忆", "s", ttl_s=60)
    db.add_memory("preference", "未过期的偏好", "s")
    assert db.purge_expired() == 1  # 只清过期的, 未过期不受影响
    assert db.purge_expired() == 0  # 幂等
    assert [r["content"] for r in db.list_memories()] == ["未过期的偏好"]


def test_default_ttl_from_config(db, monkeypatch):
    monkeypatch.setenv("MEMORY_DEFAULT_TTL_S", "3600")
    from agent_pro.config import get_settings
    get_settings.cache_clear()
    try:
        db.add_memory("fact", "带默认有效期的记忆", "s")
        assert db.search_memories("有效期", limit=3), "未过期应可召回"
        assert db.purge_expired() == 0
    finally:
        get_settings.cache_clear()


def test_no_expiry_by_default(db):
    """memory_default_ttl_s=0(默认) → 永不过期。"""
    db.add_memory("fact", "永不过期的记忆", "s")
    assert db.purge_expired() == 0
    assert db.list_memories()


# ---------- 5. API 契约 ----------
class _RecordingWorker:
    """替身 worker: 只记录提交的 turn(不进共享队列, 免污染其他测试)。"""

    def __init__(self) -> None:
        self.last = None

    async def submit(self, session_id, message, scope=None):
        from agent_pro.api.worker import Turn
        t = Turn(turn_id="turn-fake", session_id=session_id, message=message)
        if scope:
            t.user_id, t.project_id, t.agent_id = scope
        self.last = t
        return t


def test_api_session_and_chat_scope(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "k-1.1")
    from agent_pro.config import get_settings
    get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient
        from agent_pro.api import server as server_mod
        from agent_pro.memory.store import get_memory_db

        fake_worker = _RecordingWorker()
        monkeypatch.setattr(server_mod, "get_worker", lambda: fake_worker)
        client = TestClient(server_mod.app)  # 不用 with → 不起 lifespan
        h = {"X-API-Key": "k-1.1"}
        db = get_memory_db()

        # 创建会话带作用域
        r = client.post("/api/sessions", headers=h,
                        json={"user_id": "u1", "project_id": "p1"})
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        assert (r.json()["user_id"], r.json()["project_id"],
                r.json()["agent_id"]) == ("u1", "p1", "default")
        assert db.get_scope(sid) == Scope("u1", "p1", "default")

        # chat 未传作用域 → 回退会话创建时的作用域
        r2 = client.post(f"/api/sessions/{sid}/chat", headers=h,
                         json={"message": "hello"})
        assert r2.status_code == 200, r2.text
        assert fake_worker.last.scope() == Scope("u1", "p1", "default")

        # 默认作用域(不传 body)
        r3 = client.post("/api/sessions", headers=h)
        assert r3.status_code == 200, r3.text
        sid3 = r3.json()["session_id"]
        assert db.get_scope(sid3) == Scope()
    finally:
        get_settings.cache_clear()


def test_api_validations_kept(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "k-1.1")
    from agent_pro.config import get_settings
    get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient
        from agent_pro.api import server as server_mod
        client = TestClient(server_mod.app)
        h = {"X-API-Key": "k-1.1"}
        assert client.post("/api/sessions", headers=h).status_code == 200
        r = client.post("/api/sessions/" + "x" * 129 + "/chat", headers=h,
                        json={"message": "hi"})
        assert r.status_code == 400, "session_id ≤128 校验应保持"
        r = client.post("/api/sessions/s-len/chat", headers=h,
                        json={"message": "x" * 100_001})
        assert r.status_code == 422, "message ≤100000 校验应保持"
        r = client.post("/api/sessions/s-ok/chat", headers=h,
                        json={"message": "hi", "user_id": "u" * 129})
        assert r.status_code == 422, "作用域字段也应有长度上限"
    finally:
        get_settings.cache_clear()
