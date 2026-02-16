from pathlib import Path
from unittest.mock import Mock, call

import pytest

from git_recap.exceptions import (
    FetchError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService


@pytest.fixture
def mocks():
    fetcher = Mock(spec=FetcherService)
    normalizer = Mock(spec=NormalizerService)
    summarizer = Mock(spec=SummarizerService)

    fetcher.fetch.return_value = Path("/data/raw/2025/02/16/prs.json")
    normalizer.normalize.return_value = (
        Path("/data/normalized/2025/02/16/activities.jsonl"),
        Path("/data/normalized/2025/02/16/stats.json"),
    )
    summarizer.daily.return_value = Path("/data/summaries/2025/daily/02-16.md")

    return {"fetcher": fetcher, "normalizer": normalizer, "summarizer": summarizer}


@pytest.fixture
def orchestrator(mocks):
    return OrchestratorService(mocks["fetcher"], mocks["normalizer"], mocks["summarizer"])


class TestRunDaily:
    def test_calls_three_steps_in_order(self, orchestrator, mocks):
        """fetch → normalize → summarize 순서로 호출."""
        orchestrator.run_daily("2025-02-16")

        mocks["fetcher"].fetch.assert_called_once_with("2025-02-16")
        mocks["normalizer"].normalize.assert_called_once_with("2025-02-16")
        mocks["summarizer"].daily.assert_called_once_with("2025-02-16")

        # 호출 순서 검증
        manager = Mock()
        manager.attach_mock(mocks["fetcher"].fetch, "fetch")
        manager.attach_mock(mocks["normalizer"].normalize, "normalize")
        manager.attach_mock(mocks["summarizer"].daily, "summarize")

        # 이미 호출됐으므로 다시 실행
        orchestrator.run_daily("2025-02-16")
        assert manager.mock_calls == [
            call.fetch("2025-02-16"),
            call.normalize("2025-02-16"),
            call.summarize("2025-02-16"),
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
    def test_processes_all_dates(self, orchestrator, mocks):
        results = orchestrator.run_range("2025-02-14", "2025-02-16")
        assert len(results) == 3
        assert all(r["status"] == "success" for r in results)
        assert results[0]["date"] == "2025-02-14"
        assert results[1]["date"] == "2025-02-15"
        assert results[2]["date"] == "2025-02-16"

    def test_failure_skips_and_continues(self, orchestrator, mocks):
        """특정 날짜 실패해도 나머지 계속 처리."""
        def fetch_side_effect(date_str):
            if date_str == "2025-02-15":
                raise FetchError("GHES down")
            return Path(f"/data/raw/{date_str}/prs.json")

        mocks["fetcher"].fetch.side_effect = fetch_side_effect

        results = orchestrator.run_range("2025-02-14", "2025-02-16")

        assert len(results) == 3
        assert results[0]["status"] == "success"
        assert results[1]["status"] == "failed"
        assert "fetch" in results[1]["error"]
        assert results[2]["status"] == "success"

    def test_result_format(self, orchestrator, mocks):
        results = orchestrator.run_range("2025-02-16", "2025-02-16")
        r = results[0]
        assert "date" in r
        assert "status" in r
        assert "path" in r

    def test_failed_result_format(self, orchestrator, mocks):
        mocks["fetcher"].fetch.side_effect = FetchError("error")
        results = orchestrator.run_range("2025-02-16", "2025-02-16")
        r = results[0]
        assert r["status"] == "failed"
        assert "error" in r
        assert "path" not in r

    def test_single_day(self, orchestrator, mocks):
        results = orchestrator.run_range("2025-02-16", "2025-02-16")
        assert len(results) == 1

    def test_empty_range(self, orchestrator, mocks):
        """since > until → 빈 결과."""
        results = orchestrator.run_range("2025-02-17", "2025-02-16")
        assert results == []
