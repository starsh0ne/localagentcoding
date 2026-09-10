# ============================================================
# embeddings.py | v0.6 诞生(共享embedding访问, 自memory/store.py提取)
# 记忆库与知识库共用; OpenAI兼容协议; 未配置→返回False自动降级FTS
# ============================================================
from agent_pro.config import get_settings

_embedder = None  # None=未初始化 / False=禁用


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
            _embedder = OpenAIEmbeddings(
                model=s.embedding_model,
                api_key=s.embedding_api_key or s.api_key,
                base_url=s.embedding_base_url,
                check_embedding_ctx_length=False)
    return _embedder


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0