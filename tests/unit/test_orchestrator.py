from pathlib import Path
from unittest.mock import Mock, call

import pytest

from workrecap.exceptions import (
    FetchError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)
from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.orchestrator import OrchestratorService
from workrecap.services.summarizer import SummarizerService


@pytest.fixture
def mocks():
    fetcher = Mock(spec=FetcherService)
    normalizer = Mock(spec=NormalizerService)
    summarizer = Mock(spec=SummarizerService)

    fetcher.fetch.return_value = {
        "prs": Path("/data/raw/2025/02/16/prs.json"),
        "commits": Path("/data/raw/2025/02/16/commits.json"),
        "issues": Path("/data/raw/2025/02/16/issues.json"),
    }
    normalizer.normalize.return_value = (
        Path("/data/normalized/2025/02/16/activities.jsonl"),
        Path("/data/normalized/2025/02/16/stats.json"),
    )
    summarizer.daily.return_value = Path("/data/summaries/2025/daily/02-16.md")

    # Range method defaults
    fetcher.fetch_range.return_value = []
    normalizer.normalize_range.return_value = []
    summarizer.daily_range.return_value = []

    return {"fetcher": fetcher, "normalizer": normalizer, "summarizer": summarizer}


@pytest.fixture
def mock_config():
    config = Mock()
    config.daily_summary_path.side_effect = lambda d: Path(
        f"/data/summaries/{d[:4]}/daily/{d[5:7]}-{d[8:10]}.md"
    )
    return config


@pytest.fixture
def orchestrator(mocks):
    return OrchestratorService(mocks["fetcher"], mocks["normalizer"], mocks["summarizer"])


@pytest.fixture
def orchestrator_with_config(mocks, mock_config):
    return OrchestratorService(
        mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
    )


class TestRunDaily:
    def test_calls_three_steps_in_order(self, orchestrator, mocks):
        """fetch → normalize → summarize 순서로 호출."""
        orchestrator.run_daily("2025-02-16")

        mocks["fetcher"].fetch.assert_called_once_with("2025-02-16", types=None, progress=None)
        mocks["normalizer"].normalize.assert_called_once_with("2025-02-16", progress=None)
        mocks["summarizer"].daily.assert_called_once_with("2025-02-16", progress=None)

        # 호출 순서 검증
        manager = Mock()
        manager.attach_mock(mocks["fetcher"].fetch, "fetch")
        manager.attach_mock(mocks["normalizer"].normalize, "normalize")
        manager.attach_mock(mocks["summarizer"].daily, "summarize")

        # 이미 호출됐으므로 다시 실행
        orchestrator.run_daily("2025-02-16")
        assert manager.mock_calls == [
            call.fetch("2025-02-16", types=None, progress=None),
            call.normalize("2025-02-16", progress=None),
            call.summarize("2025-02-16", progress=None),
        ]

    def test_returns_summary_path(self, orchestrator):
        result = orchestrator.run_daily("2025-02-16")
        assert result == Path("/data/summaries/2025/daily/02-16.md")

    def test_fetch_failure(self, orchestrator, mocks):
        mocks["fetcher"].fetch.side_effect = FetchError("GHES timeout")

        with pytest.raises(StepFailedError) as exc_info:
            orchestrator.run_daily("2025-02-16")

        assert exc_info.value.step == "fetch"
        assert isinstance(exc_info.value.cause, FetchError)
        # normalize, summarize는 호출되지 않아야 함
        mocks["normalizer"].normalize.assert_not_called()
        mocks["summarizer"].daily.assert_not_called()

    def test_normalize_failure_preserves_raw(self, orchestrator, mocks):
        mocks["normalizer"].normalize.side_effect = NormalizeError("parse error")

        with pytest.raises(StepFailedError) as exc_info:
            orchestrator.run_daily("2025-02-16")

        assert exc_info.value.step == "normalize"
        # fetch는 이미 호출됨 (raw 보존)
        mocks["fetcher"].fetch.assert_called_once()
        mocks["summarizer"].daily.assert_not_called()

    def test_summarize_failure_preserves_normalized(self, orchestrator, mocks):
        mocks["summarizer"].daily.side_effect = SummarizeError("LLM error")

        with pytest.raises(StepFailedError) as exc_info:
            orchestrator.run_daily("2025-02-16")

        assert exc_info.value.step == "summarize"
        # fetch + normalize 이미 호출됨
        mocks["fetcher"].fetch.assert_called_once()
        mocks["normalizer"].normalize.assert_called_once()


