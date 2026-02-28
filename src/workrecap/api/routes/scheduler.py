"""Scheduler 엔드포인트 — status, history, trigger, pause, resume."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.jobs import (
    run_daily_job,
    run_monthly_job,
    run_weekly_job,
    run_yearly_job,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_FUNCS = {
    "daily": run_daily_job,
    "weekly": run_weekly_job,
    "monthly": run_monthly_job,
    "yearly": run_yearly_job,
}


def _get_scheduler(request: Request):
    return request.app.state.scheduler


def _get_history(request: Request) -> SchedulerHistory:
    return request.app.state.scheduler_history


@router.get("/status")
def get_status(request: Request):
    scheduler = _get_scheduler(request)
    return scheduler.status()


@router.get("/history")
def get_history(
    request: Request,
    job: str | None = Query(default=None),
    limit: int | None = Query(default=None),
):
    history = _get_history(request)
    return history.list(job=job, limit=limit)


@router.post("/trigger/{job_name}", status_code=202)
async def trigger_job(job_name: str, request: Request):
    if job_name not in _JOB_FUNCS:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_name}")
    scheduler = _get_scheduler(request)

    job_func = _JOB_FUNCS[job_name]
    asyncio.create_task(job_func(scheduler._config, scheduler._history, scheduler._notifier))
    return {"triggered": job_name}


@router.put("/pause")
def pause_scheduler(request: Request):
    scheduler = _get_scheduler(request)
    scheduler.pause()
    return {"state": "paused"}


@router.put("/resume")
def resume_scheduler(request: Request):
    scheduler = _get_scheduler(request)
    scheduler.resume()
    return {"state": "running"}
