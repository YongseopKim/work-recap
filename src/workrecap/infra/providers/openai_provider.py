"""OpenAI provider implementation."""

from __future__ import annotations

import json
import logging

from openai import OpenAI

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)

# OpenAI batch status → BatchStatus mapping
_STATUS_MAP: dict[str, BatchStatus] = {
    "validating": BatchStatus.SUBMITTED,
    "in_progress": BatchStatus.PROCESSING,
    "finalizing": BatchStatus.PROCESSING,
    "completed": BatchStatus.COMPLETED,
    "failed": BatchStatus.FAILED,
    "cancelled": BatchStatus.FAILED,
    "cancelling": BatchStatus.FAILED,
    "expired": BatchStatus.EXPIRED,
}


class OpenAIProvider(LLMProvider, BatchCapable):
    """OpenAI API provider with batch support."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self._client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

    @property
    def provider_name(self) -> str:
        return "openai"

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
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        # cache_system_prompt is ignored — OpenAI auto-caches prompts ≥1024 tokens
        response = self._client.chat.completions.create(**kwargs)
        text = response.choices[0].message.content

        # Extract auto-cache stats from OpenAI response
        details = getattr(response.usage, "prompt_tokens_details", None)
        cached_tokens = getattr(details, "cached_tokens", 0) if details else 0

        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            call_count=1,
            cache_read_tokens=cached_tokens or 0,
        )
        return text, usage

    def list_models(self) -> list[ModelInfo]:
        response = self._client.models.list()
        return [ModelInfo(id=m.id, name=m.id, provider=self.provider_name) for m in response.data]

    # ── BatchCapable implementation ──

    def submit_batch(self, requests: list[BatchRequest]) -> str:
        jsonl_lines = [json.dumps(self._build_batch_line(r)) for r in requests]
        jsonl_content = "\n".join(jsonl_lines)

        uploaded = self._client.files.create(
            file=("batch_input.jsonl", jsonl_content, "application/jsonl"),
            purpose="batch",
        )
        batch = self._client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        logger.info("Submitted OpenAI batch: %s (%d requests)", batch.id, len(requests))
        return batch.id

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        batch = self._client.batches.retrieve(batch_id)
        status = _STATUS_MAP.get(batch.status, BatchStatus.PROCESSING)
        logger.debug("OpenAI batch %s status: %s", batch_id, status)
        return status

    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        batch = self._client.batches.retrieve(batch_id)
        if not batch.output_file_id:
            logger.warning("OpenAI batch %s has no output file", batch_id)
            return []

        content = self._client.files.content(batch.output_file_id)
        results: list[BatchResult] = []
        for line in content.text.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            resp = entry.get("response", {})
            body = resp.get("body", {})

            if resp.get("status_code") == 200:
                text = body["choices"][0]["message"]["content"]
                usage_data = body.get("usage", {})
                details = usage_data.get("prompt_tokens_details", {})
                cached = details.get("cached_tokens", 0) if details else 0
                results.append(
                    BatchResult(
                        custom_id=entry["custom_id"],
                        content=text,
                        usage=TokenUsage(
                            prompt_tokens=usage_data.get("prompt_tokens", 0),
                            completion_tokens=usage_data.get("completion_tokens", 0),
                            total_tokens=usage_data.get("total_tokens", 0),
                            call_count=1,
                            cache_read_tokens=cached or 0,
                        ),
                    )
                )
            else:
                error = body.get("error", {})
                results.append(
                    BatchResult(
                        custom_id=entry["custom_id"],
                        error=error.get("message", "Unknown error"),
                    )
                )
        logger.info("Retrieved %d results from OpenAI batch %s", len(results), batch_id)
        return results

    @staticmethod
    def _build_batch_line(req: BatchRequest) -> dict:
        """Convert BatchRequest to OpenAI batch JSONL line format."""
        body: dict = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": req.system_prompt},
                {"role": "user", "content": req.user_content},
            ],
        }
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        if req.max_tokens is not None:
            body["max_completion_tokens"] = req.max_tokens
        return {
            "custom_id": req.custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": body,
        }