class TestRunRange:
    """Tests for optimized run_range using bulk fetch_range/normalize_range/daily_range."""

    def test_processes_all_dates(self, orchestrator_with_config, mocks):
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-16")

        assert len(results) == 3
        assert all(r["status"] == "success" for r in results)
        assert results[0]["date"] == "2025-02-14"
        assert results[1]["date"] == "2025-02-15"
        assert results[2]["date"] == "2025-02-16"

    def test_failure_propagates_from_fetch(self, orchestrator_with_config, mocks):
        """Fetch failure for a date → that date fails."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "failed", "error": "GHES down"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-16")

        assert len(results) == 3
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert "fetch" in results[1]["error"]
        assert results[2]["status"] == "success"

    def test_result_format(self, orchestrator_with_config, mocks):
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-16", "2025-02-16")
        r = results[0]
        assert "date" in r
        assert "status" in r
        assert "path" in r

    def test_failed_result_format(self, orchestrator_with_config, mocks):
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-16", "status": "failed", "error": "GHES error"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-16", "status": "failed", "error": "no raw data"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-16", "status": "failed", "error": "no normalized data"},
        ]

        results = orchestrator_with_config.run_range("2025-02-16", "2025-02-16")
        r = results[0]
        assert r["status"] == "failed"
        assert "error" in r
        assert "path" not in r

    def test_single_day(self, orchestrator_with_config, mocks):
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-16", "2025-02-16")
        assert len(results) == 1

    def test_empty_range(self, orchestrator_with_config, mocks):
        """since > until → 빈 결과."""
        results = orchestrator_with_config.run_range("2025-02-17", "2025-02-16")
        assert results == []


class TestRunRangeOptimized:
    """Tests verifying that run_range uses bulk operations correctly."""

    def test_uses_fetch_range_not_fetch(self, orchestrator_with_config, mocks):
        """fetch_range is called, per-date fetch is NOT called."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
        ]

        orchestrator_with_config.run_range("2025-02-14", "2025-02-15")

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-15", types=None, force=False, progress=None
        )
        mocks["fetcher"].fetch.assert_not_called()

    def test_calls_phases_in_order(self, orchestrator_with_config, mocks):
        """fetch_range → normalize_range → daily_range in sequence."""
        call_order = []
        mocks["fetcher"].fetch_range.side_effect = (
            lambda s, u, types=None, force=False, progress=None: (
                call_order.append("fetch_range") or []
            )
        )
        mocks["normalizer"].normalize_range.side_effect = (
            lambda s, u, force=False, progress=None, max_workers=1, batch=False: (
                call_order.append("normalize_range") or []
            )
        )
        mocks["summarizer"].daily_range.side_effect = (
            lambda s, u, force=False, progress=None, max_workers=1, batch=False: (
                call_order.append("daily_range") or []
            )
        )

        orchestrator_with_config.run_range("2025-02-14", "2025-02-15")

        assert call_order == ["fetch_range", "normalize_range", "daily_range"]

    def test_all_success_includes_path(self, orchestrator_with_config, mocks, mock_config):
        """All phases succeed → result includes path from config."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-16")

        assert all(r["status"] == "success" for r in results)
        assert results[0]["path"] == str(Path("/data/summaries/2025/daily/02-14.md"))
        assert results[1]["path"] == str(Path("/data/summaries/2025/daily/02-15.md"))
        assert results[2]["path"] == str(Path("/data/summaries/2025/daily/02-16.md"))

    def test_fetch_failure_propagates(self, orchestrator_with_config, mocks):
        """Fetch failure → error mentions 'fetch'."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "failed", "error": "GHES timeout"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-14")

        assert results[0]["status"] == "failed"
        assert "fetch" in results[0]["error"]

    def test_normalize_failure_propagates(self, orchestrator_with_config, mocks):
        """Normalize failure → error mentions 'normalize'."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "failed", "error": "parse error"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-14")

        assert results[0]["status"] == "failed"
        assert "normalize" in results[0]["error"]

    def test_summarize_failure_propagates(self, orchestrator_with_config, mocks):
        """Summarize failure → error mentions 'summarize'."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "failed", "error": "LLM error"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-14")

        assert results[0]["status"] == "failed"
        assert "summarize" in results[0]["error"]

    def test_mixed_results(self, orchestrator_with_config, mocks):
        """Multi-date with different failures per phase."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "failed", "error": "rate limit"},
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "failed", "error": "bad data"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-16")

        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert "fetch" in results[1]["error"]
        assert results[2]["status"] == "failed"
        assert "normalize" in results[2]["error"]

    def test_result_format_success(self, orchestrator_with_config, mocks):
        """Success result has date, status, path."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-16", "2025-02-16")
        r = results[0]

        assert r["date"] == "2025-02-16"
        assert r["status"] == "success"
        assert "path" in r
        assert "error" not in r

    def test_single_day_range(self, orchestrator_with_config, mocks):
        """Range of 1 day works."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-16", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-16", "2025-02-16")
        assert len(results) == 1
        assert results[0]["status"] == "success"

    def test_empty_range_results(self, orchestrator_with_config, mocks):
        """Empty results when no dates in range."""
        results = orchestrator_with_config.run_range("2025-02-17", "2025-02-16")
        assert results == []
        mocks["fetcher"].fetch_range.assert_not_called()


class TestRunRangeSkipped:
    """Tests that all-skipped phases produce skipped status in merged results."""

    def test_all_phases_skipped(self, orchestrator_with_config, mocks):
        """3개 phase 모두 skipped → merged 결과도 skipped."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "skipped"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "skipped"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "skipped"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-15")
        assert len(results) == 2
        assert all(r["status"] == "skipped" for r in results)
        assert "path" not in results[0]

    def test_mixed_skipped_and_success(self, orchestrator_with_config, mocks):
        """일부 skipped + 일부 success → 각각 올바른 status."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
            {"date": "2025-02-15", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-15")
        assert results[0]["status"] == "skipped"
        assert results[1]["status"] == "success"
        assert "path" in results[1]

    def test_partial_skipped_counts_as_success(self, orchestrator_with_config, mocks):
        """fetch skipped + normalize/summarize success → success (일부만 재처리)."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "skipped"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]

        results = orchestrator_with_config.run_range("2025-02-14", "2025-02-14")
        assert results[0]["status"] == "success"


