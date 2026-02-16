"""Fetcher → Normalizer → Summarizer 파이프라인 오케스트레이션."""

import logging
from datetime import date, timedelta
from pathlib import Path

from git_recap.exceptions import (
    FetchError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.summarizer import SummarizerService

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

        Returns:
            daily summary 파일 경로

        Raises:
            StepFailedError: 어느 단계에서든 실패 시
        """
        try:
            self._fetcher.fetch(target_date)
        except FetchError as e:
            raise StepFailedError("fetch", e) from e

        try:
            self._normalizer.normalize(target_date)
        except NormalizeError as e:
            raise StepFailedError("normalize", e) from e

        try:
            summary_path = self._summarizer.daily(target_date)
        except SummarizeError as e:
            raise StepFailedError("summarize", e) from e

        logger.info("Pipeline completed for %s → %s", target_date, summary_path)
        return summary_path

    def run_range(self, since: str, until: str) -> list[dict]:
        """
        기간 범위 backfill. since ~ until (inclusive).

        Returns:
            [{date, status, path?, error?}] 날짜별 결과
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
            except StepFailedError as e:
                results.append({
                    "date": date_str,
                    "status": "failed",
                    "error": str(e),
                })
                logger.warning("Failed %s: %s", date_str, e)

            current += timedelta(days=1)

        succeeded = sum(1 for r in results if r["status"] == "success")
        logger.info(
            "Range complete: %d/%d succeeded (%s ~ %s)",
            succeeded, len(results), since, until,
        )
        return results
