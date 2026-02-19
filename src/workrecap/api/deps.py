"""FastAPI 의존성 주입."""

from functools import lru_cache

from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()


def get_job_store() -> JobStore:
    return JobStore(get_config())


def get_llm_router(config: AppConfig | None = None):
    """Create an LLMRouter instance from ProviderConfig TOML."""
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.infra.provider_config import ProviderConfig
    from workrecap.infra.usage_tracker import UsageTracker
    from workrecap.infra.pricing import PricingTable

    if config is None:
        config = get_config()

    pc = ProviderConfig(config.provider_config_path)
    tracker = UsageTracker(pricing=PricingTable())
    return LLMRouter(pc, usage_tracker=tracker)
