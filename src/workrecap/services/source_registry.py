"""소스 레지스트리: 데이터 소스별 fetcher/normalizer 팩토리 관리."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from workrecap.services.protocols import DataSourceFetcher, DataSourceNormalizer

logger = logging.getLogger(__name__)


class SourceRegistry:
    """데이터 소스별 fetcher/normalizer 팩토리를 등록하고 조회."""

    def __init__(self) -> None:
        self._fetcher_factories: dict[str, Callable[..., DataSourceFetcher]] = {}
        self._normalizer_factories: dict[str, Callable[..., DataSourceNormalizer]] = {}

    def register(
        self,
        name: str,
        fetcher_factory: Callable[..., DataSourceFetcher],
        normalizer_factory: Callable[..., DataSourceNormalizer],
    ) -> None:
        """소스 이름과 팩토리 함수를 등록."""
        self._fetcher_factories[name] = fetcher_factory
        self._normalizer_factories[name] = normalizer_factory
        logger.debug("Registered source: %s", name)

    def get_fetcher(self, name: str, **kwargs: Any) -> DataSourceFetcher:
        """등록된 팩토리로 fetcher 인스턴스 생성."""
        if name not in self._fetcher_factories:
            raise KeyError(f"Unknown source: {name}")
        return self._fetcher_factories[name](**kwargs)

    def get_normalizer(self, name: str, **kwargs: Any) -> DataSourceNormalizer:
        """등록된 팩토리로 normalizer 인스턴스 생성."""
        if name not in self._normalizer_factories:
            raise KeyError(f"Unknown source: {name}")
        return self._normalizer_factories[name](**kwargs)

    def available_sources(self) -> list[str]:
        """등록된 소스 이름 목록."""
        return sorted(self._fetcher_factories.keys())
