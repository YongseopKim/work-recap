"""LLM Router — task-based provider+model routing.

Drop-in replacement for LLMClient with the same chat() interface.
Routes each task to its configured provider+model based on ProviderConfig.
Supports strategy modes: economy, standard, premium, adaptive, fixed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workrecap.infra.usage_tracker import UsageTracker

from workrecap.exceptions import SummarizeError
from workrecap.infra.provider_config import ProviderConfig
from workrecap.infra.providers.base import LLMProvider
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)


class LLMRouter:
    """Task-based LLM router. Replaces LLMClient with multi-provider support.

    Usage:
        router = LLMRouter(provider_config)
        result = router.chat("system prompt", "user content", task="daily")

    Strategy modes:
        - economy: base_model only, no escalation
        - standard: base_model + escalation available
        - premium: escalation_model directly (if available, else base)
        - adaptive: base → self-assessment → escalate if needed
        - fixed: exact config, no escalation
    """

    def __init__(
        self,
        provider_config: ProviderConfig,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self._config = provider_config
        self._tracker = usage_tracker
        self._providers: dict[str, LLMProvider] = {}
        self._provider_lock = threading.Lock()
        self._usage = TokenUsage()
        self._usage_lock = threading.Lock()

    def chat(
        self,
        system_prompt: str,
        user_content: str,
        *,
        task: str = "default",
    ) -> str:
        """Send a chat completion, routing to the correct provider/model for the task.

        Args:
            system_prompt: System message.
            user_content: User message.
            task: Task name for routing (enrich, daily, weekly, monthly, yearly, query).

        Returns:
            LLM response text.

        Raises:
            SummarizeError: On any API failure.
        """
        task_config = self._config.get_task_config(task)
        strategy = self._config.strategy_mode

        provider_name, model, use_escalation = self._resolve_model(task_config, strategy)

        logger.info(
            "LLM call: task=%s provider=%s model=%s strategy=%s",
            task,
            provider_name,
            model,
            strategy,
        )
        logger.debug(
            "LLM request: system_prompt=%d chars, user_content=%d chars",
            len(system_prompt),
            len(user_content),
        )

        provider = self._get_provider(provider_name)

        try:
            if use_escalation and task_config.escalation_model:
                text, total_usage = self._chat_with_escalation(
                    provider, task_config, system_prompt, user_content
                )
            else:
                t0 = time.monotonic()
                text, total_usage = provider.chat(model, system_prompt, user_content)
                elapsed = time.monotonic() - t0
                logger.info(
                    "LLM tokens: prompt=%d completion=%d total=%d (%.1fs)",
                    total_usage.prompt_tokens,
                    total_usage.completion_tokens,
                    total_usage.total_tokens,
                    elapsed,
                )

            with self._usage_lock:
                self._usage = self._usage + total_usage

            if self._tracker:
                self._tracker.record(provider_name, model, total_usage)

            logger.debug("LLM response: %d chars", len(text))
            return text
        except SummarizeError:
            raise
        except Exception as e:
            raise SummarizeError(f"LLM API call failed: {e}") from e

    def _resolve_model(self, task_config, strategy: str):
        """Determine provider, model, and whether to use escalation.

        Returns (provider_name, model, use_escalation).
        """
        provider_name = task_config.provider
        base_model = task_config.model
        escalation_model = task_config.escalation_model

        if strategy == "economy" or strategy == "fixed":
            return provider_name, base_model, False
        elif strategy == "premium":
            model = escalation_model if escalation_model else base_model
            return provider_name, model, False
        elif strategy in ("standard", "adaptive"):
            if escalation_model:
                return provider_name, base_model, True
            return provider_name, base_model, False
        else:
            return provider_name, base_model, False

    def _chat_with_escalation(self, provider, task_config, system_prompt, user_content):
        """Use EscalationHandler for adaptive escalation."""
        from workrecap.infra.escalation import EscalationHandler

        # Escalation may use same or different provider for escalation model
        # For now, same provider handles both base and escalation models
        handler = EscalationHandler(
            base_provider=provider,
            base_model=task_config.model,
            escalation_provider=provider,
            escalation_model=task_config.escalation_model,
        )
        return handler.chat(system_prompt, user_content)

    @property
    def usage(self) -> TokenUsage:
        """Aggregate token usage across all calls (backward compat with LLMClient)."""
        return self._usage

    @property
    def usage_tracker(self) -> UsageTracker | None:
        """Per-model usage tracker, if configured."""
        return self._tracker

    def _get_provider(self, provider_name: str) -> LLMProvider:
        """Get or create a provider instance (lazy + cached)."""
        if provider_name in self._providers:
            return self._providers[provider_name]

        with self._provider_lock:
            # Double-check after acquiring lock
            if provider_name in self._providers:
                return self._providers[provider_name]

            entry = self._config.get_provider_entry(provider_name)
            provider = self._create_provider(provider_name, entry)
            self._providers[provider_name] = provider
            return provider

    def _create_provider(self, name: str, entry) -> LLMProvider:
        """Factory: create a provider instance from its config entry."""
        if name == "openai":
            from workrecap.infra.providers.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key=entry.api_key)
        elif name == "anthropic":
            from workrecap.infra.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(api_key=entry.api_key)
        elif name == "gemini":
            from workrecap.infra.providers.gemini_provider import GeminiProvider

            return GeminiProvider(api_key=entry.api_key)
        elif name == "custom":
            from workrecap.infra.providers.custom_provider import CustomProvider

            return CustomProvider(api_key=entry.api_key, base_url=entry.base_url or "")
        else:
            raise SummarizeError(f"Unsupported provider: {name}")