class TestRunRangeForcePassthrough:
    """Tests that force=True is passed through to all 3 services."""

    def test_force_passed_to_all_services(self, mocks, mock_config):
        """force=True → fetch_range, normalize_range, daily_range 모두 force=True 전달."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-14", force=True)

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", types=None, force=True, progress=None
        )
        mocks["normalizer"].normalize_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=True, progress=None, max_workers=1, batch=False
        )
        mocks["summarizer"].daily_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=True, progress=None, max_workers=1, batch=False
        )

    def test_force_false_by_default(self, mocks, mock_config):
        """force 미지정 시 기본값 False 전달."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-14")

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", types=None, force=False, progress=None
        )
        mocks["normalizer"].normalize_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=False, progress=None, max_workers=1, batch=False
        )
        mocks["summarizer"].daily_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=False, progress=None, max_workers=1, batch=False
        )


class TestRunDailyTypesPassthrough:
    """Tests that types is passed through to fetcher in run_daily."""

    def test_types_passed_to_fetch(self, orchestrator, mocks):
        """types={\"prs\"} → fetch에 types 전달."""
        orchestrator.run_daily("2025-02-16", types={"prs"})
        mocks["fetcher"].fetch.assert_called_once_with("2025-02-16", types={"prs"}, progress=None)

    def test_types_none_by_default(self, orchestrator, mocks):
        """types 미지정 → None 전달."""
        orchestrator.run_daily("2025-02-16")
        mocks["fetcher"].fetch.assert_called_once_with("2025-02-16", types=None, progress=None)


