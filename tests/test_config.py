# ============================================================
# tests/test_config.py | 配置命名的回归测试(OPENAI_API_KEY vs API_KEY)
# 背景: api_key 字段原先只认 API_KEY, 而 README §五 与 .env.example 都把
#       密钥文档化为 OPENAI_API_KEY, 照文档配置的 .env 启动即
#       ValidationError: api_key Field required。
# 设计: 纯单元, 不依赖网络/LLM; Settings 带 lru_cache, 改环境后必须 cache_clear
# ============================================================
import pytest
from pydantic import ValidationError

from agent_pro.config import get_settings


@pytest.fixture
def fresh_settings():
    """每次用例前后清一次 lru_cache, 避免环境变量跨用例串味"""
    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


def test_reads_openai_api_key(fresh_settings, monkeypatch):
    """文档/.env.example 的写法: OPENAI_API_KEY 必须能读到"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-style")
    assert fresh_settings().api_key == "sk-openai-style"


def test_reads_legacy_api_key(fresh_settings, monkeypatch):
    """旧写法 API_KEY 仍需可用(向后兼容)"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("API_KEY", "sk-legacy")
    assert fresh_settings().api_key == "sk-legacy"


def test_requires_api_key_when_absent(fresh_settings, monkeypatch, tmp_path):
    """两个合法名字都不提供时应报缺失 —— 防止将来误加默认值把校验架空"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    # env_file=".env" 是相对当前工作目录解析, 换到临时目录即读不到仓库那份
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError):
        fresh_settings()


def test_reads_openai_base_url(fresh_settings, monkeypatch):
    """网关地址同理: 文档写的是 OPENAI_BASE_URL, 读不到就会打到 api.openai.com 上 401"""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw.internal.example/v1")
    assert fresh_settings().base_url == "https://gw.internal.example/v1"


def test_base_url_defaults_to_none(fresh_settings, monkeypatch, tmp_path):
    """未配置时应为 None(交给 SDK 走默认), 而不是被误设成空串"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # 隔离仓库 .env, 只测"完全未配置"这条路径
    assert fresh_settings().base_url is None
