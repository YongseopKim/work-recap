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
    def test_chat_json_mode(self, mock_cls):
        """json_mode=True passes response_format to OpenAI."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(text='[{"index": 0}]')
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        text, usage = p.chat("gpt-4o-mini", "system", "user", json_mode=True)

        assert text == '[{"index": 0}]'
        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_chat_json_mode_false_no_response_format(self, mock_cls):
        """json_mode=False (default) does not pass response_format."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        p.chat("gpt-4o-mini", "system", "user")

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert "response_format" not in call_kwargs

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_chat_max_tokens(self, mock_cls):
        """max_tokens is passed through to OpenAI."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        p.chat("gpt-4o-mini", "system", "user", max_tokens=1000)

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1000

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_chat_max_tokens_none_not_passed(self, mock_cls):
        """max_tokens=None (default) does not pass max_tokens."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        p.chat("gpt-4o-mini", "system", "user")

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_cache_tokens_extracted(self, mock_cls):
        """OpenAI auto-caching: cached_tokens extracted into cache_read_tokens."""
        mock_instance = MagicMock()
        resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="cached"))],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                prompt_tokens_details=SimpleNamespace(cached_tokens=80),
            ),
        )
        mock_instance.chat.completions.create.return_value = resp
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        _, usage = p.chat("gpt-4o-mini", "system", "user")

        assert usage.cache_read_tokens == 80
        assert usage.cache_write_tokens == 0

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_no_cache_details_defaults_zero(self, mock_cls):
        """No prompt_tokens_details → cache tokens default to 0."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_cls.return_value = mock_instance

        p = OpenAIProvider(api_key="sk-test")
        _, usage = p.chat("gpt-4o-mini", "system", "user")

        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

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
    def test_chat_json_mode_uses_prefill(self, mock_mod):
        """json_mode=True adds assistant prefill for JSON array output."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response(text='{"index": 0}]')
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        text, usage = p.chat("claude-haiku-4-5-20251001", "system", "user", json_mode=True)

        # Should prepend "[" to the response
        assert text == '[{"index": 0}]'
        call_kwargs = mock_instance.messages.create.call_args.kwargs
        msgs = call_kwargs["messages"]
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "user"}
        assert msgs[1] == {"role": "assistant", "content": "["}

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_chat_json_mode_false_no_prefill(self, mock_mod):
        """json_mode=False (default) sends normal single message."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response()
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        p.chat("claude-haiku-4-5-20251001", "system", "user")

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        msgs = call_kwargs["messages"]
        assert len(msgs) == 1
        assert msgs[0] == {"role": "user", "content": "user"}

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_chat_max_tokens(self, mock_mod):
        """max_tokens overrides the default 4096."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response()
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        p.chat("claude-haiku-4-5-20251001", "system", "user", max_tokens=1000)

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1000

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_chat_max_tokens_none_uses_default(self, mock_mod):
        """max_tokens=None uses the default 4096."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response()
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        p.chat("claude-haiku-4-5-20251001", "system", "user")

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_cache_system_prompt(self, mock_mod):
        """cache_system_prompt=True sends system as structured block with cache_control."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="cached result")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=80,
                cache_read_input_tokens=0,
            ),
        )
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        text, usage = p.chat(
            "claude-haiku-4-5-20251001",
            "system instructions",
            "user data",
            cache_system_prompt=True,
        )

        assert text == "cached result"
        # Verify system was sent as structured block with cache_control
        call_kwargs = mock_instance.messages.create.call_args.kwargs
        system_val = call_kwargs["system"]
        assert isinstance(system_val, list)
        assert system_val[0]["cache_control"] == {"type": "ephemeral"}

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_cache_tokens_in_usage(self, mock_mod):
        """Cache token stats are extracted from Anthropic response."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="result")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=90,
            ),
        )
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        _, usage = p.chat(
            "claude-haiku-4-5-20251001",
            "sys",
            "usr",
            cache_system_prompt=True,
        )

        assert usage.cache_read_tokens == 90
        assert usage.cache_write_tokens == 0

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_no_cache_no_structured_system(self, mock_mod):
        """Without cache_system_prompt, system is plain string."""
        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = _anthropic_response()
        mock_mod.Anthropic.return_value = mock_instance

        p = AnthropicProvider(api_key="sk-ant-test")
        p.chat("claude-haiku-4-5-20251001", "system", "user")

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert isinstance(call_kwargs["system"], str)

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
    def test_chat_json_mode(self, mock_genai):
        """json_mode=True sets response_mime_type in Gemini config."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _gemini_response(text='[{"index": 0}]')
        mock_genai.Client.return_value = mock_client

        p = GeminiProvider(api_key="AIza-test")
        text, usage = p.chat("gemini-2.0-flash", "system", "user", json_mode=True)

        assert text == '[{"index": 0}]'
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert config.response_mime_type == "application/json"

    @patch("workrecap.infra.providers.gemini_provider.genai")
    def test_chat_json_mode_false(self, mock_genai):
        """json_mode=False does not set response_mime_type."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _gemini_response()
        mock_genai.Client.return_value = mock_client

        p = GeminiProvider(api_key="AIza-test")
        p.chat("gemini-2.0-flash", "system", "user")

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert not hasattr(config, "response_mime_type") or config.response_mime_type is None

    @patch("workrecap.infra.providers.gemini_provider.genai")
    def test_chat_max_tokens(self, mock_genai):
        """max_tokens sets max_output_tokens in Gemini config."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _gemini_response()
        mock_genai.Client.return_value = mock_client

        p = GeminiProvider(api_key="AIza-test")
        p.chat("gemini-2.0-flash", "system", "user", max_tokens=500)

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert config.max_output_tokens == 500

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
    def test_chat_json_mode(self, mock_cls):
        """json_mode=True passes response_format to custom provider."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response(
            text='{"result": "ok"}'
        )
        mock_cls.return_value = mock_instance

        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        text, _ = p.chat("llama3", "system", "user", json_mode=True)

        assert text == '{"result": "ok"}'
        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @patch("workrecap.infra.providers.custom_provider.OpenAI")
    def test_chat_max_tokens(self, mock_cls):
        """max_tokens is passed through to custom provider."""
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = _openai_response()
        mock_cls.return_value = mock_instance

        p = CustomProvider(api_key="dummy", base_url="http://localhost:11434/v1")
        p.chat("llama3", "system", "user", max_tokens=2000)

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 2000

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
