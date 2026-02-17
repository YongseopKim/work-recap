"""Provider-agnostic LLM client."""

import logging

import anthropic
from openai import OpenAI

from git_recap.exceptions import SummarizeError

logger = logging.getLogger(__name__)


class LLMClient:
    """Provider-agnostic LLM client. OpenAI와 Anthropic을 동일 인터페이스로 호출."""

    def __init__(self, provider: str, api_key: str, model: str) -> None:
        self._provider = provider
        self._model = model

        if provider == "openai":
            self._openai = OpenAI(api_key=api_key)
        elif provider == "anthropic":
            self._anthropic = anthropic.Anthropic(api_key=api_key)
        else:
            raise SummarizeError(f"Unsupported LLM provider: {provider}")

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
            if self._provider == "openai":
                result = self._chat_openai(system_prompt, user_content)
            else:
                result = self._chat_anthropic(system_prompt, user_content)
            logger.debug("LLM response: %d chars", len(result))
            return result
        except SummarizeError:
            raise
        except Exception as e:
            raise SummarizeError(f"LLM API call failed: {e}") from e

    def _chat_openai(self, system_prompt: str, user_content: str) -> str:
        response = self._openai.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content

    def _chat_anthropic(self, system_prompt: str, user_content: str) -> str:
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content},
            ],
        )
        return response.content[0].text
