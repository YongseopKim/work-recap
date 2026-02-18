"""Custom OpenAI-compatible provider (Ollama, vLLM, LM Studio, etc.)."""

import logging

from openai import OpenAI

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)


class CustomProvider(LLMProvider):
    """OpenAI-compatible API provider with custom base_url."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def provider_name(self) -> str:
        return "custom"

    def chat(self, model: str, system_prompt: str, user_content: str) -> tuple[str, TokenUsage]:
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        text = response.choices[0].message.content
        # Some local models don't return usage stats
        if response.usage is not None:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                call_count=1,
            )
        else:
            usage = TokenUsage(call_count=1)
        return text, usage

    def list_models(self) -> list[ModelInfo]:
        response = self._client.models.list()
        return [ModelInfo(id=m.id, name=m.id, provider=self.provider_name) for m in response.data]
