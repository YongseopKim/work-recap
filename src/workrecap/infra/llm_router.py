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
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
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
        json_mode: bool = False,
        max_tokens: int | None = None,
        cache_system_prompt: bool = False,
    ) -> str:
        """Send a chat completion, routing to the correct provider/model for the task.

        Args:
            system_prompt: System message.
            user_content: User message.
            task: Task name for routing (enrich, daily, weekly, monthly, yearly, query).
            json_mode: If True, constrain output to valid JSON.
            max_tokens: Max output tokens (overrides task config if set).
            cache_system_prompt: If True, enable prompt caching for system prompt.

        Returns:
            LLM response text.

        Raises:
            SummarizeError: On any API failure.
        """
        task_config = self._config.get_task_config(task)
        strategy = self._config.strategy_mode

        provider_name, model, use_escalation = self._resolve_model(task_config, strategy)

        # Resolve max_tokens: explicit kwarg > task config > None
        resolved_max_tokens = max_tokens if max_tokens is not None else task_config.max_tokens

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
                    provider,
                    task_config,
                    system_prompt,
                    user_content,
                    json_mode=json_mode,
                    max_tokens=resolved_max_tokens,
                    cache_system_prompt=cache_system_prompt,
                )
            else:
                t0 = time.monotonic()
                text, total_usage = provider.chat(
                    model,
                    system_prompt,
                    user_content,
                    json_mode=json_mode,
                    max_tokens=resolved_max_tokens,
                    cache_system_prompt=cache_system_prompt,
                )
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

    def _chat_with_escalation(
        self,
        provider,
        task_config,
        system_prompt,
        user_content,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        cache_system_prompt: bool = False,
    ):
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
        return handler.chat(
            system_prompt,
            user_content,
            json_mode=json_mode,
            max_tokens=max_tokens,
            cache_system_prompt=cache_system_prompt,
        )

    @property
    def usage(self) -> TokenUsage:
        """Aggregate token usage across all calls (backward compat with LLMClient)."""
        return self._usage

    @property
    def usage_tracker(self) -> UsageTracker | None:
        """Per-model usage tracker, if configured."""
        return self._tracker

    # ── Batch API ──

    def submit_batch(self, requests: list[dict], *, task: str = "default") -> str:
        """Submit a batch job. Returns batch_id.

        Args:
            requests: List of dicts with keys: custom_id, system_prompt, user_content,
                      and optional: json_mode, max_tokens, cache_system_prompt.
            task: Task name for routing (determines provider/model).

        Raises:
            ValueError: If the provider does not support batch processing.
        """
        task_config = self._config.get_task_config(task)
        provider = self._get_provider(task_config.provider)

        if not isinstance(provider, BatchCapable):
            raise ValueError(f"Provider '{task_config.provider}' does not support batch processing")

        batch_requests = [
            BatchRequest(
                custom_id=r["custom_id"],
                model=task_config.model,
                system_prompt=r["system_prompt"],
                user_content=r["user_content"],
                json_mode=r.get("json_mode", False),
                max_tokens=r.get("max_tokens") or task_config.max_tokens,
                cache_system_prompt=r.get("cache_system_prompt", False),
            )
            for r in requests
        ]
        logger.info(
            "Submitting batch: task=%s provider=%s requests=%d",
            task,
            task_config.provider,
            len(batch_requests),
        )
        return provider.submit_batch(batch_requests)

    def get_batch_status(self, batch_id: str, *, task: str) -> BatchStatus:
        """Get the current status of a batch job."""
        provider = self._get_batch_provider(task)
        return provider.get_batch_status(batch_id)

    def get_batch_results(self, batch_id: str, *, task: str) -> list[BatchResult]:
        """Retrieve results from a completed batch."""
        provider = self._get_batch_provider(task)
        return provider.get_batch_results(batch_id)

    def wait_for_batch(
        self,
        batch_id: str,
        *,
        task: str,
        timeout: int | float = 3600,
        poll_interval: int | float = 10,
    ) -> list[BatchResult]:
        """Poll until batch completes, then return results.

        Raises:
            TimeoutError: If batch doesn't complete within timeout.
            RuntimeError: If batch fails or expires.
        """
        provider = self._get_batch_provider(task)
        deadline = time.monotonic() + timeout

        while True:
            status = provider.get_batch_status(batch_id)
            if status == BatchStatus.COMPLETED:
                return provider.get_batch_results(batch_id)
            if status in (BatchStatus.FAILED, BatchStatus.EXPIRED):
                raise RuntimeError(f"Batch {batch_id} failed with status: {status.value}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Batch {batch_id} timed out after {timeout}s (status: {status.value})"
                )
            time.sleep(poll_interval)

    def _get_batch_provider(self, task: str) -> BatchCapable:
        """Get a BatchCapable provider for the given task."""
        task_config = self._config.get_task_config(task)
        provider = self._get_provider(task_config.provider)
        if not isinstance(provider, BatchCapable):
            raise ValueError(f"Provider '{task_config.provider}' does not support batch processing")
        return provider

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
