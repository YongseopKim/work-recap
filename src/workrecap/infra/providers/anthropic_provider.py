"""Anthropic provider implementation."""

from __future__ import annotations

import logging

import anthropic

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)

# Anthropic processing_status → BatchStatus mapping
_STATUS_MAP: dict[str, BatchStatus] = {
    "in_progress": BatchStatus.PROCESSING,
    "ended": BatchStatus.COMPLETED,
    "canceling": BatchStatus.FAILED,
    "expired": BatchStatus.EXPIRED,
}


class AnthropicProvider(LLMProvider, BatchCapable):
    """Anthropic Messages API provider with batch support."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def chat(
        self,
        model: str,
        system_prompt: str,
        user_content: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        cache_system_prompt: bool = False,
    ) -> tuple[str, TokenUsage]:
        messages: list[dict] = [{"role": "user", "content": user_content}]
        if json_mode:
            messages.append({"role": "assistant", "content": "["})

        # Build system parameter: structured block with cache_control, or plain string
        if cache_system_prompt:
            system: str | list = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system = system_prompt

        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens or 4096,
            system=system,
            messages=messages,
        )
        text = response.content[0].text
        if json_mode:
            text = "[" + text

        # Extract cache token stats from response
        resp_usage = response.usage
        cache_read = getattr(resp_usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(resp_usage, "cache_creation_input_tokens", 0) or 0

        usage = TokenUsage(
            prompt_tokens=resp_usage.input_tokens,
            completion_tokens=resp_usage.output_tokens,
            total_tokens=resp_usage.input_tokens + resp_usage.output_tokens,
            call_count=1,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        return text, usage

    def list_models(self) -> list[ModelInfo]:
        response = self._client.models.list()
        return [
            ModelInfo(
                id=m.id,
                name=getattr(m, "display_name", m.id),
                provider=self.provider_name,
            )
            for m in response.data
        ]

    # ── BatchCapable implementation ──

    def submit_batch(self, requests: list[BatchRequest]) -> str:
        api_requests = [self._build_batch_request(r) for r in requests]
        batch = self._client.messages.batches.create(requests=api_requests)
        logger.info("Submitted Anthropic batch: %s (%d requests)", batch.id, len(requests))
        return batch.id

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        batch = self._client.messages.batches.retrieve(batch_id)
        status = _STATUS_MAP.get(batch.processing_status, BatchStatus.PROCESSING)
        logger.debug("Anthropic batch %s status: %s", batch_id, status)
        return status

    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        results: list[BatchResult] = []
        for entry in self._client.messages.batches.results(batch_id):
            if entry.result.type == "succeeded":
                msg = entry.result.message
                text = msg.content[0].text
                usage_data = msg.usage
                cache_read = getattr(usage_data, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage_data, "cache_creation_input_tokens", 0) or 0
                results.append(
                    BatchResult(
                        custom_id=entry.custom_id,
                        content=text,
                        usage=TokenUsage(
                            prompt_tokens=usage_data.input_tokens,
                            completion_tokens=usage_data.output_tokens,
                            total_tokens=usage_data.input_tokens + usage_data.output_tokens,
                            call_count=1,
                            cache_read_tokens=cache_read,
                            cache_write_tokens=cache_write,
                        ),
                    )
                )
            else:
                error_msg = getattr(entry.result, "error", None)
                error_text = (
                    getattr(error_msg, "message", str(error_msg)) if error_msg else "Unknown"
                )
                results.append(BatchResult(custom_id=entry.custom_id, error=error_text))
        logger.info("Retrieved %d results from Anthropic batch %s", len(results), batch_id)
        return results

    @staticmethod
    def _build_batch_request(req: BatchRequest) -> dict:
        """Convert BatchRequest to Anthropic API format."""
        messages: list[dict] = [{"role": "user", "content": req.user_content}]
        if req.json_mode:
            messages.append({"role": "assistant", "content": "["})

        if req.cache_system_prompt:
            system: str | list = [
                {
                    "type": "text",
                    "text": req.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system = req.system_prompt

        return {
            "custom_id": req.custom_id,
            "params": {
                "model": req.model,
                "max_tokens": req.max_tokens or 4096,
                "system": system,
                "messages": messages,
            },
        }
