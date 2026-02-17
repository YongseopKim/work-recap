"""자유 질문 엔드포인트 — async job."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.summarizer import SummarizerService

logger = logging.getLogger(__name__)

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    months: int = 3


def _run_query_task(
    job_id: str,
    question: str,
    months: int,
    config: AppConfig,
    store: JobStore,
) -> None:
    """BackgroundTask: 자유 질문 실행."""
    logger.info("Background task start: query (job=%s)", job_id)
    store.update(job_id, JobStatus.RUNNING)

    try:
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        service = SummarizerService(config, llm)
        answer = service.query(question, months_back=months)
        store.update(job_id, JobStatus.COMPLETED, result=answer)
    except Exception as e:
        store.update(job_id, JobStatus.FAILED, error=str(e))


@router.post("/query", status_code=202)
def ask_query(
    body: QueryRequest,
    bg: BackgroundTasks,
    config: AppConfig = Depends(get_config),
    store: JobStore = Depends(get_job_store),
):
    """자유 질문 async 실행."""
    job = store.create()
    bg.add_task(_run_query_task, job.job_id, body.question, body.months, config, store)
    return {"job_id": job.job_id, "status": job.status.value}
