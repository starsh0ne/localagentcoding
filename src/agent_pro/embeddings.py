# ============================================================
# embeddings.py | v0.6 诞生(共享embedding访问, 自memory/store.py提取)
# 记忆库与知识库共用; OpenAI兼容协议; 未配置→返回False自动降级FTS
# ============================================================
import time

from agent_pro.config import get_settings
from agent_pro.observability import get_logger

log = get_logger("embedding")

_embedder = None  # None=未初始化 / False=禁用

# ---- 熔断: embedding 服务不可用时, 冷却窗口内一律跳过, 不再发请求 ----
# 背景: 服务未启动(或被代理挡成 502)时, 每次检索都要等 SDK 退避重试(~4s)才降级
#       FTS; eval 12 条会白白多等近 1 分钟, 且日志被重试噪声刷屏。
# 机制: 首次失败即打开熔断(冷却 embedding_breaker_cooldown_s 秒), 窗口内直接返回
#       None(调用方降级 FTS); 窗口过后自动半开探测一次, 成功则关闭熔断。
_breaker_until = 0.0     # monotonic 时间戳; > now 表示熔断中
_breaker_opened = False  # 是否曾打开(用于只告警一次 + 恢复时提示)


def get_embedder():
    """返回embedding实例; 未配置EMBEDDING_MODEL时返回False(检索降级FTS5)"""
    global _embedder
    if _embedder is None:
        s = get_settings()
        if not s.embedding_model:
            _embedder = False
        else:
            from langchain_openai import OpenAIEmbeddings
            # check_embedding_ctx_length=False: 跳过 tiktoken 分词(对非 OpenAI 模型会
            # 回退下载 cl100k_base 词表, 离线/受限网络下 400 失败, 致 embedding 整条不可用)
            # max_retries=0: 服务不可用时快速失败(配合熔断), 不再白等 SDK 退避重试
            _embedder = OpenAIEmbeddings(
                model=s.embedding_model,
                api_key=s.embedding_api_key or s.api_key,
                base_url=s.embedding_base_url,
                check_embedding_ctx_length=False,
                max_retries=0)
    return _embedder


def embedding_disabled() -> bool:
    """熔断是否生效(处于冷却窗口内)。True → 调用方应直接降级 FTS, 别发请求。"""
    return time.monotonic() < _breaker_until


def mark_embedding_failure(exc: BaseException | None = None) -> None:
    """记录一次 embedding 失败: 打开(或续期)熔断冷却窗口。"""
    global _breaker_until, _breaker_opened
    cooldown = max(0, get_settings().embedding_breaker_cooldown_s)
    _breaker_until = time.monotonic() + cooldown
    if not _breaker_opened:
        _breaker_opened = True
        log.warning("embedding 不可用·开启熔断(降级纯 FTS)", extra={
            "event_kind": "embedding_breaker", "state": "open",
            "cooldown_s": cooldown,
            "error": f"{type(exc).__name__}: {exc}" if exc else None})


def _mark_recovered() -> None:
    """一次调用成功后关闭熔断(半开探测成功)。"""
    global _breaker_until, _breaker_opened
    if _breaker_opened:
        _breaker_opened = False
        _breaker_until = 0.0
        log.info("embedding 恢复可用·关闭熔断", extra={
            "event_kind": "embedding_breaker", "state": "closed"})


def safe_embed_query(embedder, text: str) -> list[float] | None:
    """带熔断的 embed_query。

    返回 None 表示「未启用 / 熔断中 / 本次调用失败」——调用方统一降级 FTS。
    熔断中直接短路返回, 不发起网络请求(这是省掉退避重试的关键)。
    """
    if not embedder or embedding_disabled():
        return None
    try:
        vec = embedder.embed_query(text)
    except Exception as e:
        mark_embedding_failure(e)
        return None
    _mark_recovered()
    return vec


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0