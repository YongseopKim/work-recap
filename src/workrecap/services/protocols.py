"""데이터 소스 fetcher/normalizer Protocol 정의."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DataSourceFetcher(Protocol):
    """데이터 소스별 fetcher가 구현해야 할 인터페이스."""

    @property
    def source_name(self) -> str: ...

    def fetch(
        self,
        target_date: str,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Path]: ...

    def fetch_range(
        self,
        since: str,
        until: str,
        types: set[str] | None = None,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]: ...


@runtime_checkable
class DataSourceNormalizer(Protocol):
    """데이터 소스별 normalizer가 구현해야 할 인터페이스."""

    @property
    def source_name(self) -> str: ...

    def normalize(
        self,
        target_date: str,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, Path]: ...

    def normalize_range(
        self,
        since: str,
        until: str,
        force: bool = False,
        progress: Callable[[str], None] | None = None,
        max_workers: int = 1,
    ) -> list[dict]: ...
