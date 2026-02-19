"""DataSourceFetcher / DataSourceNormalizer Protocol 준수 테스트."""

from workrecap.services.protocols import DataSourceFetcher, DataSourceNormalizer


class TestProtocolConformance:
    def test_fetcher_is_data_source_fetcher(self, test_config):
        """FetcherService는 DataSourceFetcher Protocol을 충족."""
        from unittest.mock import MagicMock

        from workrecap.services.fetcher import FetcherService

        ghes = MagicMock()
        fetcher = FetcherService(test_config, ghes)
        assert isinstance(fetcher, DataSourceFetcher)

    def test_normalizer_is_data_source_normalizer(self, test_config):
        """NormalizerService는 DataSourceNormalizer Protocol을 충족."""
        from workrecap.services.normalizer import NormalizerService

        normalizer = NormalizerService(test_config)
        assert isinstance(normalizer, DataSourceNormalizer)

    def test_fetcher_source_name(self, test_config):
        """FetcherService.source_name == 'github'."""
        from unittest.mock import MagicMock

        from workrecap.services.fetcher import FetcherService

        ghes = MagicMock()
        fetcher = FetcherService(test_config, ghes)
        assert fetcher.source_name == "github"

    def test_normalizer_source_name(self, test_config):
        """NormalizerService.source_name == 'github'."""
        from workrecap.services.normalizer import NormalizerService

        normalizer = NormalizerService(test_config)
        assert normalizer.source_name == "github"

    def test_non_conforming_class_fails(self):
        """Protocol 미충족 클래스 → isinstance 실패."""

        class NotAFetcher:
            pass

        assert not isinstance(NotAFetcher(), DataSourceFetcher)
        assert not isinstance(NotAFetcher(), DataSourceNormalizer)
