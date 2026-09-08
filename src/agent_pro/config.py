# ============================================================
# config.py | v0.2诞生 → v0.3(+workspace/limit) → v0.4(+data/+memory/+embedding)
# ============================================================
from functools import lru_cache
from pathlib import Path
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    api_key: str
    model_name: str = "deepseek-chat"
    base_url: str | None = None
    workspace_dir: Path = Path("./workspace")
    agent_recursion_limit: int = 80
    # v0.4: 数据目录（SQLite）与记忆参数
    data_dir: Path = Path("./data")
    memory_short_term_rounds: int = 5  # 短期记忆保留轮数
    memory_compact_batch: int = 5  # 每攒够5轮新对话触发一次压缩
    # v0.4: 长期记忆向量检索（OpenAI兼容协议；留空=降级FTS5关键词检索）
    embedding_model: str = ""
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    # v0.5: API服务（局域网测试，预留公网暴露）
    api_host: str = "127.0.0.1"  # 局域网测试设 0.0.0.0
    api_port: int = 8000
    api_auth_key: str = ""  # 留空时API拒绝启动，CLI不受影响


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_model() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=s.model_name, api_key=s.api_key, base_url=s.base_url)