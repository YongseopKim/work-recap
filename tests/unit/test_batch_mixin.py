"""batch_mixin 모듈 단위 테스트: BatchRequest, BatchResult, BatchStatus, BatchCapable."""

from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
from workrecap.models import TokenUsage


class TestBatchRequest:
    def test_defaults(self):
        req = BatchRequest(
            custom_id="req-1",
            model="claude-sonnet-4-5-20250929",
            system_prompt="You are helpful.",
            user_content="Hello",
        )
        assert req.custom_id == "req-1"
        assert req.model == "claude-sonnet-4-5-20250929"
        assert req.json_mode is False
        assert req.max_tokens is None
        assert req.cache_system_prompt is False

    def test_all_fields(self):
        req = BatchRequest(
            custom_id="req-2",
            model="gpt-4o",
            system_prompt="sys",
            user_content="usr",
            json_mode=True,
            max_tokens=500,
            cache_system_prompt=True,
        )
        assert req.json_mode is True
        assert req.max_tokens == 500
        assert req.cache_system_prompt is True


class TestBatchResult:
    def test_success(self):
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30, call_count=1)
        result = BatchResult(custom_id="req-1", content="Hello!", usage=usage)
        assert result.content == "Hello!"
        assert result.error is None
        assert result.usage.total_tokens == 30

    def test_error(self):
        result = BatchResult(custom_id="req-1", error="Rate limit exceeded")
        assert result.content is None
        assert result.usage is None
        assert result.error == "Rate limit exceeded"


class TestBatchStatus:
    def test_values(self):
        assert BatchStatus.SUBMITTED == "submitted"
        assert BatchStatus.PROCESSING == "processing"
        assert BatchStatus.COMPLETED == "completed"
        assert BatchStatus.FAILED == "failed"
        assert BatchStatus.EXPIRED == "expired"

    def test_is_terminal(self):
        assert BatchStatus.COMPLETED.is_terminal is True
        assert BatchStatus.FAILED.is_terminal is True
        assert BatchStatus.EXPIRED.is_terminal is True
        assert BatchStatus.SUBMITTED.is_terminal is False
        assert BatchStatus.PROCESSING.is_terminal is False


class TestBatchCapable:
    def test_is_abstract(self):
        """BatchCapable은 직접 인스턴스화할 수 없음."""
        try:
            BatchCapable()  # type: ignore[abstract]
            assert False, "Should not be instantiable"
        except TypeError:
            pass

    def test_isinstance_check(self):
        """구체 클래스가 BatchCapable로 인식되는지 확인."""

        class FakeProvider(BatchCapable):
            def submit_batch(self, requests):
                return "batch-123"

            def get_batch_status(self, batch_id):
                return BatchStatus.COMPLETED

            def get_batch_results(self, batch_id):
                return []

        provider = FakeProvider()
        assert isinstance(provider, BatchCapable)
