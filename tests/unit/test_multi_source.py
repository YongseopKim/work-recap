"""SP-6: 멀티소스 통합 테스트 — Config, Orchestrator, CLI."""

from unittest.mock import MagicMock

from workrecap.services.orchestrator import OrchestratorService


class TestAppConfigEnabledSources:
    def test_default_enabled_sources(self, test_config):
        """enabled_sources 기본값은 ['github']."""
        assert test_config.enabled_sources == ["github"]


class TestOrchestratorMultiSource:
    def test_single_fetcher_backward_compat(self):
        """단일 fetcher/normalizer 전달 시 dict 래핑."""
        fetcher = MagicMock()
        normalizer = MagicMock()
        summarizer = MagicMock()
        orch = OrchestratorService(fetcher, normalizer, summarizer)
        assert orch._fetchers == {"github": fetcher}
        assert orch._normalizers == {"github": normalizer}

    def test_dict_fetchers(self):
        """dict 형태 fetcher/normalizer 전달."""
        f1 = MagicMock()
        f2 = MagicMock()
        n1 = MagicMock()
        n2 = MagicMock()
        summarizer = MagicMock()
        orch = OrchestratorService(
            {"github": f1, "confluence": f2},
            {"github": n1, "confluence": n2},
            summarizer,
        )
        assert len(orch._fetchers) == 2
        assert len(orch._normalizers) == 2
        assert "confluence" in orch._fetchers

    def test_run_daily_uses_default_fetcher(self):
        """run_daily는 기본 fetcher(첫 번째)를 사용."""
        fetcher = MagicMock()
        fetcher.fetch.return_value = {}
        normalizer = MagicMock()
        normalizer.normalize.return_value = ("a", "b", [], None)
        summarizer = MagicMock()
        summarizer.daily.return_value = "/path/summary.md"

        orch = OrchestratorService(fetcher, normalizer, summarizer)
        orch.run_daily("2025-02-16")

        fetcher.fetch.assert_called_once()
        normalizer.normalize.assert_called_once()
        summarizer.daily.assert_called_once()


class TestCLISourceTypes:
    def test_source_types_mapping(self):
        """SOURCE_TYPES에 github 매핑 존재."""
        from workrecap.cli.main import SOURCE_TYPES

        assert "github" in SOURCE_TYPES
        assert SOURCE_TYPES["github"] == {"prs", "commits", "issues"}
