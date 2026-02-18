# Phase 0-3: exceptions.py 상세 설계

## 목적

work-recap 전역 예외 계층을 정의한다.
각 서비스는 자신만의 예외 타입을 사용하고, Orchestrator는 이를 `StepFailedError`로 감싸서
CLI/API 레이어에서 일관된 에러 처리가 가능하게 한다.

---

## 위치

`src/workrecap/exceptions.py`

## 의존성

없음 (표준 라이브러리만 사용)

---

## 상세 구현

```python
"""work-recap 예외 계층.

계층 구조:
    WorkRecapError
    ├── FetchError          (Fetcher: GHES API 실패)
    ├── NormalizeError      (Normalizer: 변환 실패)
    ├── SummarizeError      (Summarizer: LLM 호출 실패)
    └── StepFailedError     (Orchestrator: 파이프라인 단계 실패)
"""


class WorkRecapError(Exception):
    """work-recap의 모든 예외의 기반 클래스."""


class FetchError(WorkRecapError):
    """GHES API 호출 또는 raw 데이터 저장 실패."""
    step = "fetch"


class NormalizeError(WorkRecapError):
    """Raw 데이터 → Activity 변환 실패."""
    step = "normalize"


class SummarizeError(WorkRecapError):
    """LLM 호출 또는 summary 생성 실패."""
    step = "summarize"


class StepFailedError(WorkRecapError):
    """파이프라인 특정 단계 실패. Orchestrator가 발생시킨다.

    Attributes:
        step: 실패한 단계 이름 ("fetch", "normalize", "summarize")
        cause: 원래 발생한 예외
    """
    def __init__(self, step: str, cause: Exception):
        self.step = step
        self.cause = cause
        super().__init__(f"Pipeline failed at '{step}': {cause}")
```

---

## 에러 매핑

| 예외 | CLI 동작 | API HTTP 상태 |
|---|---|---|
| FetchError | stderr + exit(1) | 502 Bad Gateway |
| NormalizeError | stderr + exit(1) | 500 Internal Server Error |
| SummarizeError | stderr + exit(1) | 503 Service Unavailable |
| StepFailedError | stderr + exit(1) | cause에 따라 위 규칙 적용 |
| WorkRecapError (기타) | stderr + exit(1) | 500 Internal Server Error |

---

## 테스트 명세

### test_exceptions.py

```python
"""tests/unit/test_exceptions.py"""

class TestExceptionHierarchy:
    def test_all_inherit_from_workrecap_error(self):
        """모든 커스텀 예외가 WorkRecapError의 서브클래스."""
        assert issubclass(FetchError, WorkRecapError)
        assert issubclass(NormalizeError, WorkRecapError)
        assert issubclass(SummarizeError, WorkRecapError)
        assert issubclass(StepFailedError, WorkRecapError)

    def test_step_attribute(self):
        """FetchError, NormalizeError, SummarizeError에 step 속성이 있다."""
        assert FetchError.step == "fetch"
        assert NormalizeError.step == "normalize"
        assert SummarizeError.step == "summarize"

    def test_step_failed_error_wraps_cause(self):
        """StepFailedError가 원인 예외를 보존한다."""
        cause = FetchError("GHES timeout")
        err = StepFailedError(step="fetch", cause=cause)
        assert err.step == "fetch"
        assert err.cause is cause
        assert "fetch" in str(err)
        assert "GHES timeout" in str(err)

    def test_step_failed_error_message_format(self):
        """StepFailedError 메시지 포맷 확인."""
        cause = NormalizeError("invalid JSON")
        err = StepFailedError(step="normalize", cause=cause)
        assert str(err) == "Pipeline failed at 'normalize': invalid JSON"

    def test_catchable_by_base_class(self):
        """WorkRecapError로 모든 하위 예외를 catch할 수 있다."""
        with pytest.raises(WorkRecapError):
            raise FetchError("test")
        with pytest.raises(WorkRecapError):
            raise StepFailedError("fetch", FetchError("test"))
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 0.3.1 | WorkRecapError + 3개 서비스 예외 클래스 구현 | test_all_inherit, test_step_attribute |
| 0.3.2 | StepFailedError 구현 (step, cause 보존) | test_step_failed_error_wraps_cause, test_message_format |
| 0.3.3 | catch 동작 검증 | test_catchable_by_base_class |
