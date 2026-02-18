# Phase 4: OrchestratorService 상세 설계

## 목적

Fetcher → Normalizer → Summarizer 파이프라인을 단일 호출로 실행한다.
단일 날짜(`run_daily`) 및 기간 범위(`run_range`) 실행을 지원하며,
각 단계 실패 시 이전 산출물을 보존하고 실패 정보를 명확히 전달한다.

---

## 위치

`src/workrecap/services/orchestrator.py`

## 의존성

- `workrecap.services.fetcher.FetcherService`
- `workrecap.services.normalizer.NormalizerService`
- `workrecap.services.summarizer.SummarizerService`
- `workrecap.exceptions.StepFailedError, FetchError, NormalizeError, SummarizeError`

---

## 상세 구현

```python
import logging
from datetime import date, timedelta
from pathlib import Path

from workrecap.exceptions import (
    FetchError, NormalizeError, SummarizeError, StepFailedError,
)
from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(
        self,
        fetcher: FetcherService,
        normalizer: NormalizerService,
        summarizer: SummarizerService,
    ) -> None:
        self._fetcher = fetcher
        self._normalizer = normalizer
        self._summarizer = summarizer

    def run_daily(self, target_date: str) -> Path:
        """
        단일 날짜 전체 파이프라인: fetch → normalize → summarize(daily).

        Args:
            target_date: "YYYY-MM-DD"

        Returns:
            daily summary 파일 경로

        Raises:
            StepFailedError: 어느 단계에서든 실패 시
                - step: "fetch" | "normalize" | "summarize"
                - cause: 원래 예외
                - 실패 이전 단계의 산출물은 보존됨
        """
        # Step 1: Fetch
        try:
            self._fetcher.fetch(target_date)
        except FetchError as e:
            raise StepFailedError("fetch", e) from e

        # Step 2: Normalize
        try:
            self._normalizer.normalize(target_date)
        except NormalizeError as e:
            raise StepFailedError("normalize", e) from e

        # Step 3: Summarize
        try:
            summary_path = self._summarizer.daily(target_date)
        except SummarizeError as e:
            raise StepFailedError("summarize", e) from e

        logger.info("Pipeline completed for %s → %s", target_date, summary_path)
        return summary_path

    def run_range(self, since: str, until: str) -> list[dict]:
        """
        기간 범위 backfill. since ~ until (inclusive) 날짜별 run_daily 실행.

        Args:
            since: 시작일 "YYYY-MM-DD" (inclusive)
            until: 종료일 "YYYY-MM-DD" (inclusive)

        Returns:
            [{date, status, path?, error?}] 날짜별 결과 리스트
            - status: "success" | "failed"
            - path: 성공 시 summary 파일 경로
            - error: 실패 시 에러 메시지

        실패한 날짜는 스킵하고 다음 날짜 계속 처리.
        """
        results: list[dict] = []

        start = date.fromisoformat(since)
        end = date.fromisoformat(until)
        current = start

        while current <= end:
            date_str = current.isoformat()
            try:
                path = self.run_daily(date_str)
                results.append({
                    "date": date_str,
                    "status": "success",
                    "path": str(path),
                })
                logger.info("✓ %s", date_str)
            except StepFailedError as e:
                results.append({
                    "date": date_str,
                    "status": "failed",
                    "error": str(e),
                })
                logger.warning("✗ %s: %s", date_str, e)

            current += timedelta(days=1)

        succeeded = sum(1 for r in results if r["status"] == "success")
        logger.info(
            "Range complete: %d/%d succeeded (%s ~ %s)",
            succeeded, len(results), since, until,
        )
        return results
```

---

## 에러 보존 보장

파이프라인은 단순 순차 실행이므로, 단계 N이 실패하면 단계 1~(N-1)의 산출물은
이미 파일 시스템에 저장된 상태.

| 실패 단계 | 보존된 산출물 |
|---|---|
| fetch | (없음) |
| normalize | `data/raw/{date}/prs.json` |
| summarize | `data/raw/` + `data/normalized/{date}/` |

재실행 시 동일 파일을 덮어쓰므로 (멱등성), 사용자는 실패한 단계만 수정 후 재시도 가능.

---

## 테스트 명세

### test_orchestrator.py

Fetcher/Normalizer/Summarizer를 모두 mock하여 Orchestrator 로직을 검증한다.

```python
"""tests/unit/test_orchestrator.py"""

class TestRunDaily:
    def test_calls_three_steps_in_order(self, orchestrator, mocks):
        """fetch → normalize → summarize 순서로 호출."""

    def test_returns_summary_path(self, orchestrator, mocks):
        """성공 시 summary 파일 경로 반환."""

    def test_fetch_failure(self, orchestrator, mocks):
        """fetch 실패 → StepFailedError(step='fetch')."""

    def test_normalize_failure_preserves_raw(self, orchestrator, mocks):
        """normalize 실패 → StepFailedError(step='normalize').
           fetch는 이미 호출되었으므로 raw 산출물 보존."""

    def test_summarize_failure_preserves_normalized(self, orchestrator, mocks):
        """summarize 실패 → StepFailedError(step='summarize').
           fetch + normalize 이미 완료."""

class TestRunRange:
    def test_processes_all_dates(self, orchestrator, mocks):
        """since~until 범위의 모든 날짜 처리."""

    def test_failure_skips_and_continues(self, orchestrator, mocks):
        """특정 날짜 실패해도 나머지 계속 처리."""

    def test_result_format(self, orchestrator, mocks):
        """결과에 date, status, path/error 포함."""

    def test_single_day(self, orchestrator, mocks):
        """since == until → 1일만 처리."""

    def test_empty_range(self, orchestrator, mocks):
        """since > until → 빈 결과."""
```

### Fixtures

```python
@pytest.fixture
def mocks():
    return {
        "fetcher": Mock(spec=FetcherService),
        "normalizer": Mock(spec=NormalizerService),
        "summarizer": Mock(spec=SummarizerService),
    }

@pytest.fixture
def orchestrator(mocks):
    return OrchestratorService(
        mocks["fetcher"], mocks["normalizer"], mocks["summarizer"]
    )
```

---

## ToDo

| # | 작업 | 테스트 |
|---|---|---|
| 4.1 | `run_daily()` — 3단계 순차 실행 + StepFailedError 래핑 | TestRunDaily |
| 4.2 | `run_range()` — 기간 순회 + 실패 스킵 + 결과 리스트 | TestRunRange |
