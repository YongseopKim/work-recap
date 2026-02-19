"""Thread-safe per-model usage tracking with cost estimation."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workrecap.infra.pricing import PricingTable

from workrecap.models import ModelUsage, TokenUsage


class UsageTracker:
    """Tracks LLM usage per provider/model with optional cost estimation.

    Thread-safe: all mutations protected by a lock.
    """

    def __init__(self, pricing: PricingTable | None = None) -> None:
        self._pricing = pricing
        self._lock = threading.Lock()
        self._usages: dict[str, ModelUsage] = {}

    def record(self, provider: str, model: str, usage: TokenUsage) -> None:
        """Record a single LLM call's token usage."""
        key = f"{provider}/{model}"
        cost = 0.0
        if self._pricing:
            cost = self._pricing.estimate_cost(
                provider,
                model,
                usage.prompt_tokens,
                usage.completion_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )

        with self._lock:
            if key not in self._usages:
                self._usages[key] = ModelUsage(provider=provider, model=model)
            mu = self._usages[key]
            mu.prompt_tokens += usage.prompt_tokens
            mu.completion_tokens += usage.completion_tokens
            mu.total_tokens += usage.total_tokens
            mu.call_count += usage.call_count
            mu.estimated_cost_usd += cost
            mu.cache_read_tokens += usage.cache_read_tokens
            mu.cache_write_tokens += usage.cache_write_tokens

    @property
    def model_usages(self) -> dict[str, ModelUsage]:
        """Return a snapshot of per-model usage."""
        with self._lock:
            return dict(self._usages)

    @property
    def total_usage(self) -> TokenUsage:
        """Aggregate TokenUsage across all models (backward compat)."""
        with self._lock:
            total = TokenUsage()
            for mu in self._usages.values():
                total = total + TokenUsage(
                    prompt_tokens=mu.prompt_tokens,
                    completion_tokens=mu.completion_tokens,
                    total_tokens=mu.total_tokens,
                    call_count=mu.call_count,
                )
            return total

    def format_report(self) -> str:
        """Format a human-readable usage report."""
        with self._lock:
            usages = list(self._usages.values())

        if not usages:
            return "No LLM usage recorded."

        lines = ["LLM Usage Report:"]
        total_calls = 0
        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        total_cost = 0.0
        total_cache_read = 0
        total_cache_write = 0

        for mu in usages:
            calls_str = f"{mu.call_count} call{'s' if mu.call_count != 1 else ''}"
            cost_str = f" (~${mu.estimated_cost_usd:.3f})" if mu.estimated_cost_usd > 0 else ""
            lines.append(
                f"  {mu.provider} / {mu.model}: {calls_str}, "
                f"{mu.prompt_tokens:,}+{mu.completion_tokens:,}"
                f"={mu.total_tokens:,} tokens{cost_str}"
            )
            if mu.cache_read_tokens > 0 or mu.cache_write_tokens > 0:
                lines.append(
                    f"    cache: {mu.cache_read_tokens:,} read + {mu.cache_write_tokens:,} write"
                )
            total_calls += mu.call_count
            total_prompt += mu.prompt_tokens
            total_completion += mu.completion_tokens
            total_tokens += mu.total_tokens
            total_cost += mu.estimated_cost_usd
            total_cache_read += mu.cache_read_tokens
            total_cache_write += mu.cache_write_tokens

        if len(usages) > 1:
            lines.append("  " + "─" * 50)
            cost_str = f" (~${total_cost:.3f})" if total_cost > 0 else ""
            calls_str = f"{total_calls} call{'s' if total_calls != 1 else ''}"
            lines.append(
                f"  Total: {calls_str}, "
                f"{total_prompt:,}+{total_completion:,}"
                f"={total_tokens:,} tokens{cost_str}"
            )
            if total_cache_read > 0 or total_cache_write > 0:
                lines.append(f"    cache: {total_cache_read:,} read + {total_cache_write:,} write")

        return "\n".join(lines)
