from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from git_recap.exceptions import SummarizeError
from git_recap.infra.llm_client import LLMClient


class TestLLMClientInit:
    @patch("git_recap.infra.llm_client.OpenAI")
    def test_openai_provider(self, mock_openai_cls):
        client = LLMClient("openai", "test-key", "gpt-4o-mini")
        assert client._provider == "openai"
        mock_openai_cls.assert_called_once_with(api_key="test-key")

    @patch("git_recap.infra.llm_client.anthropic")
    def test_anthropic_provider(self, mock_anthropic_mod):
        client = LLMClient("anthropic", "test-key", "claude-sonnet-4-5-20250929")
        assert client._provider == "anthropic"
        mock_anthropic_mod.Anthropic.assert_called_once_with(api_key="test-key")

    def test_unsupported_provider(self):
        with pytest.raises(SummarizeError, match="Unsupported LLM provider"):
            LLMClient("gemini", "key", "model")


class TestChat:
    @patch("git_recap.infra.llm_client.OpenAI")
    def test_openai_chat(self, mock_openai_cls):
        # Mock chain: client.chat.completions.create().choices[0].message.content
        mock_message = SimpleNamespace(content="LLM response text")
        mock_choice = SimpleNamespace(message=mock_message)
        mock_response = SimpleNamespace(choices=[mock_choice])

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = mock_response
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

    @patch("git_recap.infra.llm_client.anthropic")
    def test_anthropic_chat(self, mock_anthropic_mod):
        mock_content_block = SimpleNamespace(text="Anthropic response")
        mock_response = SimpleNamespace(content=[mock_content_block])

        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = mock_response
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

    @patch("git_recap.infra.llm_client.OpenAI")
    def test_api_error_wrapped(self, mock_openai_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = RuntimeError("API timeout")
        mock_openai_cls.return_value = mock_instance

        client = LLMClient("openai", "key", "gpt-4o-mini")

        with pytest.raises(SummarizeError, match="LLM API call failed"):
            client.chat("system", "user")
