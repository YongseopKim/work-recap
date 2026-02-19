"""LLMRouter tests."""

import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from workrecap.infra.llm_router import LLMRouter
from workrecap.infra.provider_config import ProviderConfig
from workrecap.infra.usage_tracker import UsageTracker
from workrecap.infra.pricing import PricingTable


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".provider" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))
    return p


@pytest.fixture
def multi_provider_config(tmp_path):
    path = _write_toml(
        tmp_path,
        """\
        [strategy]
        mode = "fixed"

        [providers.openai]
        api_key = "sk-openai"

        [providers.anthropic]
        api_key = "sk-ant"

        [tasks.enrich]
        provider = "anthropic"
        model = "claude-haiku-4-5-20251001"

        [tasks.daily]
        provider = "openai"
        model = "gpt-4o-mini"

        [tasks.weekly]
        provider = "openai"
        model = "gpt-4o-mini"

        [tasks.monthly]
        provider = "anthropic"
        model = "claude-sonnet-4-5-20250929"

        [tasks.yearly]
        provider = "anthropic"
        model = "claude-sonnet-4-5-20250929"

        [tasks.query]
        provider = "openai"
        model = "gpt-4o"
        """,
    )
    return ProviderConfig(config_path=path)


@pytest.fixture
def fallback_config(tmp_path):
    """Single-provider config (replaces the old .env fallback)."""
    path = _write_toml(
        tmp_path,
        """\
        [providers.openai]
        api_key = "sk-test"

        [tasks.default]
        provider = "openai"
        model = "gpt-4o-mini"
        """,
    )
    return ProviderConfig(config_path=path)


class TestRouterInit:
    def test_creates_with_provider_config(self, multi_provider_config):
        router = LLMRouter(multi_provider_config)
        assert router is not None

    def test_creates_with_fallback_config(self, fallback_config):
        router = LLMRouter(fallback_config)
        assert router is not None


class TestRouterChat:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_routes_to_correct_provider(self, mock_openai_cls, fallback_config):
        """Fallback config routes all tasks to openai."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="response"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        result = router.chat("system", "user", task="daily")

        assert result == "response"
        mock_instance.chat.completions.create.assert_called_once()

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_default_task_when_not_specified(self, mock_openai_cls, fallback_config):
        """task 미지정 시 'default' 사용."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="default"))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=25, total_tokens=75),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        result = router.chat("system", "user")

        assert result == "default"

    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_multi_provider_routing(
        self, mock_openai_cls, mock_anthropic_mod, multi_provider_config
    ):
        """Different tasks route to different providers."""
        from types import SimpleNamespace

        # OpenAI mock
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="openai-response"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_openai

        # Anthropic mock
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="anthropic-response")],
            usage=SimpleNamespace(input_tokens=80, output_tokens=40),
        )
        mock_anthropic_mod.Anthropic.return_value = mock_anthropic

        router = LLMRouter(multi_provider_config)

        # daily → openai
        result = router.chat("sys", "usr", task="daily")
        assert result == "openai-response"

        # enrich → anthropic
        result = router.chat("sys", "usr", task="enrich")
        assert result == "anthropic-response"


class TestRouterUsage:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_usage_property_backward_compat(self, mock_openai_cls, fallback_config):
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        router.chat("s", "u", task="daily")

        assert router.usage.prompt_tokens == 100
        assert router.usage.call_count == 1

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_usage_tracker_records_per_model(self, mock_openai_cls, fallback_config):
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        tracker = UsageTracker(pricing=PricingTable())
        router = LLMRouter(fallback_config, usage_tracker=tracker)
        router.chat("s", "u", task="daily")

        usages = tracker.model_usages
        assert len(usages) == 1
        assert "openai/gpt-4o-mini" in usages


class TestRouterProviderCaching:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_provider_instances_are_cached(self, mock_openai_cls, fallback_config):
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        router.chat("s", "u", task="daily")
        router.chat("s", "u", task="weekly")

        # Only one OpenAI instance should be created
        assert mock_openai_cls.call_count == 1


class TestRouterThreadSafety:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_concurrent_chat_usage(self, mock_openai_cls, fallback_config):
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)

        def call_chat(i):
            router.chat(f"s{i}", f"u{i}", task="daily")

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(call_chat, range(10)))

        assert router.usage.call_count == 10
        assert router.usage.prompt_tokens == 1000


