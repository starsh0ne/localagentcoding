# ============================================================
# config.py | v0.2诞生 → v0.3变更(+workspace_dir / +agent_recursion_limit)
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
	# v0.3新增: Coder工作区根目录（以启动目录为基准，可被WORKSPACE_DIR覆盖）
	workspace_dir: Path = Path("./workspace")
	# v0.3新增: 子Agent单次任务步数上限
	# （LangGraph默认25步，多文件任务必超——长任务的隐藏杀手）
	agent_recursion_limit: int = 80
@lru_cache
def get_settings() -> Settings:
	return Settings()
def build_model() -> ChatOpenAI:
	s = get_settings()
	return ChatOpenAI(model=s.model_name, api_key=s.api_key, base_url=s.base_url)