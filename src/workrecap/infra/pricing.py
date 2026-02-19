"""Built-in pricing table for LLM cost estimation.

Prices are in USD per 1M tokens. Last updated: 2026-02-19.
Sources:
  - Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
  - OpenAI: https://openai.com/api/pricing/
  - Google: https://ai.google.dev/gemini-api/docs/pricing
Unknown models return 0 cost (no error).
"""

from __future__ import annotations

# (prompt_rate, completion_rate) per 1M tokens
_PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-5": (1.25, 10.00),
        "gpt-5-mini": (0.25, 2.00),
        "gpt-5-nano": (0.05, 0.40),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "o3": (2.00, 8.00),
        "o3-mini": (1.10, 4.40),
        "o4-mini": (1.10, 4.40),
    },
    "anthropic": {
        "claude-opus-4-6": (5.00, 25.00),
        "claude-opus-4-5": (5.00, 25.00),
        "claude-opus-4-1": (15.00, 75.00),
        "claude-opus-4": (15.00, 75.00),
        "claude-sonnet-4-6": (3.00, 15.00),
        "claude-sonnet-4-5": (3.00, 15.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-haiku-3-5": (0.80, 4.00),
        "claude-haiku-3": (0.25, 1.25),
    },
    "gemini": {
        "gemini-3-pro": (2.00, 12.00),
        "gemini-3-flash": (0.50, 3.00),
        "gemini-2.5-pro": (1.25, 10.00),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-flash-lite": (0.10, 0.40),
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-2.0-flash-lite": (0.075, 0.30),
    },
}


def _normalize_model_name(model: str) -> str:
    """Strip date suffixes like -20250929 for matching."""
    parts = model.split("-")
    # Remove trailing date parts (8-digit sequences)
    while parts and parts[-1].isdigit() and len(parts[-1]) == 8:
        parts.pop()
    return "-".join(parts)


class PricingTable:
    """Built-in pricing lookup for known models."""

    def get_rate(self, provider: str, model: str) -> tuple[float, float] | None:
        """Get (prompt_rate, completion_rate) per 1M tokens, or None if unknown."""
        provider_rates = _PRICING.get(provider, {})
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
