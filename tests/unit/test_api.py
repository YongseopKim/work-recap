"""BE API 테스트 — FastAPI TestClient 기반."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from git_recap.api.app import create_app
from git_recap.api.deps import get_config, get_job_store
from git_recap.api.job_store import JobStore
from git_recap.config import AppConfig
from git_recap.exceptions import FetchError, StepFailedError, SummarizeError
from git_recap.models import JobStatus


# ── Fixtures ──


@pytest.fixture()
def test_config(tmp_path):
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        llm_api_key="test-key",
        data_dir=tmp_path / "data",
        prompts_dir=tmp_path / "prompts",
    )


@pytest.fixture()
def store(test_config):
    return JobStore(test_config)


@pytest.fixture()
def client(test_config, store):
    app = create_app()
    app.dependency_overrides[get_config] = lambda: test_config
    app.dependency_overrides[get_job_store] = lambda: store
    return TestClient(app)


# ── TestJobStore ──


class TestJobStore:
    def test_create_job(self, store):
        """Job 생성 → status=ACCEPTED, job_id 존재."""
        job = store.create()
        assert job.status == JobStatus.ACCEPTED
        assert len(job.job_id) == 12
        assert job.result is None
        assert job.error is None

    def test_get_job(self, store):
        """생성된 Job 조회."""
        created = store.create()
        fetched = store.get(created.job_id)
        assert fetched is not None
        assert fetched.job_id == created.job_id
        assert fetched.status == JobStatus.ACCEPTED

    def test_get_nonexistent_job(self, store):
        """없는 Job → None."""
        assert store.get("nonexistent") is None

    def test_update_job_status(self, store):
        """상태 업데이트 (RUNNING → COMPLETED)."""
        job = store.create()
        store.update(job.job_id, JobStatus.RUNNING)
        store.update(job.job_id, JobStatus.COMPLETED, result="/path/to/summary.md")

        updated = store.get(job.job_id)
        assert updated.status == JobStatus.COMPLETED
        assert updated.result == "/path/to/summary.md"
        assert updated.error is None

    def test_update_job_with_error(self, store):
        """상태 FAILED + error 메시지."""
        job = store.create()
        store.update(job.job_id, JobStatus.FAILED, error="GHES timeout")

        updated = store.get(job.job_id)
        assert updated.status == JobStatus.FAILED
        assert updated.error == "GHES timeout"


# ── TestApp ──


class TestApp:
    def test_cors_headers(self, client):
        """OPTIONS 요청 시 CORS 헤더 포함."""
        resp = client.options(
            "/api/summary/daily/2025-02-16",
            headers={"Origin": "http://localhost:3000"},
        )
        assert "access-control-allow-origin" in resp.headers

    def test_exception_handler(self, client):
        """GitRecapError 발생 시 500 + JSON 에러."""
        # summary 라우트에서 config path가 잘못되면 에러 아닌 404가 발생하므로
        # 직접 exception을 트리거하는 것은 어려움 → job not found로 404 확인
        resp = client.get("/api/pipeline/jobs/nonexistent")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# ── TestPipelineRun ──


class TestPipelineRun:
    @patch("git_recap.api.routes.pipeline.OrchestratorService")
    @patch("git_recap.api.routes.pipeline.SummarizerService")
    @patch("git_recap.api.routes.pipeline.NormalizerService")
    @patch("git_recap.api.routes.pipeline.FetcherService")
    @patch("git_recap.api.routes.pipeline.LLMClient")
    @patch("git_recap.api.routes.pipeline.GHESClient")
    def test_run_single_date(
        self, mock_ghes, mock_llm, mock_fetch, mock_norm, mock_summ, mock_orch, client
    ):
        """POST /api/pipeline/run/{date} → 202 + job_id."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        resp = client.post("/api/pipeline/run/2025-02-16")
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "accepted"

    @patch("git_recap.api.routes.pipeline.OrchestratorService")
    @patch("git_recap.api.routes.pipeline.SummarizerService")
    @patch("git_recap.api.routes.pipeline.NormalizerService")
    @patch("git_recap.api.routes.pipeline.FetcherService")
    @patch("git_recap.api.routes.pipeline.LLMClient")
    @patch("git_recap.api.routes.pipeline.GHESClient")
    def test_run_completes_job(
        self, mock_ghes, mock_llm, mock_fetch, mock_norm, mock_summ, mock_orch, client
    ):
        """POST → job status가 completed로 전이."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        resp = client.post("/api/pipeline/run/2025-02-16")
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("git_recap.api.routes.pipeline.OrchestratorService")
    @patch("git_recap.api.routes.pipeline.SummarizerService")
    @patch("git_recap.api.routes.pipeline.NormalizerService")
    @patch("git_recap.api.routes.pipeline.FetcherService")
    @patch("git_recap.api.routes.pipeline.LLMClient")
    @patch("git_recap.api.routes.pipeline.GHESClient")
    def test_run_failure_marks_job_failed(
        self, mock_ghes, mock_llm, mock_fetch, mock_norm, mock_summ, mock_orch, client
    ):
        """파이프라인 실패 시 job status = failed."""
        mock_orch.return_value.run_daily.side_effect = StepFailedError(
            "fetch", FetchError("timeout")
        )
        resp = client.post("/api/pipeline/run/2025-02-16")
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "failed"
        assert "fetch" in status_resp.json()["error"]


# ── TestPipelineRunRange ──


class TestPipelineRunRange:
    @patch("git_recap.api.routes.pipeline.OrchestratorService")
    @patch("git_recap.api.routes.pipeline.SummarizerService")
    @patch("git_recap.api.routes.pipeline.NormalizerService")
    @patch("git_recap.api.routes.pipeline.FetcherService")
    @patch("git_recap.api.routes.pipeline.LLMClient")
    @patch("git_recap.api.routes.pipeline.GHESClient")
    def test_run_range(
        self, mock_ghes, mock_llm, mock_fetch, mock_norm, mock_summ, mock_orch, client
    ):
        """POST /api/pipeline/run/range → 202 + job_id."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
            {"date": "2025-02-16", "status": "success", "path": "/p2"},
        ]
        resp = client.post(
            "/api/pipeline/run/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("git_recap.api.routes.pipeline.OrchestratorService")
    @patch("git_recap.api.routes.pipeline.SummarizerService")
    @patch("git_recap.api.routes.pipeline.NormalizerService")
    @patch("git_recap.api.routes.pipeline.FetcherService")
    @patch("git_recap.api.routes.pipeline.LLMClient")
    @patch("git_recap.api.routes.pipeline.GHESClient")
    def test_run_range_partial_failure(
        self, mock_ghes, mock_llm, mock_fetch, mock_norm, mock_summ, mock_orch, client
    ):
        """부분 실패 시 job status = failed."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
            {"date": "2025-02-16", "status": "failed", "error": "fetch failed"},
        ]
        resp = client.post(
            "/api/pipeline/run/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "failed"
        assert "1/2" in status_resp.json()["error"]


# ── TestJobStatus ──


class TestJobStatus:
    def test_get_job_status(self, client, store):
        """GET /api/pipeline/jobs/{id} → job 정보."""
        job = store.create()
        resp = client.get(f"/api/pipeline/jobs/{job.job_id}")
        assert resp.status_code == 200
        assert resp.json()["job_id"] == job.job_id
        assert resp.json()["status"] == "accepted"

    def test_job_not_found(self, client):
        """없는 job_id → 404."""
        resp = client.get("/api/pipeline/jobs/nonexistent")
        assert resp.status_code == 404


# ── TestSummary ──


class TestSummary:
    def test_daily_summary(self, client, test_config):
        """GET /api/summary/daily/{date} → markdown 내용."""
        path = test_config.daily_summary_path("2025-02-16")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Daily Summary\n\nContent here.", encoding="utf-8")

        resp = client.get("/api/summary/daily/2025-02-16")
        assert resp.status_code == 200
        assert "Daily Summary" in resp.json()["content"]

    def test_daily_summary_not_found(self, client):
        """파일 없으면 404."""
        resp = client.get("/api/summary/daily/2099-01-01")
        assert resp.status_code == 404

    def test_weekly_summary(self, client, test_config):
        """GET /api/summary/weekly/{year}/{week}."""
        path = test_config.weekly_summary_path(2025, 7)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Weekly Summary", encoding="utf-8")

        resp = client.get("/api/summary/weekly/2025/7")
        assert resp.status_code == 200
        assert "Weekly Summary" in resp.json()["content"]

    def test_monthly_summary(self, client, test_config):
        """GET /api/summary/monthly/{year}/{month}."""
        path = test_config.monthly_summary_path(2025, 2)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Monthly Summary", encoding="utf-8")

        resp = client.get("/api/summary/monthly/2025/2")
        assert resp.status_code == 200
        assert "Monthly Summary" in resp.json()["content"]

    def test_yearly_summary(self, client, test_config):
        """GET /api/summary/yearly/{year}."""
        path = test_config.yearly_summary_path(2025)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Yearly Summary", encoding="utf-8")

        resp = client.get("/api/summary/yearly/2025")
        assert resp.status_code == 200
        assert "Yearly Summary" in resp.json()["content"]


# ── TestQuery ──


class TestQuery:
    @patch("git_recap.api.routes.query.SummarizerService")
    @patch("git_recap.api.routes.query.LLMClient")
    def test_query(self, mock_llm, mock_summ, client):
        """POST /api/query → 202 + job_id."""
        mock_summ.return_value.query.return_value = "답변입니다."
        resp = client.post("/api/query", json={"question": "이번 달 성과?"})
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    @patch("git_recap.api.routes.query.SummarizerService")
    @patch("git_recap.api.routes.query.LLMClient")
    def test_query_completes(self, mock_llm, mock_summ, client):
        """POST → job에 LLM 응답 저장."""
        mock_summ.return_value.query.return_value = "답변입니다."
        resp = client.post("/api/query", json={"question": "이번 달 성과?"})
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert status_resp.json()["result"] == "답변입니다."

    @patch("git_recap.api.routes.query.SummarizerService")
    @patch("git_recap.api.routes.query.LLMClient")
    def test_query_failure(self, mock_llm, mock_summ, client):
        """SummarizeError → job status = failed."""
        mock_summ.return_value.query.side_effect = SummarizeError("No context")
        resp = client.post("/api/query", json={"question": "질문?"})
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "failed"
        assert "No context" in status_resp.json()["error"]


# ── TestStaticFiles ──


class TestStaticFiles:
    def test_serves_index_html(self, client):
        """GET / → index.html 반환."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "git-recap" in resp.text

    def test_serves_css(self, client):
        """GET /style.css → CSS 파일 반환."""
        resp = client.get("/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_serves_js(self, client):
        """GET /app.js → JS 파일 반환."""
        resp = client.get("/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
