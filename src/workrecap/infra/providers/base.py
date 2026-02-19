"""Base class for LLM providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from workrecap.models import TokenUsage


@dataclass
class ModelInfo:
    """Metadata for an available model."""

    id: str
    name: str
    provider: str


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Each concrete provider wraps a specific SDK (OpenAI, Anthropic, Gemini, etc.)
    and exposes a uniform chat() interface returning (text, TokenUsage).
    """

    @abstractmethod
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
        """Send a chat completion request.

        Args:
            model: Model identifier (e.g. "gpt-4o-mini").
            system_prompt: System message.
            user_content: User message.
            json_mode: If True, constrain output to valid JSON.
            max_tokens: Maximum output tokens. None = provider default.
            cache_system_prompt: If True, enable prompt caching for system prompt.
                Anthropic: uses cache_control blocks. OpenAI/Gemini: auto-cached (ignored).

        Returns:
            (response_text, token_usage) tuple.
        """

    def list_models(self) -> list[ModelInfo]:
        """List available models from this provider. Default: empty list."""
        return []

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for this provider (e.g. 'openai')."""
