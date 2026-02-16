# Phase 3-1: LLMClient 상세 설계

## 목적

Provider-agnostic LLM 클라이언트. OpenAI와 Anthropic API를 동일한 인터페이스로
호출할 수 있게 하여 SummarizerService가 특정 provider에 의존하지 않도록 한다.

---

## 위치

`src/git_recap/infra/llm_client.py`

## 의존성

- `openai` (OpenAI SDK)
- `anthropic` (Anthropic SDK)
- `git_recap.exceptions.SummarizeError`

---

## 상세 구현

```python
import logging

from git_recap.exceptions import SummarizeError

logger = logging.getLogger(__name__)


class LLMClient:
    """Provider-agnostic LLM client."""

    def __init__(self, provider: str, api_key: str, model: str) -> None:
        """
        Args:
            provider: "openai" | "anthropic"
            api_key: API key
            model: 모델 ID (e.g., "gpt-4o-mini", "claude-sonnet-4-5-20250929")
        """
        self._provider = provider
        self._model = model

        if provider == "openai":
            from openai import OpenAI
            self._openai = OpenAI(api_key=api_key)
        elif provider == "anthropic":
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=api_key)
        else:
            raise SummarizeError(f"Unsupported LLM provider: {provider}")

    def chat(self, system_prompt: str, user_content: str) -> str:
        """
        LLM Chat Completion 호출.

        Args:
            system_prompt: 시스템 프롬프트 (역할/지시사항)
            user_content: 사용자 메시지 (데이터/질문)

        Returns:
            LLM 응답 텍스트

        Raises:
            SummarizeError: API 호출 실패
        """
        try:
            if self._provider == "openai":
                return self._chat_openai(system_prompt, user_content)
            else:
                return self._chat_anthropic(system_prompt, user_content)
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
```

---

## 테스트 전략

LLMClient는 외부 API를 호출하므로 unit test에서는 SDK를 mock한다.
테스트 핵심: provider 분기, 에러 래핑, 응답 추출.

```python
"""tests/unit/test_llm_client.py"""

class TestLLMClientInit:
    def test_openai_provider(self, monkeypatch):
        """openai provider 초기화 시 OpenAI 클라이언트 생성."""

    def test_anthropic_provider(self, monkeypatch):
        """anthropic provider 초기화 시 Anthropic 클라이언트 생성."""

    def test_unsupported_provider(self):
        """미지원 provider → SummarizeError."""

class TestChat:
    def test_openai_chat(self, monkeypatch):
        """OpenAI API 호출 → 응답 텍스트 반환."""

    def test_anthropic_chat(self, monkeypatch):
        """Anthropic API 호출 → 응답 텍스트 반환."""

    def test_api_error_wrapped(self, monkeypatch):
        """API 에러 → SummarizeError로 래핑."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 3.1.1 | LLMClient 초기화 (provider 분기) | TestLLMClientInit |
| 3.1.2 | chat() — OpenAI/Anthropic 호출 + 에러 래핑 | TestChat |
