import pytest

from workrecap.exceptions import (
    FetchError,
    WorkRecapError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)


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

    def test_step_failed_catchable_by_base_class(self):
        """StepFailedError도 WorkRecapError로 catch 가능."""
        with pytest.raises(WorkRecapError):
            raise StepFailedError("fetch", FetchError("test"))
