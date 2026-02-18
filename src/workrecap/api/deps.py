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
    """Create an LLMRouter instance from AppConfig."""
    from workrecap.infra.llm_router import LLMRouter
    from workrecap.infra.provider_config import ProviderConfig
    from workrecap.infra.usage_tracker import UsageTracker
    from workrecap.infra.pricing import PricingTable

    if config is None:
        config = get_config()

    config_path = config.provider_config_path
    if config_path.exists():
        pc = ProviderConfig(config_path=config_path)
    else:
        pc = ProviderConfig(config_path=None, fallback_config=config)

    tracker = UsageTracker(pricing=PricingTable())
    return LLMRouter(pc, usage_tracker=tracker)
