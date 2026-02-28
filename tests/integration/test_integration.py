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
        activities_path, stats_path, _, _ = normalizer.normalize(test_date)

        assert activities_path.exists(), "activities.jsonl not created"
        assert stats_path.exists(), "stats.json not created"

        # stats 검증 (nested DailyStats 구조: github.authored_count 등)
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        assert stats["date"] == test_date
        gh = stats["github"]
        assert gh["authored_count"] >= 0
        assert gh["reviewed_count"] >= 0
        assert gh["commit_count"] >= 0

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


class TestPromptCaching:
    """cache_system_prompt=True로 동일 system prompt 2회 호출 시 cache hit 확인."""

    def test_cache_read_tokens_on_second_call(self, real_config):
        """Same system prompt called twice → second call should have cache_read_tokens > 0.

        Tests at the provider level (bypassing router/escalation) to isolate
        Anthropic prompt caching behavior directly.
        """
        from workrecap.infra.provider_config import ProviderConfig
        from workrecap.infra.providers.anthropic_provider import AnthropicProvider

        pc = ProviderConfig(real_config.provider_config_path)
        api_key = pc.providers["anthropic"].api_key
        provider = AnthropicProvider(api_key=api_key)

        # Anthropic caching minimum: empirically ~2048 tokens for Sonnet 4.6
        # (docs say 1024, but actual threshold is higher).
        # Generate diverse text (~3000 tokens) to safely exceed the minimum.
        lines = []
        for i in range(150):
            lines.append(
                f"Section {i}: The engineer reviewed PR #{i + 100} involving "
                f"refactoring of the authentication module to support OAuth2 "
                f"bearer tokens and JWT validation with RSA-256 signatures. "
                f"The changes touched {i + 5} files across {i + 2} directories."
            )
        system_prompt = (
            "You are a helpful assistant that summarizes software engineering work. "
            "Respond with a single short sentence.\n\n" + "\n".join(lines)
        )
        # Find an Anthropic task model for this test (we're testing Anthropic caching)
        anthropic_model = None
        for task in ("monthly", "yearly", "enrich", "daily", "weekly", "query"):
            try:
                tc = pc.get_task_config(task)
                if tc.provider == "anthropic":
                    anthropic_model = tc.model
                    break
            except KeyError:
                continue
        if not anthropic_model:
            pytest.skip("No Anthropic task configured — cannot test prompt caching")
        model = anthropic_model

        # 1st call — cache miss (should populate cache, cache_write > 0)
        text1, usage1 = provider.chat(
            model,
            system_prompt,
            "Summarize: fixed a login bug.",
            cache_system_prompt=True,
        )
        print(
            f"\n  1st call: prompt={usage1.prompt_tokens} "
            f"cache_read={usage1.cache_read_tokens} "
            f"cache_write={usage1.cache_write_tokens}"
        )

        # 2nd call — same system prompt, different user → cache hit expected
        text2, usage2 = provider.chat(
            model,
            system_prompt,
            "Summarize: added unit tests.",
            cache_system_prompt=True,
        )
        print(
            f"  2nd call: prompt={usage2.prompt_tokens} "
            f"cache_read={usage2.cache_read_tokens} "
            f"cache_write={usage2.cache_write_tokens}"
        )

        # 1st call should write to cache
        assert usage1.cache_write_tokens > 0, (
            f"Expected cache_write_tokens > 0 on 1st call, got {usage1.cache_write_tokens}"
        )
        # 2nd call should read from cache
        assert usage2.cache_read_tokens > 0, (
            f"Expected cache_read_tokens > 0 on 2nd call, got {usage2.cache_read_tokens}"
        )
