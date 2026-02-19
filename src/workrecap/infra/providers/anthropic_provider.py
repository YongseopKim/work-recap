"""Anthropic provider implementation."""

import logging

import anthropic

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API provider."""

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
