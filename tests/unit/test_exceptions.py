import pytest

from git_recap.exceptions import (
    FetchError,
    GitRecapError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_git_recap_error(self):
        """모든 커스텀 예외가 GitRecapError의 서브클래스."""
        assert issubclass(FetchError, GitRecapError)
        assert issubclass(NormalizeError, GitRecapError)
        assert issubclass(SummarizeError, GitRecapError)
        assert issubclass(StepFailedError, GitRecapError)

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
        """GitRecapError로 모든 하위 예외를 catch할 수 있다."""
        with pytest.raises(GitRecapError):
            raise FetchError("test")

    def test_step_failed_catchable_by_base_class(self):
        """StepFailedError도 GitRecapError로 catch 가능."""
        with pytest.raises(GitRecapError):
            raise StepFailedError("fetch", FetchError("test"))
