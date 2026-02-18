"""Model discovery 테스트."""

from unittest.mock import MagicMock

from workrecap.infra.model_discovery import discover_models
from workrecap.infra.providers.base import ModelInfo


class TestDiscoverModels:
    def test_aggregates_from_multiple_providers(self):
        """여러 provider의 모델 목록을 합친다."""
        provider_a = MagicMock()
        provider_a.provider_name = "openai"
        provider_a.list_models.return_value = [
            ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
        ]
        provider_b = MagicMock()
        provider_b.provider_name = "anthropic"
        provider_b.list_models.return_value = [
            ModelInfo(
                id="claude-sonnet-4-5-20250929", name="Claude Sonnet 4.5", provider="anthropic"
            ),
        ]

        result = discover_models({"openai": provider_a, "anthropic": provider_b})
        assert len(result) == 2
        providers = {m.provider for m in result}
        assert providers == {"openai", "anthropic"}

    def test_empty_provider_returns_empty(self):
        """모델이 없는 provider는 결과에 포함되지 않음."""
        provider = MagicMock()
        provider.provider_name = "custom"
        provider.list_models.return_value = []

        result = discover_models({"custom": provider})
        assert result == []

    def test_provider_error_skipped(self):
        """list_models가 예외를 던져도 다른 provider는 계속 진행."""
        ok_provider = MagicMock()
        ok_provider.provider_name = "openai"
        ok_provider.list_models.return_value = [
            ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai"),
        ]
        bad_provider = MagicMock()
        bad_provider.provider_name = "broken"
        bad_provider.list_models.side_effect = RuntimeError("API down")

        result = discover_models({"openai": ok_provider, "broken": bad_provider})
        assert len(result) == 1
        assert result[0].provider == "openai"

    def test_sorted_by_provider_then_id(self):
        """결과는 provider, id 순으로 정렬."""
        provider = MagicMock()
        provider.provider_name = "openai"
        provider.list_models.return_value = [
            ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
            ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai"),
        ]

        result = discover_models({"openai": provider})
        assert result[0].id == "gpt-4o"
        assert result[1].id == "gpt-4o-mini"
