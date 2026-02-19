"""Anthropic provider batch 기능 단위 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from workrecap.infra.providers.anthropic_provider import AnthropicProvider
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchStatus,
)


@pytest.fixture
def provider():
    with patch("workrecap.infra.providers.anthropic_provider.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        p = AnthropicProvider(api_key="test-key")
        yield p


class TestAnthropicBatchCapable:
    def test_is_batch_capable(self, provider):
        assert isinstance(provider, BatchCapable)

    def test_submit_batch(self, provider):
        """submit_batch → client.messages.batches.create 호출."""
        mock_batch = MagicMock()
        mock_batch.id = "msgbatch_abc123"
        provider._client.messages.batches.create.return_value = mock_batch

        requests = [
            BatchRequest(
                custom_id="req-1",
                model="claude-sonnet-4-5-20250929",
                system_prompt="You are helpful.",
                user_content="Hello",
                max_tokens=1024,
            ),
            BatchRequest(
                custom_id="req-2",
                model="claude-sonnet-4-5-20250929",
                system_prompt="You are helpful.",
                user_content="World",
                json_mode=True,
                max_tokens=512,
            ),
        ]
        batch_id = provider.submit_batch(requests)

        assert batch_id == "msgbatch_abc123"
        call_args = provider._client.messages.batches.create.call_args
        api_requests = call_args.kwargs["requests"]
        assert len(api_requests) == 2

        # First request: normal
        r1 = api_requests[0]
        assert r1["custom_id"] == "req-1"
        assert r1["params"]["model"] == "claude-sonnet-4-5-20250929"
        assert r1["params"]["max_tokens"] == 1024
        assert r1["params"]["system"] == "You are helpful."
        assert r1["params"]["messages"] == [{"role": "user", "content": "Hello"}]

        # Second request: json_mode → assistant prefill
        r2 = api_requests[1]
        assert r2["params"]["messages"] == [
            {"role": "user", "content": "World"},
            {"role": "assistant", "content": "["},
        ]

    def test_submit_batch_with_cache(self, provider):
        """cache_system_prompt=True → system에 cache_control 블록."""
        mock_batch = MagicMock()
        mock_batch.id = "msgbatch_cache"
        provider._client.messages.batches.create.return_value = mock_batch

        requests = [
            BatchRequest(
                custom_id="req-cache",
                model="claude-sonnet-4-5-20250929",
                system_prompt="Cached instructions",
                user_content="Data",
                cache_system_prompt=True,
                max_tokens=1024,
            ),
        ]
        provider.submit_batch(requests)

        call_args = provider._client.messages.batches.create.call_args
        api_req = call_args.kwargs["requests"][0]
        system = api_req["params"]["system"]
        assert isinstance(system, list)
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_get_batch_status_processing(self, provider):
        mock_batch = MagicMock()
        mock_batch.processing_status = "in_progress"
        provider._client.messages.batches.retrieve.return_value = mock_batch

        status = provider.get_batch_status("msgbatch_abc")
        assert status == BatchStatus.PROCESSING

    def test_get_batch_status_ended(self, provider):
        mock_batch = MagicMock()
        mock_batch.processing_status = "ended"
        provider._client.messages.batches.retrieve.return_value = mock_batch

        status = provider.get_batch_status("msgbatch_abc")
        assert status == BatchStatus.COMPLETED

    def test_get_batch_status_canceling(self, provider):
        mock_batch = MagicMock()
        mock_batch.processing_status = "canceling"
        provider._client.messages.batches.retrieve.return_value = mock_batch

        status = provider.get_batch_status("msgbatch_abc")
        assert status == BatchStatus.FAILED

    def test_get_batch_results(self, provider):
        """결과 스트림에서 succeeded/errored 항목 파싱."""
        entry_ok = MagicMock()
        entry_ok.custom_id = "req-1"
        entry_ok.result.type = "succeeded"
        entry_ok.result.message.content = [MagicMock(text="Response text")]
        entry_ok.result.message.usage.input_tokens = 100
        entry_ok.result.message.usage.output_tokens = 50
        # Cache token attributes
        entry_ok.result.message.usage.cache_read_input_tokens = 10
        entry_ok.result.message.usage.cache_creation_input_tokens = 5

        entry_err = MagicMock()
        entry_err.custom_id = "req-2"
        entry_err.result.type = "errored"
        entry_err.result.error.message = "Server error"

        provider._client.messages.batches.results.return_value = iter([entry_ok, entry_err])

        results = provider.get_batch_results("msgbatch_abc")
        assert len(results) == 2

        r1 = results[0]
        assert r1.custom_id == "req-1"
        assert r1.content == "Response text"
        assert r1.usage is not None
        assert r1.usage.prompt_tokens == 100
        assert r1.usage.completion_tokens == 50
        assert r1.usage.cache_read_tokens == 10
        assert r1.usage.cache_write_tokens == 5
        assert r1.error is None

        r2 = results[1]
        assert r2.custom_id == "req-2"
        assert r2.content is None
        assert r2.error == "Server error"

    def test_get_batch_results_json_mode_prepend(self, provider):
        """json_mode로 제출한 요청의 결과에서 '[' 프리펜드는 하지 않음.

        batch 결과에서는 assistant prefill이 이미 응답에 포함되지 않으므로
        프리펜드하지 않는 것이 올바름. caller가 필요시 처리."""
        entry = MagicMock()
        entry.custom_id = "req-json"
        entry.result.type = "succeeded"
        entry.result.message.content = [MagicMock(text='[{"key": "value"}]')]
        entry.result.message.usage.input_tokens = 50
        entry.result.message.usage.output_tokens = 20
        entry.result.message.usage.cache_read_input_tokens = 0
        entry.result.message.usage.cache_creation_input_tokens = 0

        provider._client.messages.batches.results.return_value = iter([entry])

        results = provider.get_batch_results("msgbatch_json")
        assert results[0].content == '[{"key": "value"}]'