class TestRunRangeTypesPassthrough:
    """Tests that types is passed through to fetcher in run_range."""

    def test_types_passed_to_fetch_range(self, mocks, mock_config):
        """types={\"commits\"} → fetch_range에 types 전달."""
        mocks["fetcher"].fetch_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["normalizer"].normalize_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]
        mocks["summarizer"].daily_range.return_value = [
            {"date": "2025-02-14", "status": "success"},
        ]

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-14", types={"commits"})

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", types={"commits"}, force=False, progress=None
        )

    def test_types_with_force(self, mocks, mock_config):
        """types + force 동시 전달."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-14", force=True, types={"issues"})

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", types={"issues"}, force=True, progress=None
        )


class TestProgressCallback:
    """Tests that progress callback is passed through to all services."""

    def test_run_daily_passes_progress(self, mocks, mock_config):
        """run_daily → fetch/normalize/daily에 progress 전달."""
        cb = Mock()
        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_daily("2025-02-16", progress=cb)

        mocks["fetcher"].fetch.assert_called_once_with("2025-02-16", types=None, progress=cb)
        mocks["normalizer"].normalize.assert_called_once_with("2025-02-16", progress=cb)
        mocks["summarizer"].daily.assert_called_once_with("2025-02-16", progress=cb)
        assert cb.call_count >= 3  # at least 3 phase messages

    def test_run_range_passes_progress(self, mocks, mock_config):
        """run_range → fetch_range/normalize_range/daily_range에 progress 전달."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        cb = Mock()
        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-14", progress=cb)

        mocks["fetcher"].fetch_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", types=None, force=False, progress=cb
        )
        mocks["normalizer"].normalize_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=False, progress=cb, max_workers=1, batch=False
        )
        mocks["summarizer"].daily_range.assert_called_once_with(
            "2025-02-14", "2025-02-14", force=False, progress=cb, max_workers=1, batch=False
        )
        assert cb.call_count >= 3  # Phase 1/3, 2/3, 3/3


# ── max_workers passthrough ──


class TestRunRangeMaxWorkers:
    def test_max_workers_passed_to_services(self, mocks, mock_config):
        """run_range(max_workers=5) → normalize_range/daily_range에 전달."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-16", max_workers=5)

        _, kwargs = mocks["normalizer"].normalize_range.call_args
        assert kwargs.get("max_workers") == 5

        _, kwargs = mocks["summarizer"].daily_range.call_args
        assert kwargs.get("max_workers") == 5

    def test_max_workers_default_1(self, mocks, mock_config):
        """run_range() 기본 max_workers=1."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-16")

        _, kwargs = mocks["normalizer"].normalize_range.call_args
        assert kwargs.get("max_workers") == 1

        _, kwargs = mocks["summarizer"].daily_range.call_args
        assert kwargs.get("max_workers") == 1


# ── batch passthrough ──


class TestRunRangeBatch:
    def test_batch_passed_to_services(self, mocks, mock_config):
        """run_range(batch=True) → normalize_range/daily_range에 batch=True 전달."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-16", batch=True)

        _, kwargs = mocks["normalizer"].normalize_range.call_args
        assert kwargs.get("batch") is True

        _, kwargs = mocks["summarizer"].daily_range.call_args
        assert kwargs.get("batch") is True

    def test_batch_default_false(self, mocks, mock_config):
        """run_range() 기본 batch=False → normalize/summarize에도 batch 미전달 or False."""
        mocks["fetcher"].fetch_range.return_value = []
        mocks["normalizer"].normalize_range.return_value = []
        mocks["summarizer"].daily_range.return_value = []

        orchestrator = OrchestratorService(
            mocks["fetcher"], mocks["normalizer"], mocks["summarizer"], config=mock_config
        )
        orchestrator.run_range("2025-02-14", "2025-02-16")

        _, kwargs = mocks["normalizer"].normalize_range.call_args
        assert kwargs.get("batch", False) is False

        _, kwargs = mocks["summarizer"].daily_range.call_args
        assert kwargs.get("batch", False) is False
