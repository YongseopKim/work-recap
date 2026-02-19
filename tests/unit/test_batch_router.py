"""LLMRouter batch 메서드 단위 테스트."""

import pytest

from workrecap.infra.llm_router import LLMRouter
from workrecap.infra.provider_config import ProviderConfig
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
from workrecap.infra.providers.base import LLMProvider
from workrecap.models import TokenUsage


class FakeBatchProvider(LLMProvider, BatchCapable):
    """Test double: a provider that supports batch."""

    def __init__(self):
        self._submitted = []
        self._status = BatchStatus.COMPLETED
        self._results = []

    @property
    def provider_name(self) -> str:
        return "fake"

    def chat(self, model, system_prompt, user_content, **kwargs):
        return "response", TokenUsage(call_count=1)

    def submit_batch(self, requests):
        self._submitted = requests
        return "batch-fake-123"

    def get_batch_status(self, batch_id):
        return self._status

    def get_batch_results(self, batch_id):
        return self._results


class FakeNonBatchProvider(LLMProvider):
    """Test double: a provider that does NOT support batch."""

    @property
    def provider_name(self) -> str:
        return "fake_no_batch"

    def chat(self, model, system_prompt, user_content, **kwargs):
        return "response", TokenUsage(call_count=1)


@pytest.fixture
def config(tmp_path):
    """ProviderConfig with a test TOML."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("""
[strategy]
mode = "fixed"

[providers.fake]
api_key = "test"

[tasks.daily]
provider = "fake"
model = "fake-model"
max_tokens = 2048
""")
    return ProviderConfig(toml_path)


class TestLLMRouterBatch:
    def test_submit_batch(self, config):
        provider = FakeBatchProvider()
        router = LLMRouter(config)
        router._providers["fake"] = provider

        raw_requests = [
            {
                "custom_id": "day-2026-01-01",
                "system_prompt": "Summarize",
                "user_content": "Activity data...",
                "json_mode": True,
            },
            {
                "custom_id": "day-2026-01-02",
                "system_prompt": "Summarize",
                "user_content": "More data...",
            },
        ]

        batch_id = router.submit_batch(raw_requests, task="daily")
        assert batch_id == "batch-fake-123"
        assert len(provider._submitted) == 2

        r1 = provider._submitted[0]
        assert isinstance(r1, BatchRequest)
        assert r1.custom_id == "day-2026-01-01"
        assert r1.model == "fake-model"
        assert r1.json_mode is True
        assert r1.max_tokens == 2048  # from task config

        r2 = provider._submitted[1]
        assert r2.json_mode is False

    def test_submit_batch_explicit_max_tokens(self, config):
        """명시적 max_tokens가 task config보다 우선."""
        provider = FakeBatchProvider()
        router = LLMRouter(config)
        router._providers["fake"] = provider

        raw_requests = [
            {
                "custom_id": "req-1",
                "system_prompt": "s",
                "user_content": "u",
                "max_tokens": 500,
            },
        ]

        router.submit_batch(raw_requests, task="daily")
        assert provider._submitted[0].max_tokens == 500

    def test_submit_batch_non_batch_provider_raises(self, config):
        """BatchCapable이 아닌 provider는 ValueError."""
        provider = FakeNonBatchProvider()
        router = LLMRouter(config)
        router._providers["fake"] = provider

        with pytest.raises(ValueError, match="does not support batch"):
            router.submit_batch(
                [{"custom_id": "r1", "system_prompt": "s", "user_content": "u"}], task="daily"
            )

    def test_get_batch_status(self, config):
        provider = FakeBatchProvider()
        provider._status = BatchStatus.PROCESSING
        router = LLMRouter(config)
        router._providers["fake"] = provider

        status = router.get_batch_status("batch-123", task="daily")
        assert status == BatchStatus.PROCESSING

    def test_get_batch_results(self, config):
        provider = FakeBatchProvider()
        provider._results = [
            BatchResult(custom_id="r1", content="ok", usage=TokenUsage(call_count=1)),
        ]
        router = LLMRouter(config)
        router._providers["fake"] = provider

        results = router.get_batch_results("batch-123", task="daily")
        assert len(results) == 1
        assert results[0].content == "ok"

    def test_wait_for_batch_immediate(self, config):
        """이미 완료된 batch는 즉시 결과 반환."""
        provider = FakeBatchProvider()
        provider._status = BatchStatus.COMPLETED
        provider._results = [
            BatchResult(custom_id="r1", content="done"),
        ]
        router = LLMRouter(config)
        router._providers["fake"] = provider

        results = router.wait_for_batch("batch-123", task="daily", timeout=5, poll_interval=0.1)
        assert len(results) == 1

    def test_wait_for_batch_polls(self, config):
        """PROCESSING → COMPLETED 전환 시 polling."""
        provider = FakeBatchProvider()
        call_count = 0

        def transitioning_status(batch_id):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                provider._status = BatchStatus.COMPLETED
            return provider._status

        provider.get_batch_status = transitioning_status
        provider._status = BatchStatus.PROCESSING
        provider._results = [BatchResult(custom_id="r1", content="done")]
        router = LLMRouter(config)
        router._providers["fake"] = provider

        results = router.wait_for_batch("batch-123", task="daily", timeout=10, poll_interval=0.01)
        assert len(results) == 1
        assert call_count >= 3

    def test_wait_for_batch_timeout(self, config):
        """timeout 시 TimeoutError."""
        provider = FakeBatchProvider()
        provider._status = BatchStatus.PROCESSING
        router = LLMRouter(config)
        router._providers["fake"] = provider

        with pytest.raises(TimeoutError, match="timed out"):
            router.wait_for_batch("batch-123", task="daily", timeout=0.1, poll_interval=0.02)

    def test_wait_for_batch_failed(self, config):
        """FAILED status → RuntimeError."""
        provider = FakeBatchProvider()
        provider._status = BatchStatus.FAILED
        router = LLMRouter(config)
        router._providers["fake"] = provider

        with pytest.raises(RuntimeError, match="failed"):
            router.wait_for_batch("batch-123", task="daily", timeout=5, poll_interval=0.01)
