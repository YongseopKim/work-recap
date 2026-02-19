"""Google Gemini provider implementation using google-genai SDK."""

import logging

from google import genai
from google.genai import types

from workrecap.infra.providers.base import LLMProvider, ModelInfo
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""

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
