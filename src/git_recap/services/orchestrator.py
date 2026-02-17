"""Fetcher → Normalizer → Summarizer 파이프라인 오케스트레이션."""

import logging
from datetime import date
from pathlib import Path

from git_recap.config import AppConfig
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
        *,
        config: AppConfig | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._normalizer = normalizer
        self._summarizer = summarizer
        self._config = config

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
        기간 범위 backfill using bulk operations.

        Uses fetch_range → normalize_range → daily_range for significantly
        fewer API calls compared to per-date run_daily.

        Returns:
            [{date, status, path?, error?}] 날짜별 결과
        """
        start = date.fromisoformat(since)
        end = date.fromisoformat(until)
        if start > end:
            return []

        fetch_results = self._fetcher.fetch_range(since, until)
        normalize_results = self._normalizer.normalize_range(since, until)
        summarize_results = self._summarizer.daily_range(since, until)

        results = self._merge_results(fetch_results, normalize_results, summarize_results)

        succeeded = sum(1 for r in results if r["status"] == "success")
        logger.info(
            "Range complete: %d/%d succeeded (%s ~ %s)",
            succeeded,
            len(results),
            since,
            until,
        )
        return results

    def _merge_results(
        self,
        fetch_results: list[dict],
        normalize_results: list[dict],
        summarize_results: list[dict],
    ) -> list[dict]:
        """Merge per-phase results into unified per-date results."""
        # Index by date
        fetch_by_date = {r["date"]: r for r in fetch_results}
        norm_by_date = {r["date"]: r for r in normalize_results}
        summ_by_date = {r["date"]: r for r in summarize_results}

        # Collect all dates, preserving order from fetch_results
        all_dates: list[str] = []
        seen: set[str] = set()
        for results_list in (fetch_results, normalize_results, summarize_results):
            for r in results_list:
                if r["date"] not in seen:
                    all_dates.append(r["date"])
                    seen.add(r["date"])

        merged: list[dict] = []
        phases = [
            ("fetch", fetch_by_date),
            ("normalize", norm_by_date),
            ("summarize", summ_by_date),
        ]

        for d in sorted(all_dates):
            failed_step = None
            failed_error = None

            for step_name, by_date in phases:
                entry = by_date.get(d, {})
                if entry.get("status") == "failed":
                    failed_step = step_name
                    failed_error = entry.get("error", "unknown error")
                    break

            if failed_step:
                merged.append(
                    {
                        "date": d,
                        "status": "failed",
                        "error": f"Pipeline failed at '{failed_step}': {failed_error}",
                    }
                )
            else:
                result: dict = {"date": d, "status": "success"}
                if self._config:
                    result["path"] = str(self._config.daily_summary_path(d))
                merged.append(result)

        return merged
