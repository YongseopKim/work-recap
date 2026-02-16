# Phase 6: BE API 상세 설계

## 목적

FastAPI 기반 REST API로 Service Layer를 웹에서 호출할 수 있게 한다.
LLM 호출이 포함된 작업(pipeline, query)은 async job으로 처리하여 timeout을 방지한다.
Summary 조회는 sync로 이미 생성된 .md 파일을 반환한다.

---

## 위치

```
src/git_recap/api/
├── __init__.py
├── app.py              # FastAPI app factory + exception handler + CORS
├── deps.py             # Depends() — config, 서비스 팩토리
├── job_store.py        # Job 파일 CRUD (data/state/jobs/)
└── routes/
    ├── __init__.py
    ├── pipeline.py     # POST /run/{date}, POST /run/range, GET /jobs/{id}
    ├── summary.py      # GET /summary/daily/{date}, weekly, monthly, yearly
    └── query.py        # POST /query
```

## 의존성

- `fastapi`, `uvicorn`
- `git_recap.config.AppConfig`
- `git_recap.models.Job`, `JobStatus`, `save_json`, `load_json`
- `git_recap.services.*` (Fetcher, Normalizer, Summarizer, Orchestrator)
- `git_recap.infra.*` (GHESClient, LLMClient)
- `git_recap.exceptions.GitRecapError`

---

## 엔드포인트 명세

### Pipeline

| Method | Path | 설명 | Sync/Async |
|--------|------|------|------------|
| POST | `/api/pipeline/run/{date}` | 단일 날짜 파이프라인 실행 | Async (BackgroundTasks) |
| POST | `/api/pipeline/run/range` | 기간 범위 backfill | Async (BackgroundTasks) |
| GET | `/api/pipeline/jobs/{job_id}` | Job 상태 조회 | Sync |

### Summary

| Method | Path | 설명 | Sync/Async |
|--------|------|------|------------|
| GET | `/api/summary/daily/{date}` | Daily summary 조회 | Sync (파일 읽기) |
| GET | `/api/summary/weekly/{year}/{week}` | Weekly summary 조회 | Sync |
| GET | `/api/summary/monthly/{year}/{month}` | Monthly summary 조회 | Sync |
| GET | `/api/summary/yearly/{year}` | Yearly summary 조회 | Sync |

### Query

| Method | Path | 설명 | Sync/Async |
|--------|------|------|------------|
| POST | `/api/query` | 자유 질문 (LLM 호출) | Async (BackgroundTasks) |

---

## 상세 구현

### app.py — FastAPI 앱 팩토리

```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from git_recap.exceptions import GitRecapError
from git_recap.api.routes import pipeline, summary, query


def create_app() -> FastAPI:
    app = FastAPI(title="git-recap", version="0.1.0")

    # CORS (FE 개발용)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 라우터 등록
    app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
    app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
    app.include_router(query.router, prefix="/api", tags=["query"])

    # 예외 핸들러
    @app.exception_handler(GitRecapError)
    async def handle_git_recap_error(request: Request, exc: GitRecapError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    return app


app = create_app()
```

**설계 결정:**
- `create_app()` 팩토리 패턴 사용 → 테스트에서 app을 독립적으로 생성 가능
- 모듈 레벨 `app = create_app()` → `uvicorn git_recap.api.app:app`으로 실행 가능
- CORS `allow_origins=["*"]` → 개발 편의. 프로덕션 시 제한 가능
- GitRecapError 핸들러는 sync 엔드포인트에서 직접 에러가 발생할 때만 동작
  (async job 에러는 job status의 error 필드에 기록됨)

---

### deps.py — 의존성 주입

```python
from functools import lru_cache

from git_recap.config import AppConfig
from git_recap.api.job_store import JobStore


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()


def get_job_store() -> JobStore:
    return JobStore(get_config())
```

**설계 결정:**
- `get_config()`는 `@lru_cache`로 앱 수명 동안 1회만 로드
- 서비스(Orchestrator, Summarizer 등)는 엔드포인트 내에서 직접 생성
  (CLI와 동일 패턴 — DI 컨테이너 없이 간단한 wiring)
- `JobStore`만 `Depends()`로 주입 (여러 라우트에서 공유)

---

### job_store.py — Job 파일 CRUD

```python
import uuid
from datetime import datetime, timezone

from git_recap.config import AppConfig
from git_recap.models import Job, JobStatus, save_json, load_json


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
        )

    def update(
        self, job_id: str, status: JobStatus,
        result: str | None = None, error: str | None = None,
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
```

