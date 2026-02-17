"""Fetch 엔드포인트 — individual fetch operations."""

import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.infra.client_pool import GHESClientPool
from git_recap.infra.ghes_client import GHESClient
from git_recap.models import JobStatus
from git_recap.services.daily_state import DailyStateStore
from git_recap.services.fetch_progress import FetchProgressStore
from git_recap.services.fetcher import FetcherService

logger = logging.getLogger(__name__)

router = APIRouter()


class FetchSingleRequest(BaseModel):
    types: list[str] | None = None
    force: bool = False


class FetchRangeRequest(BaseModel):
    since: str
    until: str
    types: list[str] | None = None
    force: bool = False
    max_workers: int | None = None


def _fetch_single_task(
    job_id: str,
    target_date: str,
    config: AppConfig,
    store: JobStore,
    types: set[str] | None = None,
    force: bool = False,
) -> None:
    """BackgroundTask: 단일 날짜 fetch."""
    logger.info("Background task start: fetch %s (job=%s)", target_date, job_id)
    store.update(job_id, JobStatus.RUNNING)

    ghes = None
    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token, search_interval=2.0)
        ds = DailyStateStore(config.daily_state_path)
        ps = FetchProgressStore(config.state_dir / "fetch_progress")
        service = FetcherService(config, ghes, daily_state=ds, progress_store=ps)
        result = service.fetch(target_date, types=types)
        paths = {k: str(v) for k, v in result.items()}
        store.update(job_id, JobStatus.COMPLETED, result=str(paths))
    except Exception as e:
        logger.warning("Background task failed: fetch %s: %s", target_date, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))
    finally:
        if ghes:
            ghes.close()


def _fetch_range_task(
    job_id: str,
    since: str,
    until: str,
    config: AppConfig,
    store: JobStore,
    types: set[str] | None = None,
    force: bool = False,
    max_workers: int = 1,
) -> None:
    """BackgroundTask: 기간 범위 fetch."""
    logger.info("Background task start: fetch_range %s..%s (job=%s)", since, until, job_id)
    store.update(job_id, JobStatus.RUNNING)

    pool = None
    ghes = None
    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token, search_interval=2.0)
        ds = DailyStateStore(config.daily_state_path)
        ps = FetchProgressStore(config.state_dir / "fetch_progress")
        fetch_kwargs: dict = {"daily_state": ds, "progress_store": ps}
        if max_workers > 1:
            pool = GHESClientPool(config.ghes_url, config.ghes_token, size=max_workers)
            fetch_kwargs["max_workers"] = max_workers
            fetch_kwargs["client_pool"] = pool
        service = FetcherService(config, ghes, **fetch_kwargs)
        results = service.fetch_range(since, until, types=types, force=force)

        succeeded = sum(1 for r in results if r["status"] == "success")
        result_msg = f"{succeeded}/{len(results)} succeeded"
        if succeeded < len(results):
            store.update(job_id, JobStatus.FAILED, error=result_msg)
        else:
            store.update(job_id, JobStatus.COMPLETED, result=result_msg)
    except Exception as e:
        logger.warning("Background task failed: fetch_range %s..%s: %s", since, until, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))
    finally:
        if ghes:
            ghes.close()
        if pool:
            pool.close()


@router.post("/range", status_code=202)
def fetch_range(
    body: FetchRangeRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 fetch async 실행."""
    job = store.create()
    types_set = set(body.types) if body.types else None
    max_workers = body.max_workers if body.max_workers else 1
    bg.add_task(
        _fetch_range_task,
        job.job_id,
        body.since,
        body.until,
        config,
        store,
        types=types_set,
        force=body.force,
        max_workers=max_workers,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/{date}", status_code=202)
def fetch_single(
    date: str,
    bg: BackgroundTasks,
    body: FetchSingleRequest | None = Body(default=None),
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 fetch async 실행."""
    if body is None:
        body = FetchSingleRequest()
    job = store.create()
    types_set = set(body.types) if body.types else None
    bg.add_task(
        _fetch_single_task,
        job.job_id,
        date,
        config,
        store,
        types=types_set,
        force=body.force,
    )
    return {"job_id": job.job_id, "status": job.status.value}