class TestRouterStrategyModes:
    """Test strategy mode behavior: economy, standard, premium, adaptive, fixed."""

    def _make_config(self, tmp_path, mode, with_escalation=True):
        esc = 'escalation_model = "gpt-4o"' if with_escalation else ""
        content = f"""\
        [strategy]
        mode = "{mode}"

        [providers.openai]
        api_key = "sk-test"

        [tasks.daily]
        provider = "openai"
        model = "gpt-4o-mini"
        {esc}
        """
        p = tmp_path / ".provider" / "config.toml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
        return ProviderConfig(config_path=p)

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_economy_mode_uses_base_only(self, mock_openai_cls, tmp_path):
        """Economy mode: always base model, no escalation."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="economy"))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=25, total_tokens=75),
        )
        mock_openai_cls.return_value = mock_instance

        config = self._make_config(tmp_path, "economy")
        router = LLMRouter(config)
        result = router.chat("sys", "usr", task="daily")

        assert result == "economy"
        # Verify model used is base model
        call_args = mock_instance.chat.completions.create.call_args
        assert call_args.kwargs.get("model", call_args[1].get("model")) == "gpt-4o-mini"

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_premium_mode_uses_escalation_model(self, mock_openai_cls, tmp_path):
        """Premium mode: use escalation_model directly if available."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="premium"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        config = self._make_config(tmp_path, "premium")
        router = LLMRouter(config)
        result = router.chat("sys", "usr", task="daily")

        assert result == "premium"
        call_args = mock_instance.chat.completions.create.call_args
        assert call_args.kwargs.get("model", call_args[1].get("model")) == "gpt-4o"

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_premium_mode_falls_back_to_base_without_escalation(self, mock_openai_cls, tmp_path):
        """Premium mode without escalation_model: use base model."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="base"))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=25, total_tokens=75),
        )
        mock_openai_cls.return_value = mock_instance

        config = self._make_config(tmp_path, "premium", with_escalation=False)
        router = LLMRouter(config)
        result = router.chat("sys", "usr", task="daily")
        assert result == "base"

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_fixed_mode_uses_base_only(self, mock_openai_cls, tmp_path):
        """Fixed mode: exact config, no escalation."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fixed"))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=25, total_tokens=75),
        )
        mock_openai_cls.return_value = mock_instance

        config = self._make_config(tmp_path, "fixed")
        router = LLMRouter(config)
        result = router.chat("sys", "usr", task="daily")
        assert result == "fixed"


class TestRouterJsonMode:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_json_mode_passed_to_provider(self, mock_openai_cls, fallback_config):
        """json_mode=True is forwarded to the provider's chat()."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        result = router.chat("sys", "usr", task="daily", json_mode=True)

        assert result == '{"a":1}'
        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_json_mode_default_false(self, mock_openai_cls, fallback_config):
        """json_mode defaults to False — no response_format sent."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="text"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        router.chat("sys", "usr", task="daily")

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert "response_format" not in call_kwargs


class TestRouterCacheSystemPrompt:
    @patch("workrecap.infra.providers.anthropic_provider.anthropic")
    def test_cache_system_prompt_forwarded(self, mock_mod, tmp_path):
        """cache_system_prompt=True is forwarded to provider."""
        from types import SimpleNamespace

        p = _write_toml(
            tmp_path,
            """\
            [providers.anthropic]
            api_key = "sk-ant"

            [tasks.default]
            provider = "anthropic"
            model = "claude-haiku-4-5-20251001"
            """,
        )
        config = ProviderConfig(config_path=p)

        mock_instance = MagicMock()
        mock_instance.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="cached")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )
        mock_mod.Anthropic.return_value = mock_instance

        router = LLMRouter(config)
        router.chat("sys", "usr", task="default", cache_system_prompt=True)

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        system_val = call_kwargs["system"]
        assert isinstance(system_val, list)
        assert system_val[0]["cache_control"] == {"type": "ephemeral"}


class TestRouterMaxTokens:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_max_tokens_from_task_config(self, mock_openai_cls, tmp_path):
        """max_tokens in task config is forwarded to the provider."""
        from types import SimpleNamespace

        p = _write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.enrich]
            provider = "openai"
            model = "gpt-4o-mini"
            max_tokens = 1000
            """,
        )
        config = ProviderConfig(config_path=p)

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(config)
        router.chat("sys", "usr", task="enrich")

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 1000

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_max_tokens_not_in_config_not_passed(self, mock_openai_cls, fallback_config):
        """When max_tokens is not in config, it's not passed to provider."""
        from types import SimpleNamespace

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        router.chat("sys", "usr", task="daily")

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs

    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_max_tokens_kwarg_overrides_config(self, mock_openai_cls, tmp_path):
        """Explicit max_tokens kwarg overrides the config value."""
        from types import SimpleNamespace

        p = _write_toml(
            tmp_path,
            """\
            [providers.openai]
            api_key = "sk-test"

            [tasks.enrich]
            provider = "openai"
            model = "gpt-4o-mini"
            max_tokens = 1000
            """,
        )
        config = ProviderConfig(config_path=p)

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="r"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(config)
        router.chat("sys", "usr", task="enrich", max_tokens=500)

        call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 500


class TestRouterErrorHandling:
    @patch("workrecap.infra.providers.openai_provider.OpenAI")
    def test_api_error_wrapped_as_summarize_error(self, mock_openai_cls, fallback_config):
        from workrecap.exceptions import SummarizeError

        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = RuntimeError("API down")
        mock_openai_cls.return_value = mock_instance

        router = LLMRouter(fallback_config)
        with pytest.raises(SummarizeError, match="API down"):
            router.chat("s", "u", task="daily")