**파일 경로:** `data/state/jobs/{job_id}.json`

**상태 전이:**
```
ACCEPTED → RUNNING → COMPLETED
                   → FAILED
```

---

### routes/pipeline.py

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.exceptions import GitRecapError
from git_recap.infra.ghes_client import GHESClient
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.fetcher import FetcherService
from git_recap.services.normalizer import NormalizerService
from git_recap.services.orchestrator import OrchestratorService
from git_recap.services.summarizer import SummarizerService

router = APIRouter()


class RangeRequest(BaseModel):
    since: str   # YYYY-MM-DD
    until: str   # YYYY-MM-DD


def _run_pipeline_task(job_id: str, target_date: str) -> None:
    """BackgroundTask: 단일 날짜 파이프라인 실행."""
    config = get_config()
    store = JobStore(config)
    store.update(job_id, JobStatus.RUNNING)

    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        fetcher = FetcherService(config, ghes)
        normalizer = NormalizerService(config)
        summarizer = SummarizerService(config, llm)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer)

        path = orchestrator.run_daily(target_date)
        ghes.close()
        store.update(job_id, JobStatus.COMPLETED, result=str(path))
    except Exception as e:
        store.update(job_id, JobStatus.FAILED, error=str(e))


def _run_range_task(job_id: str, since: str, until: str) -> None:
    """BackgroundTask: 기간 범위 파이프라인 실행."""
    config = get_config()
    store = JobStore(config)
    store.update(job_id, JobStatus.RUNNING)

    try:
        ghes = GHESClient(config.ghes_url, config.ghes_token)
        llm = LLMClient(config.llm_provider, config.llm_api_key, config.llm_model)
        fetcher = FetcherService(config, ghes)
        normalizer = NormalizerService(config)
        summarizer = SummarizerService(config, llm)
        orchestrator = OrchestratorService(fetcher, normalizer, summarizer)

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


@router.post("/run/{date}", status_code=202)
def run_pipeline(
    date: str,
    bg: BackgroundTasks,
    store: JobStore = Depends(get_job_store),
):
    """단일 날짜 파이프라인 async 실행."""
    job = store.create()
    bg.add_task(_run_pipeline_task, job.job_id, date)
    return {"job_id": job.job_id, "status": job.status.value}


@router.post("/run/range", status_code=202)
def run_pipeline_range(
    body: RangeRequest,
    bg: BackgroundTasks,
    store: JobStore = Depends(get_job_store),
):
    """기간 범위 파이프라인 async 실행."""
    job = store.create()
    bg.add_task(_run_range_task, job.job_id, body.since, body.until)
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
```

---

### routes/summary.py

```python
from fastapi import APIRouter, Depends, HTTPException

from git_recap.api.deps import get_config
from git_recap.config import AppConfig

router = APIRouter()


def _read_summary(path) -> dict:
    """Summary 파일 읽어서 반환. 없으면 404."""
    if not path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    content = path.read_text(encoding="utf-8")
    return {"content": content, "path": str(path)}


@router.get("/daily/{date}")
def get_daily_summary(date: str):
    config = get_config()
    return _read_summary(config.daily_summary_path(date))


@router.get("/weekly/{year}/{week}")
def get_weekly_summary(year: int, week: int):
    config = get_config()
    return _read_summary(config.weekly_summary_path(year, week))


@router.get("/monthly/{year}/{month}")
def get_monthly_summary(year: int, month: int):
    config = get_config()
    return _read_summary(config.monthly_summary_path(year, month))


@router.get("/yearly/{year}")
def get_yearly_summary(year: int):
    config = get_config()
    return _read_summary(config.yearly_summary_path(year))
```

---

### routes/query.py

```python
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.infra.llm_client import LLMClient
from git_recap.models import JobStatus
from git_recap.services.summarizer import SummarizerService

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    months: int = 3


def _run_query_task(job_id: str, question: str, months: int) -> None:
    """BackgroundTask: 자유 질문 실행."""
    config = get_config()
    store = JobStore(config)
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
    store: JobStore = Depends(get_job_store),
):
    """자유 질문 async 실행."""
    job = store.create()
    bg.add_task(_run_query_task, job.job_id, body.question, body.months)
    return {"job_id": job.job_id, "status": job.status.value}
