"""Model discovery — aggregate list_models() across providers."""

import logging

from workrecap.infra.providers.base import LLMProvider, ModelInfo

logger = logging.getLogger(__name__)


def discover_models(providers: dict[str, LLMProvider]) -> list[ModelInfo]:
    """Collect available models from all providers.

    Providers that raise on list_models() are silently skipped.
    Results are sorted by (provider, id).
    """
    models: list[ModelInfo] = []
    for name, provider in sorted(providers.items()):
        try:
            models.extend(provider.list_models())
        except Exception:
            logger.warning("Failed to list models for provider '%s'", name)
    models.sort(key=lambda m: (m.provider, m.id))
    return models
