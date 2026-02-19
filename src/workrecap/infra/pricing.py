"""Pricing table for LLM cost estimation — loaded from TOML.

Prices are in USD per 1M tokens.
File: pricing.toml (repo root, git-tracked).
Unknown models return 0 cost (no error).
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_pricing(path: Path) -> dict[str, dict[str, tuple[float, float]]]:
    """Load pricing data from a TOML file.

    Returns {provider: {model: (input_rate, output_rate)}}.
    """
    raw = tomllib.loads(path.read_text())
    pricing: dict[str, dict[str, tuple[float, float]]] = {}
    for provider, models in raw.items():
        pricing[provider] = {
            model: (entry["input"], entry["output"]) for model, entry in models.items()
        }
    return pricing


def _normalize_model_name(model: str) -> str:
    """Strip date suffixes like -20250929 for matching."""
    parts = model.split("-")
    # Remove trailing date parts (8-digit sequences)
    while parts and parts[-1].isdigit() and len(parts[-1]) == 8:
        parts.pop()
    return "-".join(parts)


class PricingTable:
    """Pricing lookup for known models, loaded from TOML."""

    def __init__(self, path: Path | None = None) -> None:
        resolved = path or Path("pricing.toml")
        if resolved.exists():
            self._pricing = _load_pricing(resolved)
        else:
            logger.warning("Pricing file not found: %s — all costs will be $0", resolved)
            self._pricing: dict[str, dict[str, tuple[float, float]]] = {}

    def get_rate(self, provider: str, model: str) -> tuple[float, float] | None:
        """Get (prompt_rate, completion_rate) per 1M tokens, or None if unknown."""
        provider_rates = self._pricing.get(provider, {})
        # Try exact match first
        if model in provider_rates:
            return provider_rates[model]
        # Try normalized name (strip date suffixes)
        normalized = _normalize_model_name(model)
        if normalized in provider_rates:
            return provider_rates[normalized]
        return None

    def estimate_cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimate cost in USD. Returns 0.0 for unknown models."""
        rate = self.get_rate(provider, model)
        if rate is None:
            return 0.0
        prompt_rate, completion_rate = rate
        return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000
