"""Pipeline 엔드포인트 — run, run/range, job status."""

import calendar
import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.exceptions import SummarizeError
from git_recap.infra.client_pool import GHESClientPool
from git_recap.infra.ghes_client import GHESClient
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.daily_state import DailyStateStore
from git_recap.services.fetch_progress import FetchProgressStore
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)

router = APIRouter()


class RunSingleRequest(BaseModel):
    force: bool = False
    types: list[str] | None = None
    enrich: bool = True


class RunRangeRequest(BaseModel):
    since: str
    until: str
    force: bool = False
    types: list[str] | None = None
    max_workers: int | None = None
    enrich: bool = True
    summarize_weekly: str | None = None
    summarize_monthly: str | None = None
    summarize_yearly: int | None = None


def _weeks_in_month(year: int, month: int) -> list[tuple[int, int]]:
    """해당 월에 걸치는 모든 ISO (year, week) 튜플을 순서대로 반환."""
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        iso_y, iso_w, _ = date(year, month, day).isocalendar()
        key = (iso_y, iso_w)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _run_hierarchical(
    summarizer: SummarizerService,
    force: bool,
    summarize_weekly: str | None,
    summarize_monthly: str | None,
    summarize_yearly: int | None,
) -> str | None:
    """Run hierarchical summarization after daily pipeline."""
    if summarize_weekly:
        parts = summarize_weekly.split("-")
        yr, wk = int(parts[0]), int(parts[1])
        path = summarizer.weekly(yr, wk, force=force)
        return str(path)
    elif summarize_monthly:
        parts = summarize_monthly.split("-")
        yr, mo = int(parts[0]), int(parts[1])
        for wy, ww in _weeks_in_month(yr, mo):
            try:
                summarizer.weekly(wy, ww, force=force)
            except SummarizeError:
                pass
        path = summarizer.monthly(yr, mo, force=force)
        return str(path)
    elif summarize_yearly is not None:
        yr = summarize_yearly
        for mo in range(1, 13):
            for wy, ww in _weeks_in_month(yr, mo):
                try:
                    summarizer.weekly(wy, ww, force=force)
                except SummarizeError:
                    pass
            try:
                summarizer.monthly(yr, mo, force=force)
            except SummarizeError:
                pass
        path = summarizer.yearly(yr, force=force)
        return str(path)
    return None


def _run_pipeline_task(
    job_id: str,
    target_date: str,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
    types: set[str] | None = None,
    enrich: bool = True,
) -> None:
    """BackgroundTask: 단일 날짜 파이프라인 실행."""
    logger.info(
        "Background task start: run_pipeline %s (job=%s, force=%s, types=%s, enrich=%s)",
        target_date,
        job_id,
        force,
        types,
        enrich,
    )
    store.update(job_id, JobStatus.RUNNING)

    ghes = None
    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token, search_interval=2.0)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        ds = DailyStateStore(config.daily_state_path)
        ps = FetchProgressStore(config.state_dir / "fetch_progress")
        fetcher = FetcherService(config, ghes, daily_state=ds, progress_store=ps)
        normalizer = NormalizerService(config, daily_state=ds, llm=llm if enrich else None)
        summarizer = SummarizerService(config, llm, daily_state=ds)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer, config=config)

        path = orchestrator.run_daily(target_date, types=types)
        logger.info("Background task complete: run_pipeline %s → %s", target_date, path)
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        logger.warning("Background task failed: run_pipeline %s: %s", target_date, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))
    finally:
        if ghes:
            ghes.close()


def _run_range_task(
    job_id: str,
    since: str,
    until: str,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
    types: set[str] | None = None,
    max_workers: int = 1,
    enrich: bool = True,
    summarize_weekly: str | None = None,
    summarize_monthly: str | None = None,
    summarize_yearly: int | None = None,
) -> None:
    """BackgroundTask: 기간 범위 파이프라인 실행."""
    logger.info(
        "Background task start: run_range %s..%s (job=%s, force=%s, workers=%d)",
        since,
        until,
        job_id,
        force,
        max_workers,
    )
    store.update(job_id, JobStatus.RUNNING)

    pool = None
    ghes = None
    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token, search_interval=2.0)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        ds = DailyStateStore(config.daily_state_path)
        ps = FetchProgressStore(config.state_dir / "fetch_progress")
        fetch_kwargs: dict = {"daily_state": ds, "progress_store": ps}
        if max_workers > 1:
            pool = GHESClientPool(config.ghes_url, config.ghes_token, size=max_workers)
            fetch_kwargs["max_workers"] = max_workers
            fetch_kwargs["client_pool"] = pool
        fetcher = FetcherService(config, ghes, **fetch_kwargs)
        normalizer = NormalizerService(config, daily_state=ds, llm=llm if enrich else None)
        summarizer = SummarizerService(config, llm, daily_state=ds)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer, config=config)

        results = orchestrator.run_range(
            since,
            until,
            force=force,
            types=types,
            max_workers=max_workers,
        )

        succeeded = sum(1 for r in results if r["status"] == "success")
        failed = sum(1 for r in results if r["status"] == "failed")
        result_msg = f"{succeeded}/{len(results)} succeeded"

        # Hierarchical summarization after successful daily pipeline
        hier_msg = None
        if failed == 0:
            hier_msg = _run_hierarchical(
                summarizer,
                force,
                summarize_weekly,
                summarize_monthly,
                summarize_yearly,
            )

        if hier_msg:
            result_msg += f"; hierarchical: {hier_msg}"

        if failed > 0:
            store.update(job_id, JobStatus.FAILED, error=result_msg)
        else:
            store.update(job_id, JobStatus.COMPLETED, result=result_msg)
    except Exception as e:
        store.update(job_id, JobStatus.FAILED, error=str(e))
    finally:
        if ghes:
            ghes.close()
        if pool:
            pool.close()


@router.post("/run/range", status_code=202)
def run_pipeline_range(
    body: RunRangeRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 파이프라인 async 실행."""
    job = store.create()
    types_set = set(body.types) if body.types else None
    max_workers = body.max_workers if body.max_workers else config.max_workers
    bg.add_task(
        _run_range_task,
        job.job_id,
        body.since,
        body.until,
        config,
        store,
        force=body.force,
        types=types_set,
        max_workers=max_workers,
        enrich=body.enrich,
        summarize_weekly=body.summarize_weekly,
        summarize_monthly=body.summarize_monthly,
        summarize_yearly=body.summarize_yearly,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/run/{date}", status_code=202)
def run_pipeline(
    date: str,
    bg: BackgroundTasks,
    body: RunSingleRequest | None = Body(default=None),
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 파이프라인 async 실행."""
    if body is None:
        body = RunSingleRequest()
    job = store.create()
    types_set = set(body.types) if body.types else None
    bg.add_task(
        _run_pipeline_task,
        job.job_id,
        date,
        config,
        store,
        force=body.force,
        types=types_set,
        enrich=body.enrich,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.get("/jobs/{job_id}")
def get_job_status(
    job_id: str,
    store: JobStore = Depends(get_job_store),
):
    """Job 상태 조회."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "result": job.result,
        "error": job.error,
    }
