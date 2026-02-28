"""Job 파일 CRUD — data/state/jobs/{job_id}.json 관리."""

import uuid
from datetime import datetime, timezone

from workrecap.config import AppConfig
from workrecap.models import Job, JobStatus, load_json, save_json


class JobStore:
    def __init__(self, config: AppConfig) -> None:
        self._jobs_dir = config.jobs_dir

    def _job_path(self, job_id: str):
        return self._jobs_dir / f"{job_id}.json"

    def create(self) -> Job:
        """새 Job 생성 (status=ACCEPTED)."""
        now = datetime.now(timezone.utc).isoformat()
        job = Job(
            job_id=uuid.uuid4().hex[:12],
            status=JobStatus.ACCEPTED,
            created_at=now,
            updated_at=now,
        )
        save_json(job, self._job_path(job.job_id))
        return job

    def get(self, job_id: str) -> Job | None:
        """Job 조회. 없으면 None."""
        path = self._job_path(job_id)
        if not path.exists():
            return None
        data = load_json(path)
        return Job(
            job_id=data["job_id"],
            status=JobStatus(data["status"]),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            result=data.get("result"),
            error=data.get("error"),
            progress=data.get("progress"),
        )

    def update(
        self,
        job_id: str,
        status: JobStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> Job:
        """Job 상태 업데이트."""
        job = self.get(job_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        job.status = status
        job.updated_at = datetime.now(timezone.utc).isoformat()
        job.result = result
        job.error = error
        save_json(job, self._job_path(job_id))
        return job

    def update_progress(self, job_id: str, progress: str) -> Job:
        """Job progress만 업데이트 (status 유지)."""
        job = self.get(job_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        job.progress = progress
        job.updated_at = datetime.now(timezone.utc).isoformat()
        save_json(job, self._job_path(job_id))
        return job
