"""Summarize 트리거 엔드포인트 — daily/weekly/monthly/yearly 생성."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from workrecap.api.deps import get_config, get_job_store
from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig
from workrecap.api.deps import get_llm_router
from workrecap.models import JobStatus
from workrecap.services.daily_state import DailyStateStore
from workrecap.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)

router = APIRouter()


class SummarizeDailyRangeRequest(BaseModel):
    since: str
    until: str
    force: bool = False
    max_workers: int | None = None
    batch: bool = False


class SummarizeWeeklyRequest(BaseModel):
    year: int
    week: int
    force: bool = False


class SummarizeMonthlyRequest(BaseModel):
    year: int
    month: int
    force: bool = False


class SummarizeYearlyRequest(BaseModel):
    year: int
    force: bool = False


def _summarize_daily_single_task(
    job_id: str,
    target_date: str,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
) -> None:
    """BackgroundTask: 단일 날짜 daily summary 생성."""
    logger.info("Background task start: summarize daily %s (job=%s)", target_date, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = get_llm_router(config)
        ds = DailyStateStore(config.daily_state_path)
        service = SummarizerService(config, llm, daily_state=ds)
        path = service.daily(target_date, force=force)
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        logger.warning("Background task failed: summarize daily %s: %s", target_date, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _summarize_daily_range_task(
    job_id: str,
    since: str,
    until: str,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
    max_workers: int = 1,
    batch: bool = False,
) -> None:
    """BackgroundTask: 기간 범위 daily summary 생성."""
    logger.info(
        "Background task start: summarize daily_range %s..%s (job=%s)",
        since,
        until,
        job_id,
    )
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = get_llm_router(config)
        ds = DailyStateStore(config.daily_state_path)
        service = SummarizerService(config, llm, daily_state=ds)
        results = service.daily_range(
            since, until, force=force, max_workers=max_workers, batch=batch
        )

        succeeded = sum(1 for r in results if r["status"] == "success")
        result_msg = f"{succeeded}/{len(results)} succeeded"
        if succeeded < len(results):
            store.update(job_id, JobStatus.FAILED, error=result_msg)
        else:
            store.update(job_id, JobStatus.COMPLETED, result=result_msg)
    except Exception as e:
        logger.warning(
            "Background task failed: summarize daily_range %s..%s: %s",
            since,
            until,
            e,
        )
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _summarize_weekly_task(
    job_id: str,
    year: int,
    week: int,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
) -> None:
    """BackgroundTask: weekly summary 생성."""
    logger.info("Background task start: summarize weekly %d-W%d (job=%s)", year, week, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.weekly(year, week, force=force)
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        logger.warning("Background task failed: summarize weekly %d-W%d: %s", year, week, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _summarize_monthly_task(
    job_id: str,
    year: int,
    month: int,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
) -> None:
    """BackgroundTask: monthly summary 생성."""
    logger.info("Background task start: summarize monthly %d-%d (job=%s)", year, month, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.monthly(year, month, force=force)
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        logger.warning("Background task failed: summarize monthly %d-%d: %s", year, month, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _summarize_yearly_task(
    job_id: str,
    year: int,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
) -> None:
    """BackgroundTask: yearly summary 생성."""
    logger.info("Background task start: summarize yearly %d (job=%s)", year, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = get_llm_router(config)
        service = SummarizerService(config, llm)
        path = service.yearly(year, force=force)
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        logger.warning("Background task failed: summarize yearly %d: %s", year, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


@router.post("/daily/range", status_code=202)
def summarize_daily_range(
    body: SummarizeDailyRangeRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 daily summary async 생성."""
    job = store.create()
    max_workers = body.max_workers if body.max_workers else config.max_workers
    bg.add_task(
        _summarize_daily_range_task,
        job.job_id,
        body.since,
        body.until,
        config,
        store,
        force=body.force,
        max_workers=max_workers,
        batch=body.batch,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/daily/{date}", status_code=202)
def summarize_daily_single(
    date: str,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 daily summary async 생성."""
    job = store.create()
    bg.add_task(
        _summarize_daily_single_task,
        job.job_id,
        date,
        config,
        store,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/weekly", status_code=202)
def summarize_weekly(
    body: SummarizeWeeklyRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """Weekly summary async 생성."""
    job = store.create()
    bg.add_task(
        _summarize_weekly_task,
        job.job_id,
        body.year,
        body.week,
        config,
        store,
        force=body.force,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/monthly", status_code=202)
def summarize_monthly(
    body: SummarizeMonthlyRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """Monthly summary async 생성."""
    job = store.create()
    bg.add_task(
        _summarize_monthly_task,
        job.job_id,
        body.year,
        body.month,
        config,
        store,
        force=body.force,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/yearly", status_code=202)
def summarize_yearly(
    body: SummarizeYearlyRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """Yearly summary async 생성."""
    job = store.create()
    bg.add_task(
        _summarize_yearly_task,
        job.job_id,
        body.year,
        config,
        store,
        force=body.force,
    )
    return {"job_id": job.job_id, "status": job.status.value}
