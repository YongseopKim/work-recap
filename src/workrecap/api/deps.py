"""FastAPI 의존성 주입."""

from functools import lru_cache

from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()


def get_job_store() -> JobStore:
    return JobStore(get_config())
