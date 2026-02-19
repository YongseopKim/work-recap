"""OpenAI provider implementation."""

import logging

from openai import OpenAI

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

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
            kwargs["max_tokens"] = max_tokens
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
