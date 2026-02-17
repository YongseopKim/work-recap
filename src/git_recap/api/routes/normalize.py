"""Normalize 엔드포인트 — individual normalize operations."""

import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.daily_state import DailyStateStore
from git_recap.services.normalizer import NormalizerService

logger = logging.getLogger(__name__)

router = APIRouter()


class NormalizeSingleRequest(BaseModel):
    enrich: bool = True
    force: bool = False


class NormalizeRangeRequest(BaseModel):
    since: str
    until: str
    force: bool = False
    enrich: bool = True
    max_workers: int | None = None


def _normalize_single_task(
    job_id: str,
    target_date: str,
    config: AppConfig,
    store: JobStore,
    enrich: bool = True,
    force: bool = False,
) -> None:
    """BackgroundTask: 단일 날짜 normalize."""
    logger.info("Background task start: normalize %s (job=%s)", target_date, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        ds = DailyStateStore(config.daily_state_path)
        llm = (
            LLMClient(config.llm_provider, config.llm_api_key, config.llm_model) if enrich else None
        )
        service = NormalizerService(config, daily_state=ds, llm=llm)
        act_path, stats_path = service.normalize(target_date)
        store.update(
            job_id,
            JobStatus.COMPLETED,
            result=f"{act_path}, {stats_path}",
        )
    except Exception as e:
        logger.warning("Background task failed: normalize %s: %s", target_date, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _normalize_range_task(
    job_id: str,
    since: str,
    until: str,
    config: AppConfig,
    store: JobStore,
    force: bool = False,
    enrich: bool = True,
    max_workers: int = 1,
) -> None:
    """BackgroundTask: 기간 범위 normalize."""
    logger.info("Background task start: normalize_range %s..%s (job=%s)", since, until, job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        ds = DailyStateStore(config.daily_state_path)
        llm = (
            LLMClient(config.llm_provider, config.llm_api_key, config.llm_model) if enrich else None
        )
        service = NormalizerService(config, daily_state=ds, llm=llm)
        results = service.normalize_range(since, until, force=force, max_workers=max_workers)

        succeeded = sum(1 for r in results if r["status"] == "success")
        result_msg = f"{succeeded}/{len(results)} succeeded"
        if succeeded < len(results):
            store.update(job_id, JobStatus.FAILED, error=result_msg)
        else:
            store.update(job_id, JobStatus.COMPLETED, result=result_msg)
    except Exception as e:
        logger.warning("Background task failed: normalize_range %s..%s: %s", since, until, e)
        store.update(job_id, JobStatus.FAILED, error=str(e))


@router.post("/range", status_code=202)
def normalize_range(
    body: NormalizeRangeRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 normalize async 실행."""
    job = store.create()
    max_workers = body.max_workers if body.max_workers else config.max_workers
    bg.add_task(
        _normalize_range_task,
        job.job_id,
        body.since,
        body.until,
        config,
        store,
        force=body.force,
        enrich=body.enrich,
        max_workers=max_workers,
    )
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/{date}", status_code=202)
def normalize_single(
    date: str,
    bg: BackgroundTasks,
    body: NormalizeSingleRequest | None = Body(default=None),
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 normalize async 실행."""
    if body is None:
        body = NormalizeSingleRequest()
    job = store.create()
    bg.add_task(
        _normalize_single_task,
        job.job_id,
        date,
        config,
        store,
        enrich=body.enrich,
        force=body.force,
    )
    return {"job_id": job.job_id, "status": job.status.value}
