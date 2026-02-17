"""Fetcher → Normalizer → Summarizer 파이프라인 오케스트레이션."""

import logging
from collections.abc import Callable
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

    def run_daily(
        self,
        target_date: str,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> Path:
        """
        단일 날짜 전체 파이프라인: fetch → normalize → summarize(daily).

        Returns:
            daily summary 파일 경로

        Raises:
            StepFailedError: 어느 단계에서든 실패 시
        """
        logger.info("Pipeline start: %s (types=%s)", target_date, types)
        if progress:
            progress(f"Pipeline: fetch {target_date}")

        try:
            self._fetcher.fetch(target_date, types=types, progress=progress)
        except FetchError as e:
            raise StepFailedError("fetch", e) from e
        logger.info("Phase complete: fetch → normalize (%s)", target_date)
        if progress:
            progress(f"Pipeline: normalize {target_date}")

        try:
            self._normalizer.normalize(target_date, progress=progress)
        except NormalizeError as e:
            raise StepFailedError("normalize", e) from e
        logger.info("Phase complete: normalize → summarize (%s)", target_date)
        if progress:
            progress(f"Pipeline: summarize {target_date}")

        try:
            summary_path = self._summarizer.daily(target_date, progress=progress)
        except SummarizeError as e:
            raise StepFailedError("summarize", e) from e

        logger.info("Pipeline completed for %s → %s", target_date, summary_path)
        return summary_path

    def run_range(
        self,
        since: str,
        until: str,
        force: bool = False,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> list[dict]:
        """
        기간 범위 backfill using bulk operations.

        Uses fetch_range → normalize_range → daily_range for significantly
        fewer API calls compared to per-date run_daily.

        Returns:
            [{date, status, path?, error?}] 날짜별 결과
        """
        logger.info("Pipeline range: %s..%s (force=%s, types=%s)", since, until, force, types)
        start = date.fromisoformat(since)
        end = date.fromisoformat(until)
        if start > end:
            return []

        if progress:
            progress(f"Phase 1/3: Fetching {since}..{until}")
        fetch_results = self._fetcher.fetch_range(
            since, until, types=types, force=force, progress=progress
        )
        logger.info("Phase complete: fetch → normalize (%s..%s)", since, until)
        if progress:
            progress(f"Phase 2/3: Normalizing {since}..{until}")
        normalize_results = self._normalizer.normalize_range(
            since, until, force=force, progress=progress
        )
        logger.info("Phase complete: normalize → summarize (%s..%s)", since, until)
        if progress:
            progress(f"Phase 3/3: Summarizing {since}..{until}")
        summarize_results = self._summarizer.daily_range(
            since, until, force=force, progress=progress
        )

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
            all_skipped = True

            for step_name, by_date in phases:
                entry = by_date.get(d, {})
                if entry.get("status") == "failed":
                    failed_step = step_name
                    failed_error = entry.get("error", "unknown error")
                    all_skipped = False
                    break
                if entry.get("status") != "skipped":
                    all_skipped = False

            if failed_step:
                merged.append(
                    {
                        "date": d,
                        "status": "failed",
                        "error": f"Pipeline failed at '{failed_step}': {failed_error}",
                    }
                )
            elif all_skipped:
                merged.append({"date": d, "status": "skipped"})
            else:
                result: dict = {"date": d, "status": "success"}
                if self._config:
                    result["path"] = str(self._config.daily_summary_path(d))
                merged.append(result)

        return merged
