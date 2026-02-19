"""Google Gemini provider implementation using google-genai SDK."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.infra.providers.batch_mixin import (
    BatchCapable,
    BatchRequest,
    BatchResult,
    BatchStatus,
)
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)

# Gemini job state → BatchStatus mapping
_STATUS_MAP: dict[str, BatchStatus] = {
    "JOB_STATE_PENDING": BatchStatus.SUBMITTED,
    "JOB_STATE_RUNNING": BatchStatus.PROCESSING,
    "JOB_STATE_SUCCEEDED": BatchStatus.COMPLETED,
    "JOB_STATE_FAILED": BatchStatus.FAILED,
    "JOB_STATE_CANCELLED": BatchStatus.FAILED,
    "JOB_STATE_PAUSED": BatchStatus.PROCESSING,
}


class GeminiProvider(LLMProvider, BatchCapable):
    """Google Gemini API provider with batch support."""

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "gemini"

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
        # cache_system_prompt is accepted but not acted on — Gemini uses implicit
        # caching (automatic since 2025-05, Gemini 2.5+). Requests sharing a common
        # prefix get cache hits automatically. Min tokens: Flash 1024 / Pro 2048.
        # cached_content_token_count in usage_metadata shows actual cache hits.
        config_kwargs: dict = {"system_instruction": system_prompt}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        response = self._client.models.generate_content(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        text = response.text
        meta = response.usage_metadata
        usage = TokenUsage(
            prompt_tokens=meta.prompt_token_count,
            completion_tokens=meta.candidates_token_count,
            total_tokens=meta.total_token_count,
            call_count=1,
            cache_read_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
        )
        return text, usage

    def list_models(self) -> list[ModelInfo]:
        models = self._client.models.list()
        return [
            ModelInfo(
                id=m.name,
                name=getattr(m, "display_name", m.name),
                provider=self.provider_name,
            )
            for m in models
        ]

    # ── BatchCapable implementation ──

    def submit_batch(self, requests: list[BatchRequest]) -> str:
        if not requests:
            raise ValueError("Cannot submit empty batch")
        model = requests[0].model
        src = [self._build_batch_entry(r) for r in requests]
        job = self._client.batches.create(model=model, src=src)
        logger.info("Submitted Gemini batch: %s (%d requests)", job.name, len(requests))
        return job.name

    def get_batch_status(self, batch_id: str) -> BatchStatus:
        job = self._client.batches.get(name=batch_id)
        status = _STATUS_MAP.get(str(job.state), BatchStatus.PROCESSING)
        logger.debug("Gemini batch %s status: %s", batch_id, status)
        return status

    def get_batch_results(self, batch_id: str) -> list[BatchResult]:
        job = self._client.batches.get(name=batch_id)
        results: list[BatchResult] = []

        for entry in getattr(job, "responses", []) or []:
            key = getattr(entry, "key", None)
            response = getattr(entry, "response", None)
            if response is None:
                results.append(BatchResult(custom_id=key or "", error="No response for entry"))
                continue

            try:
                text = response.candidates[0].content.parts[0].text
                meta = response.usage_metadata
                results.append(
                    BatchResult(
                        custom_id=key or "",
                        content=text,
                        usage=TokenUsage(
                            prompt_tokens=meta.prompt_token_count,
                            completion_tokens=meta.candidates_token_count,
                            total_tokens=meta.total_token_count,
                            call_count=1,
                        ),
                    )
                )
            except (AttributeError, IndexError) as e:
                results.append(
                    BatchResult(custom_id=key or "", error=f"Failed to parse response: {e}")
                )

        logger.info("Retrieved %d results from Gemini batch %s", len(results), batch_id)
        return results

    @staticmethod
    def _build_batch_entry(req: BatchRequest) -> dict:
        """Convert BatchRequest to Gemini inline batch format."""
        entry: dict = {
            "key": req.custom_id,
            "contents": [
                {
                    "parts": [{"text": req.user_content}],
                    "role": "user",
                }
            ],
        }
        config: dict = {"system_instruction": req.system_prompt}
        if req.json_mode:
            config["response_mime_type"] = "application/json"
        if req.max_tokens is not None:
            config["max_output_tokens"] = req.max_tokens
        if config:
            entry["config"] = config
        return entry
