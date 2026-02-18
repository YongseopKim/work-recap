"""Provider abstraction layer tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.infra.providers.openai_provider import OpenAIProvider
from workrecap.infra.providers.anthropic_provider import AnthropicProvider
from workrecap.infra.providers.gemini_provider import GeminiProvider
from workrecap.infra.providers.custom_provider import CustomProvider
from workrecap.models import TokenUsage


# ── Fixtures ──


def _openai_response(text="hello", prompt=100, completion=50, total=150):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        ),
    )


def _anthropic_response(text="hello", input_t=80, output_t=40):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_t, output_tokens=output_t),
    )


def _gemini_response(text="hello", prompt=90, completion=45, total=135):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=completion,
            total_token_count=total,
        ),
    )


# ── Base ABC ──


class TestLLMProviderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]

    def test_provider_name_required(self):
        """Concrete classes must define provider_name."""

        class Incomplete(LLMProvider):
            def chat(self, model, system_prompt, user_content):
                pass

        # list_models has default, but provider_name is abstract
        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


class TestModelInfo:
    def test_model_info_fields(self):
        info = ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai")
        assert info.id == "gpt-4o"
        assert info.name == "GPT-4o"
        assert info.provider == "openai"


# ── OpenAI Provider ──


class TestOpenAIProvider:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_init_creates_client(self, mock_cls):
        p = OpenAIProvider(api_key="sk-test")
        assert p.provider_name == "openai"
        mock_cls.assert_called_once_with(api_key="sk-test", timeout=120.0, max_retries=3)

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_chat_returns_text_and_usage(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(
            text="result", prompt=200, completion=100, total=300
        )
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        text, usage = p.chat("gpt-4o-mini", "system", "user")

        assert text == "result"
        assert usage == TokenUsage(
            prompt_tokens=200, completion_tokens=100, total_tokens=300, call_count=1
        )
        mock_instance.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
        )

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_chat_wraps_error(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = RuntimeError("timeout")
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        with pytest.raises(Exception, match="timeout"):
            p.chat("gpt-4o-mini", "sys", "usr")

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_list_models(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.models.list.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(id="gpt-4o", owned_by="openai"),
                SimpleNamespace(id="gpt-4o-mini", owned_by="openai"),
            ]
        )
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        models = p.list_models()
        assert len(models) == 2
        assert models[0].id == "gpt-4o"
        assert models[0].provider == "openai"


# ── Anthropic Provider ──


class TestAnthropicProvider:
    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_init_creates_client(self, mock_mod):
        p = AnthropicProvider(api_key="sk-ant-test")
        assert p.provider_name == "anthropic"
        mock_mod.Anthropic.assert_called_once_with(
            api_key="sk-ant-test", timeout=120.0, max_retries=3
        )

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_chat_returns_text_and_usage(self, mock_mod):
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response(
            text="result", input_t=150, output_t=75
        )
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        text, usage = p.chat("claude-haiku-4-5-20251001", "system", "user")

        assert text == "result"
        assert usage == TokenUsage(
            prompt_tokens=150, completion_tokens=75, total_tokens=225, call_count=1
        )
        mock_instance.messages.create.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system="system",
            messages=[{"role": "user", "content": "user"}],
        )

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_list_models(self, mock_mod):
        mock_instance = MagicMock()
        mock_instance.models.list.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(id="claude-haiku-4-5-20251001", display_name="Claude Haiku"),
                SimpleNamespace(id="claude-sonnet-4-5-20250929", display_name="Claude Sonnet"),
            ]
        )
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        models = p.list_models()
        assert len(models) == 2
        assert models[0].id == "claude-haiku-4-5-20251001"
        assert models[0].provider == "anthropic"


# ── Gemini Provider ──


class TestGeminiProvider:
    @patch("workrecap.infra.providers.gemini_provider.genai")
    def test_init_creates_client(self, mock_genai):
        p = GeminiProvider(api_key="AIza-test")
        assert p.provider_name == "gemini"
        mock_genai.Client.assert_called_once_with(api_key="AIza-test")

    @patch("workrecap.infra.providers.gemini_provider.genai")
    def test_chat_returns_text_and_usage(self, mock_genai):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _gemini_response(
            text="result", prompt=200, completion=100, total=300
        )
        mock_genai.Client.return_value = mock_client

        p = GeminiProvider(api_key="AIza-test")
        text, usage = p.chat("gemini-2.0-flash", "system", "user")

        assert text == "result"
        assert usage == TokenUsage(
            prompt_tokens=200, completion_tokens=100, total_tokens=300, call_count=1
        )
        mock_client.models.generate_content.assert_called_once()

    @patch("workrecap.infra.providers.gemini_provider.genai")
    def test_list_models(self, mock_genai):
        mock_client = MagicMock()
        mock_client.models.list.return_value = [
            SimpleNamespace(name="models/gemini-2.0-flash", display_name="Gemini 2.0 Flash"),
        ]
        mock_genai.Client.return_value = mock_client

        p = GeminiProvider(api_key="AIza-test")
        models = p.list_models()
        assert len(models) == 1
        assert models[0].id == "models/gemini-2.0-flash"
        assert models[0].provider == "gemini"


# ── Custom Provider (OpenAI-compatible) ──


class TestCustomProvider:
    @patch("workrecap.infra.providers.custom_provider.OpenAI")
    def test_init_with_base_url(self, mock_cls):
        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        assert p.provider_name == "custom"
        mock_cls.assert_called_once_with(
            api_key="dummy",
            base_url="http://localhost:11434/v1",
            timeout=120.0,
            max_retries=3,
        )

    @patch("workrecap.infra.providers.custom_provider.OpenAI")
    def test_chat_delegates_to_openai_sdk(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(text="local")
        mock_cls.return_value = mock_instance

        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        text, usage = p.chat("llama3", "system", "user")

        assert text == "local"
        assert usage.call_count == 1

    @patch("workrecap.infra.providers.custom_provider.OpenAI")
    def test_chat_handles_missing_usage(self, mock_cls):
        """Some local models don't return usage stats."""
        mock_instance = MagicMock()
        resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="local"))],
            usage=None,
        )
        mock_instance.chat.completions.create.return_value = resp
        mock_cls.return_value = mock_instance

        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        text, usage = p.chat("llama3", "system", "user")

        assert text == "local"
        assert usage == TokenUsage(call_count=1)

    @patch("workrecap.infra.providers.custom_provider.OpenAI")
    def test_list_models(self, mock_cls):
        mock_instance = MagicMock()
        mock_instance.models.list.return_value = SimpleNamespace(
            data=[SimpleNamespace(id="llama3", owned_by="local")]
        )
        mock_cls.return_value = mock_instance

        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        models = p.list_models()
        assert len(models) == 1
        assert models[0].provider == "custom"
