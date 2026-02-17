"""Pipeline 엔드포인트 — run, run/range, job status."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.infra.ghes_client import GHESClient
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService

router = APIRouter()


class RangeRequest(BaseModel):
    since: str
    until: str


def _run_pipeline_task(
    job_id: str,
    target_date: str,
    config: AppConfig,
    store: JobStore,
) -> None:
    """BackgroundTask: 단일 날짜 파이프라인 실행."""
    store.update(job_id, JobStatus.RUNNING)

    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        fetcher = FetcherService(config, ghes)
        normalizer = NormalizerService(config)
        summarizer = SummarizerService(config, llm)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer, config=config)

        path = orchestrator.run_daily(target_date)
        ghes.close()
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _run_range_task(
    job_id: str,
    since: str,
    until: str,
    config: AppConfig,
    store: JobStore,
) -> None:
    """BackgroundTask: 기간 범위 파이프라인 실행."""
    store.update(job_id, JobStatus.RUNNING)

    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        fetcher = FetcherService(config, ghes)
        normalizer = NormalizerService(config)
        summarizer = SummarizerService(config, llm)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer, config=config)

        results = orchestrator.run_range(since, until)
        ghes.close()

        succeeded = sum(1 for r in results if r["status"] == "success")
        result_msg = f"{succeeded}/{len(results)} succeeded"
        if succeeded < len(results):
            store.update(job_id, JobStatus.FAILED, error=result_msg)
        else:
            store.update(job_id, JobStatus.COMPLETED, result=result_msg)
    except Exception as e:
        store.update(job_id, JobStatus.FAILED, error=str(e))


@router.post("/run/range", status_code=202)
def run_pipeline_range(
    body: RangeRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 파이프라인 async 실행."""
    job = store.create()
    bg.add_task(_run_range_task, job.job_id, body.since, body.until, config, store)
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/run/{date}", status_code=202)
def run_pipeline(
    date: str,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 파이프라인 async 실행."""
    job = store.create()
    bg.add_task(_run_pipeline_task, job.job_id, date, config, store)
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
