"""Gemini provider batch 기능 단위 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchStatus,
)
from workrecap.infra.providers.gemini_provider import GeminiProvider


@pytest.fixture
def provider():
    with patch("workrecap.infra.providers.gemini_provider.genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        p = GeminiProvider(api_key="test-key")
        yield p


class TestGeminiBatchCapable:
    def test_is_batch_capable(self, provider):
        assert isinstance(provider, BatchCapable)

    def test_submit_batch(self, provider):
        """submit_batch → client.batches.create(model=..., src=[...])."""
        mock_job = MagicMock()
        mock_job.name = "batches/batch_gemini_123"
        provider._client.batches.create.return_value = mock_job

        requests = [
            BatchRequest(
                custom_id="req-1",
                model="gemini-2.5-flash",
                system_prompt="You are helpful.",
                user_content="Hello",
            ),
            BatchRequest(
                custom_id="req-2",
                model="gemini-2.5-flash",
                system_prompt="You are helpful.",
                user_content="World",
                json_mode=True,
            ),
        ]
        batch_id = provider.submit_batch(requests)

        assert batch_id == "batches/batch_gemini_123"
        call_args = provider._client.batches.create.call_args
        assert call_args.kwargs["model"] == "gemini-2.5-flash"
        src = call_args.kwargs["src"]
        assert len(src) == 2

        # First request: normal
        r1 = src[0]
        assert r1["key"] == "req-1"
        assert r1["contents"][0]["parts"][0]["text"] == "Hello"
        assert r1["contents"][0]["role"] == "user"
        assert "response_mime_type" not in r1.get("config", {})

        # Second request: json_mode
        r2 = src[1]
        assert r2["config"]["response_mime_type"] == "application/json"

    def test_get_batch_status_mapping(self, provider):
        """Gemini batch state 매핑 테스트."""
        for api_state, expected in [
            ("JOB_STATE_PENDING", BatchStatus.SUBMITTED),
            ("JOB_STATE_RUNNING", BatchStatus.PROCESSING),
            ("JOB_STATE_SUCCEEDED", BatchStatus.COMPLETED),
            ("JOB_STATE_FAILED", BatchStatus.FAILED),
            ("JOB_STATE_CANCELLED", BatchStatus.FAILED),
        ]:
            mock_job = MagicMock()
            mock_job.state = api_state
            provider._client.batches.get.return_value = mock_job

            status = provider.get_batch_status("batches/123")
            assert status == expected, f"Expected {expected} for '{api_state}', got {status}"

    def test_get_batch_results(self, provider):
        """배치 결과 파싱."""
        mock_job = MagicMock()
        mock_job.state = "JOB_STATE_SUCCEEDED"

        # Simulate response entries
        entry_ok = MagicMock()
        entry_ok.key = "req-1"
        entry_ok.response.candidates = [MagicMock()]
        entry_ok.response.candidates[0].content.parts = [MagicMock(text="Hello result")]
        entry_ok.response.usage_metadata.prompt_token_count = 40
        entry_ok.response.usage_metadata.candidates_token_count = 20
        entry_ok.response.usage_metadata.total_token_count = 60

        entry_err = MagicMock()
        entry_err.key = "req-2"
        entry_err.response = None  # Failed entry

        mock_job.responses = [entry_ok, entry_err]
        provider._client.batches.get.return_value = mock_job

        results = provider.get_batch_results("batches/123")
        assert len(results) == 2

        r1 = results[0]
        assert r1.custom_id == "req-1"
        assert r1.content == "Hello result"
        assert r1.usage.prompt_tokens == 40
        assert r1.usage.completion_tokens == 20

        r2 = results[1]
        assert r2.custom_id == "req-2"
        assert r2.content is None
        assert "failed" in r2.error.lower() or "no response" in r2.error.lower()
