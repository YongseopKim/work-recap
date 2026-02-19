"""SourceRegistry 테스트."""

import pytest

from workrecap.services.source_registry import SourceRegistry


class _DummyFetcher:
    @property
    def source_name(self) -> str:
        return "dummy"

    def fetch(self, target_date, types=None, progress=None):
        return {}

    def fetch_range(self, since, until, types=None, force=False, progress=None):
        return []


class _DummyNormalizer:
    @property
    def source_name(self) -> str:
        return "dummy"

    def normalize(self, target_date, progress=None):
        from pathlib import Path

        return Path("/a"), Path("/b")

    def normalize_range(self, since, until, force=False, progress=None, max_workers=1):
        return []


class TestSourceRegistry:
    def test_register_and_available(self):
        reg = SourceRegistry()
        reg.register("dummy", _DummyFetcher, _DummyNormalizer)
        assert "dummy" in reg.available_sources()

    def test_get_fetcher(self):
        reg = SourceRegistry()
        reg.register("dummy", _DummyFetcher, _DummyNormalizer)
        fetcher = reg.get_fetcher("dummy")
        assert fetcher.source_name == "dummy"

    def test_get_normalizer(self):
        reg = SourceRegistry()
        reg.register("dummy", _DummyFetcher, _DummyNormalizer)
        normalizer = reg.get_normalizer("dummy")
        assert normalizer.source_name == "dummy"

    def test_unknown_source_raises(self):
        reg = SourceRegistry()
        with pytest.raises(KeyError, match="Unknown source"):
            reg.get_fetcher("nonexistent")

    def test_multiple_sources(self):
        reg = SourceRegistry()
        reg.register("alpha", _DummyFetcher, _DummyNormalizer)
        reg.register("beta", _DummyFetcher, _DummyNormalizer)
        assert reg.available_sources() == ["alpha", "beta"]
