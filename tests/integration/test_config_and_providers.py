"""Integration tests — config consistency + provider connectivity + enrich task.

실행: pytest -m integration -x -v -k test_config_and_providers
"""

import json
from pathlib import Path

import pytest

from workrecap.infra.pricing import PricingTable
from workrecap.infra.provider_config import ProviderConfig
from tests.integration.conftest import HAS_ENV

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_ENV, reason=".env file not found — skipping integration tests"),
]


class TestConfigConsistency:
    """config.toml과 pricing.toml 간 일관성 검증."""

    def test_provider_config_validates(self, real_config):
        """ProviderConfig.validate() 에러 없음 확인."""
        pc = ProviderConfig(real_config.provider_config_path)
        errors = pc.validate()
        assert errors == [], f"Config validation errors: {errors}"

    def test_all_task_models_in_pricing(self, real_config):
        """config.toml의 모든 task model(+ escalation_model)이 pricing.toml에 존재."""
        pc = ProviderConfig(real_config.provider_config_path)
        pricing = PricingTable(Path("pricing.toml"))

        missing = []
        for task_name in ("enrich", "daily", "weekly", "monthly", "yearly", "query"):
            try:
                tc = pc.get_task_config(task_name)
            except KeyError:
                continue

            rate = pricing.get_rate(tc.provider, tc.model)
            if rate is None:
                missing.append(f"{task_name}: {tc.provider}/{tc.model}")

            if tc.escalation_model:
                esc_rate = pricing.get_rate(tc.provider, tc.escalation_model)
                if esc_rate is None:
                    missing.append(f"{task_name} (escalation): {tc.provider}/{tc.escalation_model}")

        assert missing == [], f"Models not in pricing.toml: {missing}"


class TestProviderConnectivity:
    """최소 비용 모델로 각 provider 연결 확인."""

    def test_openai_connectivity(self, real_config):
        """OpenAI gpt-5-mini로 간단한 chat 호출."""
        pc = ProviderConfig(real_config.provider_config_path)
        if "openai" not in pc.providers:
            pytest.skip("OpenAI provider not configured")

        from workrecap.infra.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key=pc.providers["openai"].api_key)
        text, usage = provider.chat(
            "gpt-5-mini",
            "You are a helpful assistant.",
            "Say 'hello' and nothing else.",
        )
        assert text.strip(), "Empty response from OpenAI"
        assert usage.total_tokens > 0, f"Expected total_tokens > 0, got {usage.total_tokens}"

    def test_anthropic_connectivity(self, real_config):
        """Anthropic claude-haiku-4-5로 간단한 chat 호출."""
        pc = ProviderConfig(real_config.provider_config_path)
        if "anthropic" not in pc.providers:
            pytest.skip("Anthropic provider not configured")

        from workrecap.infra.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key=pc.providers["anthropic"].api_key)
        text, usage = provider.chat(
            "claude-haiku-4-5-20251001",
            "You are a helpful assistant.",
            "Say 'hello' and nothing else.",
        )
        assert text.strip(), "Empty response from Anthropic"
        assert usage.total_tokens > 0, f"Expected total_tokens > 0, got {usage.total_tokens}"

    def test_gemini_connectivity(self, real_config):
        """Gemini gemini-2.0-flash-lite로 간단한 chat 호출."""
        pc = ProviderConfig(real_config.provider_config_path)
        if "gemini" not in pc.providers:
            pytest.skip("Gemini provider not configured")

        from google.genai.errors import ClientError

        from workrecap.infra.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(api_key=pc.providers["gemini"].api_key)
        try:
            text, usage = provider.chat(
                "gemini-2.0-flash-lite",
                "You are a helpful assistant.",
                "Say 'hello' and nothing else.",
            )
        except ClientError as e:
            if e.code == 429:
                pytest.skip(f"Gemini rate limit exceeded: {e}")
            raise
        assert text.strip(), "Empty response from Gemini"
        assert usage.total_tokens > 0, f"Expected total_tokens > 0, got {usage.total_tokens}"


class TestEnrichTask:
    """LLMRouter를 통한 enrich task JSON 응답 검증."""

    def test_enrich_json_response(self, real_config, llm_router):
        """task='enrich', json_mode=True로 실제 LLM 호출 → 유효한 JSON 응답."""
        user_content = (
            "Activity: PR #123 'Fix login bug' by user1\n"
            "Files: src/auth/login.py (+10 -5)\n"
            "Body: Fixed null pointer in session validation\n\n"
            "Classify this activity as JSON with keys: intent, change_summary"
        )
        text = llm_router.chat(
            "You are a code reviewer. Respond with a JSON array of objects.",
            user_content,
            task="enrich",
            json_mode=True,
        )

        # Should be valid JSON — either a string to parse, or already parsed
        # (adaptive mode's escalation handler may return pre-parsed objects)
        if isinstance(text, str):
            parsed = json.loads(text)
        else:
            parsed = text
        assert parsed is not None, "Parsed JSON should not be None"
        assert isinstance(parsed, (list, dict)), f"Expected list or dict, got {type(parsed)}"
