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
    # v1.1: 长期记忆召回/有效期/融合系数
    memory_recall_top_k: int = 5   # 每轮长期记忆语义召回条数
    memory_default_ttl_s: int = 0  # 长期记忆默认有效期(秒); 0=永不过期
    memory_rrf_k: int = 60         # RRF 融合系数(与 kb/store.py 保持一致)
    # v0.4: 长期记忆向量检索（OpenAI兼容协议；留空=降级FTS5关键词检索）
    embedding_model: str = ""
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    # v1.0.4: embedding 熔断冷却(秒); 服务不可用时窗口内不再重试, 直接降级 FTS
    embedding_breaker_cooldown_s: int = 300
    # v0.5: API服务（局域网测试，预留公网暴露）
    api_host: str = "127.0.0.1"  # 局域网测试设 0.0.0.0
    api_port: int = 8000
    api_auth_key: str = ""  # 留空时API拒绝启动，CLI不受影响
    # v0.9: 状态持久化后端(Redis); 空=默认 redis://localhost:6379, 不可用时降级内存
    redis_url: str = ""
    # v0.6: 私有知识库文档目录(导入扫描根)
    kb_dir: Path = Path("./knowledge")
    # v0.7: 可观测性(结构化日志)
    log_level: str = "INFO"          # DEBUG/INFO/WARNING/ERROR
    log_dir: Path = Path("./logs")   # 日志落盘目录(运行时产物，已 gitignore)
    # v1.0: 并发 worker 池
    worker_concurrency: int = 4      # 同时执行的 turn 数(>1 即并行; 资源/LLM配额够可上 100)
    turn_timeout_s: int = 600        # 单 turn 整体超时(防长任务占槽)
    queue_backend: str = "auto"      # auto(默认, Redis 可用则用) / redis / memory
    queue_maxsize: int = 1000        # 队列上限(背压): 满则提交返回 429, 防内存无限增长
    # v1.0: 评测数据集路径
    eval_dataset_path: Path = Path("./eval/gold_dataset.json")
    # v1.0.2: 评测单条超时(秒); 防单条卡在超长 LLM 请求拖垮整轮
    eval_item_timeout_s: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_model() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=s.model_name, api_key=s.api_key, base_url=s.base_url)