```

---

## 에러 처리 전략

| 상황 | 처리 |
|------|------|
| Sync 엔드포인트 (summary GET) 파일 없음 | HTTP 404 |
| Sync 엔드포인트 예상치 못한 에러 | GitRecapError → HTTP 500 (exception handler) |
| Async job 실행 중 에러 | Job status = FAILED, error 필드에 메시지 |
| Job ID 없음 | HTTP 404 |
| 잘못된 요청 body | FastAPI 자동 422 (Pydantic validation) |

**Async job 에러는 HTTP 에러 코드로 매핑하지 않는다.**
job 생성(POST) 자체는 항상 202를 반환하고, 실행 결과는 polling으로 확인한다.

---

## 응답 포맷

### Job 생성 응답 (202)
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "accepted"
}
```

### Job 상태 조회 응답
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "created_at": "2025-02-16T10:00:00+00:00",
  "updated_at": "2025-02-16T10:00:30+00:00",
  "result": "/data/summaries/2025/daily/02-16.md",
  "error": null
}
```

### Summary 조회 응답
```json
{
  "content": "# 2025-02-16 Daily Summary\n\n...",
  "path": "data/summaries/2025/daily/02-16.md"
}
```

### 에러 응답
```json
{
  "error": "Pipeline failed at 'fetch': GHES timeout"
}
```

---

## 테스트 전략

`httpx.AsyncClient` + `ASGITransport` 대신 **Starlette `TestClient`** 사용.
`TestClient`는 `BackgroundTasks`를 동기적으로 실행하므로
POST → 즉시 job 상태 확인이 가능하다.

서비스/인프라는 monkeypatch로 mock한다.
`deps.py`의 `get_config()`를 override하여 `tmp_path` 기반 config 주입.

---

## 테스트 명세

### test_api.py

```python
"""tests/unit/test_api.py"""
from starlette.testclient import TestClient

class TestApp:
    def test_cors_headers(self, client):
        """OPTIONS 요청 시 CORS 헤더 포함."""

    def test_exception_handler(self, client):
        """GitRecapError 발생 시 500 + JSON 에러."""

class TestJobStore:
    def test_create_job(self, store):
        """Job 생성 → status=ACCEPTED, job_id 존재."""

    def test_get_job(self, store):
        """생성된 Job 조회."""

    def test_get_nonexistent_job(self, store):
        """없는 Job → None."""

    def test_update_job_status(self, store):
        """상태 업데이트 (RUNNING → COMPLETED)."""

    def test_update_job_with_error(self, store):
        """상태 FAILED + error 메시지."""

class TestPipelineRun:
    def test_run_single_date(self, client):
        """POST /api/pipeline/run/{date} → 202 + job_id."""

    def test_run_completes_job(self, client):
        """POST → job status가 completed로 전이."""

    def test_run_failure_marks_job_failed(self, client):
        """파이프라인 실패 시 job status = failed."""

class TestPipelineRunRange:
    def test_run_range(self, client):
        """POST /api/pipeline/run/range → 202 + job_id."""

    def test_run_range_partial_failure(self, client):
        """부분 실패 시 job status = failed."""

class TestJobStatus:
    def test_get_job_status(self, client):
        """GET /api/pipeline/jobs/{id} → job 정보."""

    def test_job_not_found(self, client):
        """없는 job_id → 404."""

class TestSummary:
    def test_daily_summary(self, client, tmp_summary):
        """GET /api/summary/daily/{date} → markdown 내용."""

    def test_daily_summary_not_found(self, client):
        """파일 없으면 404."""

    def test_weekly_summary(self, client, tmp_summary):
        """GET /api/summary/weekly/{year}/{week}."""

    def test_monthly_summary(self, client, tmp_summary):
        """GET /api/summary/monthly/{year}/{month}."""

    def test_yearly_summary(self, client, tmp_summary):
        """GET /api/summary/yearly/{year}."""

class TestQuery:
    def test_query(self, client):
        """POST /api/query → 202 + job_id."""

    def test_query_completes(self, client):
        """POST → job에 LLM 응답 저장."""

    def test_query_failure(self, client):
        """SummarizeError → job status = failed."""
```

---

## ToDo

| # | 작업 | 테스트 |
|---|------|--------|
| 6.1 | `job_store.py` — Job 파일 CRUD | TestJobStore (5 tests) |
| 6.2 | `app.py` + `deps.py` — FastAPI 앱, CORS, exception handler | TestApp (2 tests) |
| 6.3 | `routes/pipeline.py` — run + run/range + jobs 엔드포인트 | TestPipelineRun, TestPipelineRunRange, TestJobStatus (6 tests) |
| 6.4 | `routes/summary.py` — summary 조회 엔드포인트 | TestSummary (5 tests) |
| 6.5 | `routes/query.py` — 자유 질문 엔드포인트 | TestQuery (3 tests) |
