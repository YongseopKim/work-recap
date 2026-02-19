"""OpenAI provider batch 기능 단위 테스트."""

import json
from unittest.mock import MagicMock, patch

import pytest

from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchStatus,
)
from workrecap.infra.providers.openai_provider import OpenAIProvider


@pytest.fixture
def provider():
    with patch("workrecap.infra.providers.openai_provider.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        p = OpenAIProvider(api_key="test-key")
        yield p


class TestOpenAIBatchCapable:
    def test_is_batch_capable(self, provider):
        assert isinstance(provider, BatchCapable)

    def test_submit_batch(self, provider):
        """submit_batch → JSONL 파일 업로드 → batches.create."""
        mock_file = MagicMock()
        mock_file.id = "file-abc123"
        provider._client.files.create.return_value = mock_file

        mock_batch = MagicMock()
        mock_batch.id = "batch_xyz789"
        provider._client.batches.create.return_value = mock_batch

        requests = [
            BatchRequest(
                custom_id="req-1",
                model="gpt-4o-mini",
                system_prompt="You are helpful.",
                user_content="Hello",
                max_tokens=1024,
            ),
            BatchRequest(
                custom_id="req-2",
                model="gpt-4o-mini",
                system_prompt="You are helpful.",
                user_content="World",
                json_mode=True,
                max_tokens=512,
            ),
        ]
        batch_id = provider.submit_batch(requests)

        assert batch_id == "batch_xyz789"

        # Verify file upload
        file_call = provider._client.files.create.call_args
        assert file_call.kwargs["purpose"] == "batch"
        # The file argument should be a tuple (filename, content, content_type)
        uploaded = file_call.kwargs["file"]
        assert uploaded[0] == "batch_input.jsonl"

        # Parse JSONL content
        lines = uploaded[1].strip().split("\n")
        assert len(lines) == 2

        line1 = json.loads(lines[0])
        assert line1["custom_id"] == "req-1"
        assert line1["method"] == "POST"
        assert line1["url"] == "/v1/chat/completions"
        body1 = line1["body"]
        assert body1["model"] == "gpt-4o-mini"
        assert body1["max_completion_tokens"] == 1024
        assert "response_format" not in body1

        line2 = json.loads(lines[1])
        body2 = line2["body"]
        assert body2["response_format"] == {"type": "json_object"}
        assert body2["max_completion_tokens"] == 512

        # Verify batch creation
        batch_call = provider._client.batches.create.call_args
        assert batch_call.kwargs["input_file_id"] == "file-abc123"
        assert batch_call.kwargs["endpoint"] == "/v1/chat/completions"
        assert batch_call.kwargs["completion_window"] == "24h"

    def test_get_batch_status_mapping(self, provider):
        """OpenAI batch status 매핑 테스트."""
        for api_status, expected in [
            ("validating", BatchStatus.SUBMITTED),
            ("in_progress", BatchStatus.PROCESSING),
            ("finalizing", BatchStatus.PROCESSING),
            ("completed", BatchStatus.COMPLETED),
            ("failed", BatchStatus.FAILED),
            ("cancelled", BatchStatus.FAILED),
            ("expired", BatchStatus.EXPIRED),
        ]:
            mock_batch = MagicMock()
            mock_batch.status = api_status
            provider._client.batches.retrieve.return_value = mock_batch

            status = provider.get_batch_status("batch_xyz")
            assert status == expected, f"Expected {expected} for '{api_status}', got {status}"

    def test_get_batch_results(self, provider):
        """output_file_id에서 결과를 읽어오는 흐름."""
        # Setup batch with output file
        mock_batch = MagicMock()
        mock_batch.output_file_id = "file-output-123"
        provider._client.batches.retrieve.return_value = mock_batch

        # Setup file content as JSONL
        result_lines = [
            json.dumps(
                {
                    "id": "resp-1",
                    "custom_id": "req-1",
                    "response": {
                        "status_code": 200,
                        "body": {
                            "choices": [{"message": {"content": "Hello response"}}],
                            "usage": {
                                "prompt_tokens": 50,
                                "completion_tokens": 25,
                                "total_tokens": 75,
                                "prompt_tokens_details": {"cached_tokens": 10},
                            },
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "id": "resp-2",
                    "custom_id": "req-2",
                    "response": {
                        "status_code": 400,
                        "body": {"error": {"message": "Invalid request"}},
                    },
                }
            ),
        ]
        mock_content = MagicMock()
        mock_content.text = "\n".join(result_lines)
        provider._client.files.content.return_value = mock_content

        results = provider.get_batch_results("batch_xyz")
        assert len(results) == 2

        r1 = results[0]
        assert r1.custom_id == "req-1"
        assert r1.content == "Hello response"
        assert r1.usage is not None
        assert r1.usage.prompt_tokens == 50
        assert r1.usage.completion_tokens == 25
        assert r1.usage.cache_read_tokens == 10
        assert r1.error is None

        r2 = results[1]
        assert r2.custom_id == "req-2"
        assert r2.content is None
        assert r2.error == "Invalid request"

    def test_get_batch_results_no_output_file(self, provider):
        """output_file_id가 없으면 빈 리스트."""
        mock_batch = MagicMock()
        mock_batch.output_file_id = None
        provider._client.batches.retrieve.return_value = mock_batch

        results = provider.get_batch_results("batch_xyz")
        assert results == []
