from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workrecap.exceptions import SummarizeError
from workrecap.infra.llm_client import LLMClient
from workrecap.models import TokenUsage


def _openai_response(text="LLM response text", prompt=100, completion=50, total=150):
    """OpenAI 스타일 응답 mock."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        ),
    )


def _anthropic_response(text="Anthropic response", input_t=80, output_t=40):
    """Anthropic 스타일 응답 mock."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_t, output_tokens=output_t),
    )


class TestLLMClientInit:
    @patch("workrecap.infra.llm_client.OpenAI")
    def test_openai_provider(self, mock_openai_cls):
        client = LLMClient("openai", "test-key", "gpt-4o-mini")
        assert client._provider == "openai"
        mock_openai_cls.assert_called_once_with(api_key="test-key", timeout=120.0, max_retries=3)

    @patch("workrecap.infra.llm_client.anthropic")
    def test_anthropic_provider(self, mock_anthropic_mod):
        client = LLMClient("anthropic", "test-key", "claude-sonnet-4-5-20250929")
        assert client._provider == "anthropic"
        mock_anthropic_mod.Anthropic.assert_called_once_with(
            api_key="test-key", timeout=120.0, max_retries=3
        )

    def test_unsupported_provider(self):
        with pytest.raises(SummarizeError, match="Unsupported LLM provider"):
            LLMClient("gemini", "key", "model")

    @patch("workrecap.infra.llm_client.OpenAI")
    def test_initial_usage_is_zero(self, mock_openai_cls):
        client = LLMClient("openai", "key", "gpt-4o-mini")
        assert client.usage == TokenUsage()


class TestChat:
    @patch("workrecap.infra.llm_client.OpenAI")
    def test_openai_chat(self, mock_openai_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")
        result = client.chat("system prompt", "user content")

        assert result == "LLM response text"
        mock_instance.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user content"},
            ],
        )

    @patch("workrecap.infra.llm_client.anthropic")
    def test_anthropic_chat(self, mock_anthropic_mod):
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response()
        mock_anthropic_mod.Anthropic.return_value = mock_instance

        client = LLMClient("anthropic", "key", "claude-sonnet-4-5-20250929")
        result = client.chat("system", "user")

        assert result == "Anthropic response"
        mock_instance.messages.create.assert_called_once_with(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            system="system",
            messages=[{"role": "user", "content": "user"}],
        )

    @patch("workrecap.infra.llm_client.OpenAI")
    def test_api_error_wrapped(self, mock_openai_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = RuntimeError("API timeout")
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")

        with pytest.raises(SummarizeError, match="LLM API call failed"):
            client.chat("system", "user")


class TestTokenUsageTracking:
    @patch("workrecap.infra.llm_client.OpenAI")
    def test_openai_single_call_usage(self, mock_openai_cls):
        """단일 OpenAI 호출 후 usage 누적."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(
            prompt=200, completion=100, total=300
        )
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")
        client.chat("sys", "usr")

        assert client.usage.prompt_tokens == 200
        assert client.usage.completion_tokens == 100
        assert client.usage.total_tokens == 300
        assert client.usage.call_count == 1

    @patch("workrecap.infra.llm_client.anthropic")
    def test_anthropic_single_call_usage(self, mock_anthropic_mod):
        """단일 Anthropic 호출 후 usage 누적."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response(input_t=150, output_t=75)
        mock_anthropic_mod.Anthropic.return_value = mock_instance

        client = LLMClient("anthropic", "key", "claude-sonnet-4-5-20250929")
        client.chat("sys", "usr")

        assert client.usage.prompt_tokens == 150
        assert client.usage.completion_tokens == 75
        assert client.usage.total_tokens == 225
        assert client.usage.call_count == 1

    @patch("workrecap.infra.llm_client.OpenAI")
    def test_usage_accumulates_across_calls(self, mock_openai_cls):
        """여러 호출 시 usage 누적."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = [
            _openai_response(prompt=100, completion=50, total=150),
            _openai_response(prompt=200, completion=80, total=280),
            _openai_response(prompt=300, completion=120, total=420),
        ]
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")
        client.chat("s1", "u1")
        client.chat("s2", "u2")
        client.chat("s3", "u3")

        assert client.usage.prompt_tokens == 600
        assert client.usage.completion_tokens == 250
        assert client.usage.total_tokens == 850
        assert client.usage.call_count == 3

    @patch("workrecap.infra.llm_client.OpenAI")
    def test_failed_call_does_not_accumulate(self, mock_openai_cls):
        """실패한 호출은 usage에 포함되지 않음."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = [
            _openai_response(prompt=100, completion=50, total=150),
            RuntimeError("timeout"),
        ]
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")
        client.chat("s1", "u1")

        with pytest.raises(SummarizeError):
            client.chat("s2", "u2")

        assert client.usage.call_count == 1
        assert client.usage.total_tokens == 150


class TestTimeoutAndRetry:
    @patch("workrecap.infra.llm_client.OpenAI")
    def test_openai_custom_timeout_and_retries(self, mock_openai_cls):
        """커스텀 timeout/max_retries가 OpenAI에 전달."""
        LLMClient("openai", "key", "gpt-4o-mini", timeout=60.0, max_retries=5)
        mock_openai_cls.assert_called_once_with(api_key="key", timeout=60.0, max_retries=5)

    @patch("workrecap.infra.llm_client.anthropic")
    def test_anthropic_custom_timeout_and_retries(self, mock_anthropic_mod):
        """커스텀 timeout/max_retries가 Anthropic에 전달."""
        LLMClient("anthropic", "key", "claude-sonnet-4-5-20250929", timeout=30.0, max_retries=1)
        mock_anthropic_mod.Anthropic.assert_called_once_with(
            api_key="key", timeout=30.0, max_retries=1
        )

    @patch("workrecap.infra.llm_client.OpenAI")
    def test_default_timeout_and_retries(self, mock_openai_cls):
        """기본값 timeout=120, max_retries=3."""
        LLMClient("openai", "key", "gpt-4o-mini")
        mock_openai_cls.assert_called_once_with(api_key="key", timeout=120.0, max_retries=3)


class TestThreadSafety:
    @patch("workrecap.infra.llm_client.OpenAI")
    def test_concurrent_chat_usage_accumulation(self, mock_openai_cls):
        """10 threads calling chat() concurrently should accumulate usage correctly."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(
            prompt=100, completion=50, total=150
        )
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")

        def call_chat(i):
            client.chat(f"system_{i}", f"user_{i}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(call_chat, range(10)))

        assert client.usage.prompt_tokens == 1000
        assert client.usage.completion_tokens == 500
        assert client.usage.total_tokens == 1500
        assert client.usage.call_count == 10
