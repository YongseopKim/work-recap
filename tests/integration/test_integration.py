"""Integration tests — real .env → GitHub API → LLM → full pipeline.

실행: pytest -m integration -x -v
날짜 지정: INTEGRATION_TEST_DATE=2026-02-14 pytest -m integration -x -v
"""

import json
from datetime import date, timedelta

import pytest

from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.orchestrator import OrchestratorService
from workrecap.services.summarizer import SummarizerService
from tests.integration.conftest import HAS_ENV

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_ENV, reason=".env file not found — skipping integration tests"),
]


class TestIntegrationPipeline:
    """단계별 파이프라인 통합 테스트. 정의 순서대로 실행, -x로 fail-fast."""

    def test_01_fetch(self, real_config, ghes_client, test_date):
        """Step 1: GitHub API에서 PR/Commit/Issue 데이터 fetch."""
        fetcher = FetcherService(real_config, ghes_client)
        results = fetcher.fetch(test_date)

        raw_dir = real_config.date_raw_dir(test_date)

        # 3개 파일 모두 존재
        for name in ("prs.json", "commits.json", "issues.json"):
            path = raw_dir / name
            assert path.exists(), f"{name} not created"

            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data, list), f"{name} should contain a JSON array"

        # results dict에 키 존재
        assert "prs" in results
        assert "commits" in results
        assert "issues" in results

    def test_02_normalize(self, real_config, test_date):
        """Step 2: raw 데이터를 Activity + DailyStats로 정규화."""
        normalizer = NormalizerService(real_config)
        activities_path, stats_path = normalizer.normalize(test_date)

        assert activities_path.exists(), "activities.jsonl not created"
        assert stats_path.exists(), "stats.json not created"

        # stats 검증
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        assert stats["date"] == test_date
        assert stats["authored_count"] >= 0
        assert stats["reviewed_count"] >= 0
        assert stats["commit_count"] >= 0

    def test_03_summarize_daily(self, real_config, llm_router, test_date):
        """Step 3: LLM을 호출하여 daily summary markdown 생성."""
        summarizer = SummarizerService(real_config, llm_router)
        summary_path = summarizer.daily(test_date)

        assert summary_path.exists(), "Daily summary markdown not created"

        content = summary_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, "Daily summary is empty"

    def test_04_full_pipeline(self, real_config, ghes_client, llm_router, test_date):
        """Step 4: 새로운 날짜로 전체 파이프라인 (fetch→normalize→summarize) 실행."""
        # test_date 하루 전 사용 → Step 1~3과 다른 날짜
        pipeline_date = (date.fromisoformat(test_date) - timedelta(days=1)).isoformat()

        fetcher = FetcherService(real_config, ghes_client)
        normalizer = NormalizerService(real_config)
        summarizer = SummarizerService(real_config, llm_router)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer)

        summary_path = orchestrator.run_daily(pipeline_date)

        # 모든 중간 산출물 존재 확인
        raw_dir = real_config.date_raw_dir(pipeline_date)
        assert (raw_dir / "prs.json").exists()
        assert (raw_dir / "commits.json").exists()
        assert (raw_dir / "issues.json").exists()

        norm_dir = real_config.date_normalized_dir(pipeline_date)
        assert (norm_dir / "activities.jsonl").exists()
        assert (norm_dir / "stats.json").exists()

        assert summary_path.exists()
        assert len(summary_path.read_text(encoding="utf-8").strip()) > 0
