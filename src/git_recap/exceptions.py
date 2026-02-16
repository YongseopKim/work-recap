"""git-recap 예외 계층.

계층 구조:
    GitRecapError
    ├── FetchError          (Fetcher: GHES API 실패)
    ├── NormalizeError      (Normalizer: 변환 실패)
    ├── SummarizeError      (Summarizer: LLM 호출 실패)
    └── StepFailedError     (Orchestrator: 파이프라인 단계 실패)
"""


class GitRecapError(Exception):
    """git-recap의 모든 예외의 기반 클래스."""


class FetchError(GitRecapError):
    """GHES API 호출 또는 raw 데이터 저장 실패."""

    step = "fetch"


class NormalizeError(GitRecapError):
    """Raw 데이터 → Activity 변환 실패."""

    step = "normalize"


class SummarizeError(GitRecapError):
    """LLM 호출 또는 summary 생성 실패."""

    step = "summarize"


class StepFailedError(GitRecapError):
    """파이프라인 특정 단계 실패. Orchestrator가 발생시킨다."""

    def __init__(self, step: str, cause: Exception):
        self.step = step
        self.cause = cause
        super().__init__(f"Pipeline failed at '{step}': {cause}")
