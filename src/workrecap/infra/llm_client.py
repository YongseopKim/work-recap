"""Provider-agnostic LLM client.

.. deprecated::
    Use :class:`workrecap.infra.llm_router.LLMRouter` instead.
    This module is kept for backward compatibility and reference.
"""

import logging
import threading
import time

import anthropic
from openai import OpenAI

from workrecap.exceptions import SummarizeError
from workrecap.models import TokenUsage

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 120.0
LLM_MAX_RETRIES = 3


class LLMClient:
    """Provider-agnostic LLM client. OpenAI와 Anthropic을 동일 인터페이스로 호출."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        *,
        timeout: float = LLM_TIMEOUT,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> None:
        self._provider = provider
        self._model = model
        self._usage = TokenUsage()
        self._usage_lock = threading.Lock()

        if provider == "openai":
            self._openai = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        elif provider == "anthropic":
            self._anthropic = anthropic.Anthropic(
                api_key=api_key,
                timeout=timeout,
                max_retries=max_retries,
            )
        else:
            raise SummarizeError(f"Unsupported LLM provider: {provider}")

    @property
    def usage(self) -> TokenUsage:
        """누적 토큰 사용량 반환."""
        return self._usage

    def chat(self, system_prompt: str, user_content: str) -> str:
        """
        LLM Chat Completion 호출.

        Returns:
            LLM 응답 텍스트

        Raises:
            SummarizeError: API 호출 실패
        """
        logger.info("LLM call: provider=%s model=%s", self._provider, self._model)
        logger.debug(
            "LLM request: system_prompt=%d chars, user_content=%d chars",
            len(system_prompt),
            len(user_content),
        )
        try:
            t0 = time.monotonic()
            if self._provider == "openai":
                text, call_usage = self._chat_openai(system_prompt, user_content)
            else:
                text, call_usage = self._chat_anthropic(system_prompt, user_content)
            elapsed = time.monotonic() - t0
            with self._usage_lock:
                self._usage = self._usage + call_usage
            logger.info(
                "LLM tokens: prompt=%d completion=%d total=%d (%.1fs)",
                call_usage.prompt_tokens,
                call_usage.completion_tokens,
                call_usage.total_tokens,
                elapsed,
            )
            logger.debug("LLM response: %d chars", len(text))
            return text
        except SummarizeError:
            raise
        except Exception as e:
            raise SummarizeError(f"LLM API call failed: {e}") from e

    def _chat_openai(self, system_prompt: str, user_content: str) -> tuple[str, TokenUsage]:
        response = self._openai.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        text = response.choices[0].message.content
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            call_count=1,
        )
        return text, usage

    def _chat_anthropic(self, system_prompt: str, user_content: str) -> tuple[str, TokenUsage]:
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content},
            ],
        )
        text = response.content[0].text
        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            call_count=1,
        )
        return text, usage
