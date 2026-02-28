"""Fetcher → Normalizer → Summarizer 파이프라인 오케스트레이션."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from workrecap.config import AppConfig
from workrecap.exceptions import (
    FetchError,
    NormalizeError,
    StepFailedError,
    SummarizeError,
)
from workrecap.services.fetcher import FetcherService
from workrecap.services.normalizer import NormalizerService
from workrecap.services.summarizer import SummarizerService

if TYPE_CHECKING:
    from workrecap.services.protocols import DataSourceFetcher, DataSourceNormalizer
    from workrecap.services.storage import StorageService

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(
        self,
        fetcher: FetcherService | dict[str, DataSourceFetcher],
        normalizer: NormalizerService | dict[str, DataSourceNormalizer],
        summarizer: SummarizerService,
        *,
        config: AppConfig | None = None,
        storage: StorageService | None = None,
    ) -> None:
        # 하위호환: 단일 fetcher/normalizer → dict 래핑
        if isinstance(fetcher, dict):
            self._fetchers = fetcher
        else:
            self._fetchers = {"github": fetcher}
        if isinstance(normalizer, dict):
            self._normalizers = normalizer
        else:
            self._normalizers = {"github": normalizer}
        # 기본 소스 (단일 소스 호환용)
        self._fetcher = fetcher if not isinstance(fetcher, dict) else next(iter(fetcher.values()))
        self._normalizer = (
            normalizer if not isinstance(normalizer, dict) else next(iter(normalizer.values()))
        )
        self._summarizer = summarizer
        self._config = config
        self._storage = storage

    def run_daily(
        self,
        target_date: str,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> Path:
        """
        단일 날짜 전체 파이프라인: fetch → normalize → summarize(daily).
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
            _, _, activities, stats = self._normalizer.normalize(target_date, progress=progress)
        except NormalizeError as e:
            raise StepFailedError("normalize", e) from e

        # Storage: activities + stats
        if self._storage and activities is not None:
            self._safe_storage_call(
                "save_activities",
                self._storage.save_activities_sync,
                target_date,
                [self._activity_to_dict(a) for a in activities],
                self._stats_to_dict(stats) if stats else {},
            )

        logger.info("Phase complete: normalize → summarize (%s)", target_date)
        if progress:
            progress(f"Pipeline: summarize {target_date}")

        try:
            summary_path = self._summarizer.daily(target_date, progress=progress)
        except SummarizeError as e:
            raise StepFailedError("summarize", e) from e

        # Storage: summary
        if self._storage and summary_path.exists():
            content = summary_path.read_text(encoding="utf-8")
            self._safe_storage_call(
                "save_summary",
                self._storage.save_summary_sync,
                "daily",
                target_date,
                content,
            )

        logger.info("Pipeline completed for %s → %s", target_date, summary_path)
        return summary_path

    @staticmethod
    def _safe_storage_call(label: str, fn: Callable, *args) -> None:
        """Storage 호출을 try/except로 감싸서 실패 시 로깅만."""
        try:
            fn(*args)
        except Exception as e:
            logger.warning("Storage %s failed: %s", label, e)

    @staticmethod
    def _activity_to_dict(activity) -> dict:
        """Activity dataclass → dict 변환."""
        from dataclasses import asdict

        return asdict(activity)

    @staticmethod
    def _stats_to_dict(stats) -> dict:
        """DailyStats dataclass → dict 변환."""
        from dataclasses import asdict

        return asdict(stats)

    def run_range(
        self,
        since: str,
        until: str,
        force: bool = False,
        types: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
        max_workers: int = 1,
        batch: bool = False,
    ) -> list[dict]:
        """
        기간 범위 backfill using bulk operations.
        """
        logger.info(
            "Pipeline range: %s..%s (force=%s, types=%s, workers=%d, batch=%s)",
            since,
            until,
            force,
            types,
            max_workers,
            batch,
        )
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
            since, until, force=force, progress=progress, max_workers=max_workers, batch=batch
        )

        logger.info("Phase complete: normalize → summarize (%s..%s)", since, until)
        if progress:
            progress(f"Phase 3/3: Summarizing {since}..{until}")
        summarize_results = self._summarizer.daily_range(
            since, until, force=force, progress=progress, max_workers=max_workers, batch=batch
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

    def run_weekly(self, year: int, week: int, force: bool = False) -> Path:
        """Weekly summary 생성."""
        return self._summarizer.weekly(year, week, force)

    def run_monthly(self, year: int, month: int, force: bool = False) -> Path:
        """Monthly summary 생성."""
        return self._summarizer.monthly(year, month, force)

    def run_yearly(self, year: int, force: bool = False) -> Path:
        """Yearly summary 생성."""
        return self._summarizer.yearly(year, force)

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
