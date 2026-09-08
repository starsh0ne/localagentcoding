"""配置集中管理 (v0.2)

基于 pydantic-settings 读取 .env，集中管理 LLM / 工具 / 记忆 / RAG / API /
可观测性等各版本模块的运行时配置，避免散落在代码中。
"""

# TODO(v0.2): 定义 Settings（pydantic-settings.BaseSettings），从 .env 加载：
#   - llm: model_name, base_url, api_key
#   - tools: enabled 列表
#   - memory: window_size
#   - rag: embedding_model, vector_store_path
#   - api: host, port
#   - observability: langchain_tracing_v2, otlp_endpoint
# 并提供全局 settings = Settings() 单例。
# ============================================================
# config.py | v0.2 诞生（配置集中管理）
# 偿还v0.1债务: 环境变量散落在main.py → Pydantic Settings统一管理
# 设计: build_model()是全项目唯一的模型构造入口，
#       换模型只改.env，代码零改动
# ============================================================
from functools import lru_cache
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置：从 .env 读取，类型与缺失校验由 Pydantic 保证"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    # 模型接入（OpenAI兼容协议，一套代码通吃 GPT/DeepSeek/千问）
    api_key: str
    model_name: str = "deepseek-chat"
    base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    """单例配置：避免重复解析.env"""
    return Settings()


def build_model() -> ChatOpenAI:
    """统一模型构造入口"""
    s = get_settings()
    return ChatOpenAI(
        model=s.model_name,
        api_key=s.api_key,
        base_url=s.base_url,
    )