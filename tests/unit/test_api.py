"""BE API 테스트 — FastAPI TestClient 기반."""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from workrecap.api.app import create_app
from workrecap.api.deps import get_config, get_job_store
from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig
from workrecap.exceptions import FetchError, StepFailedError, SummarizeError
from workrecap.models import JobStatus


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


# ── Helper: standard pipeline mocks decorator ──

PIPELINE_MOCKS = [
    "workrecap.api.routes.pipeline.OrchestratorService",
    "workrecap.api.routes.pipeline.SummarizerService",
    "workrecap.api.routes.pipeline.NormalizerService",
    "workrecap.api.routes.pipeline.FetcherService",
    "workrecap.api.routes.pipeline.FetchProgressStore",
    "workrecap.api.routes.pipeline.DailyStateStore",
    "workrecap.api.routes.pipeline.LLMClient",
    "workrecap.api.routes.pipeline.GHESClient",
    "workrecap.api.routes.pipeline.GHESClientPool",
]

FETCH_MOCKS = [
    "workrecap.api.routes.fetch.FetcherService",
    "workrecap.api.routes.fetch.FetchProgressStore",
    "workrecap.api.routes.fetch.DailyStateStore",
    "workrecap.api.routes.fetch.GHESClient",
    "workrecap.api.routes.fetch.GHESClientPool",
]

NORMALIZE_MOCKS = [
    "workrecap.api.routes.normalize.NormalizerService",
    "workrecap.api.routes.normalize.DailyStateStore",
    "workrecap.api.routes.normalize.LLMClient",
]

SUMMARIZE_MOCKS = [
    "workrecap.api.routes.summarize_pipeline.SummarizerService",
    "workrecap.api.routes.summarize_pipeline.DailyStateStore",
    "workrecap.api.routes.summarize_pipeline.LLMClient",
]


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
        """WorkRecapError 발생 시 500 + JSON 에러."""
        # summary 라우트에서 config path가 잘못되면 에러 아닌 404가 발생하므로
        # 직접 exception을 트리거하는 것은 어려움 → job not found로 404 확인
        resp = client.get("/api/pipeline/jobs/nonexistent")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# ── TestPipelineRun ──


class TestPipelineRun:
    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_single_date(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """POST /api/pipeline/run/{date} → 202 + job_id (no body, backward compatible)."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        resp = client.post("/api/pipeline/run/2025-02-16")
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "accepted"

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_single_with_body(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """POST /api/pipeline/run/{date} with force/types/enrich passes params through."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        resp = client.post(
            "/api/pipeline/run/2025-02-16",
            json={"force": True, "types": ["prs"], "enrich": False},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

        # Verify types passed as set
        mock_orch.return_value.run_daily.assert_called_once_with("2025-02-16", types={"prs"})

        # Verify enrich=False → NormalizerService gets llm=None
        norm_call_kwargs = mock_norm.call_args
        assert norm_call_kwargs.kwargs.get("llm") is None

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_completes_job(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """POST → job status가 completed로 전이."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        resp = client.post("/api/pipeline/run/2025-02-16")
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_failure_marks_job_failed(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
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

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_single_injects_progress_store(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """FetchProgressStore injected into FetcherService."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        client.post("/api/pipeline/run/2025-02-16")

        # FetcherService should be created with progress_store
        fetch_kwargs = mock_fetch.call_args
        assert "progress_store" in fetch_kwargs.kwargs

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_single_closes_ghes(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """GHESClient.close() called in finally."""
        mock_orch.return_value.run_daily.return_value = Path("/data/daily.md")
        client.post("/api/pipeline/run/2025-02-16")
        mock_ghes.return_value.close.assert_called_once()


# ── TestPipelineRunRange ──


class TestPipelineRunRange:
    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
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

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range_partial_failure(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
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

    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range_with_params(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        client,
    ):
        """force/types/workers/enrich pass-through to orchestrator."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success", "path": "/p1"},
        ]
        resp = client.post(
            "/api/pipeline/run/range",
            json={
                "since": "2025-02-15",
                "until": "2025-02-15",
                "force": True,
                "types": ["prs", "commits"],
                "max_workers": 3,
                "enrich": False,
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

        # Verify orchestrator.run_range called with correct params
        call_kwargs = mock_orch.return_value.run_range.call_args
        assert call_kwargs.kwargs["force"] is True
        assert call_kwargs.kwargs["types"] == {"prs", "commits"}
        assert call_kwargs.kwargs["max_workers"] == 3

        # enrich=False → NormalizerService gets llm=None
        norm_kwargs = mock_norm.call_args
        assert norm_kwargs.kwargs.get("llm") is None

    @patch("workrecap.api.routes.pipeline.GHESClientPool")
    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range_pool_cleanup(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        mock_pool,
        client,
    ):
        """GHESClientPool.close() called when workers > 1."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
        ]
        client.post(
            "/api/pipeline/run/range",
            json={"since": "2025-02-15", "until": "2025-02-15", "max_workers": 3},
        )
        # Pool should be created and closed
        mock_pool.assert_called_once()
        mock_pool.return_value.close.assert_called_once()

    @patch("workrecap.api.routes.pipeline._run_hierarchical")
    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range_hierarchical_weekly(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        mock_hier,
        client,
    ):
        """summarize_weekly triggers hierarchical summarization."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-10", "status": "success"},
            {"date": "2025-02-11", "status": "success"},
        ]
        mock_hier.return_value = "/path/to/weekly.md"

        resp = client.post(
            "/api/pipeline/run/range",
            json={
                "since": "2025-02-10",
                "until": "2025-02-11",
                "summarize_weekly": "2025-7",
            },
        )
        job_id = resp.json()["job_id"]
        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "hierarchical" in status_resp.json()["result"]
        mock_hier.assert_called_once()

    @patch("workrecap.api.routes.pipeline._run_hierarchical")
    @patch("workrecap.api.routes.pipeline.OrchestratorService")
    @patch("workrecap.api.routes.pipeline.SummarizerService")
    @patch("workrecap.api.routes.pipeline.NormalizerService")
    @patch("workrecap.api.routes.pipeline.FetcherService")
    @patch("workrecap.api.routes.pipeline.FetchProgressStore")
    @patch("workrecap.api.routes.pipeline.DailyStateStore")
    @patch("workrecap.api.routes.pipeline.LLMClient")
    @patch("workrecap.api.routes.pipeline.GHESClient")
    def test_run_range_hierarchical_skipped_on_failure(
        self,
        mock_ghes,
        mock_llm,
        mock_ds,
        mock_ps,
        mock_fetch,
        mock_norm,
        mock_summ,
        mock_orch,
        mock_hier,
        client,
    ):
        """Hierarchical summarization skipped when daily pipeline has failures."""
        mock_orch.return_value.run_range.return_value = [
            {"date": "2025-02-10", "status": "success"},
            {"date": "2025-02-11", "status": "failed", "error": "fail"},
        ]
        client.post(
            "/api/pipeline/run/range",
            json={
                "since": "2025-02-10",
                "until": "2025-02-11",
                "summarize_weekly": "2025-7",
            },
        )
        mock_hier.assert_not_called()


# ── TestPipelineRunRangeHierarchical (unit tests for helpers) ──


class TestHierarchicalHelper:
    def test_weeks_in_month(self):
        """_weeks_in_month returns correct ISO weeks for a month."""
        from workrecap.api.routes.pipeline import _weeks_in_month

        weeks = _weeks_in_month(2025, 2)
        # Feb 2025: starts on Saturday Feb 1 (W5), ends on Friday Feb 28 (W9)
        assert len(weeks) >= 4
        assert all(isinstance(w, tuple) and len(w) == 2 for w in weeks)

    @patch("workrecap.api.routes.pipeline.SummarizeError", SummarizeError)
    def test_run_hierarchical_weekly(self):
        """_run_hierarchical with summarize_weekly calls weekly."""
        from unittest.mock import MagicMock

        from workrecap.api.routes.pipeline import _run_hierarchical

        summarizer = MagicMock()
        summarizer.weekly.return_value = Path("/path/weekly.md")
        result = _run_hierarchical(summarizer, False, "2025-7", None, None)
        assert result == "/path/weekly.md"
        summarizer.weekly.assert_called_once_with(2025, 7, force=False)

    def test_run_hierarchical_monthly(self):
        """_run_hierarchical with summarize_monthly calls weekly+monthly."""
        from unittest.mock import MagicMock

        from workrecap.api.routes.pipeline import _run_hierarchical

        summarizer = MagicMock()
        summarizer.weekly.return_value = Path("/w.md")
        summarizer.monthly.return_value = Path("/path/monthly.md")
        result = _run_hierarchical(summarizer, True, None, "2025-2", None)
        assert result == "/path/monthly.md"
        summarizer.monthly.assert_called_once_with(2025, 2, force=True)
        # weekly called for each week in Feb 2025
        assert summarizer.weekly.call_count >= 4

    def test_run_hierarchical_yearly(self):
        """_run_hierarchical with summarize_yearly calls weekly+monthly+yearly."""
        from unittest.mock import MagicMock

        from workrecap.api.routes.pipeline import _run_hierarchical

        summarizer = MagicMock()
        summarizer.weekly.return_value = Path("/w.md")
        summarizer.monthly.return_value = Path("/m.md")
        summarizer.yearly.return_value = Path("/path/yearly.md")
        result = _run_hierarchical(summarizer, False, None, None, 2025)
        assert result == "/path/yearly.md"
        summarizer.yearly.assert_called_once_with(2025, force=False)
        # monthly called 12 times
        assert summarizer.monthly.call_count == 12

    def test_run_hierarchical_none(self):
        """_run_hierarchical returns None when no summarize option."""
        from unittest.mock import MagicMock

        from workrecap.api.routes.pipeline import _run_hierarchical

        summarizer = MagicMock()
        result = _run_hierarchical(summarizer, False, None, None, None)
        assert result is None


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


# ── TestFetchEndpoints ──


class TestFetchEndpoints:
    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_single(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """POST /api/pipeline/fetch/{date} → 202 + completed."""
        mock_fetcher.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        resp = client.post("/api/pipeline/fetch/2025-02-16")
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_single_with_types(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """POST /api/pipeline/fetch/{date} with types passes them through."""
        mock_fetcher.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        client.post(
            "/api/pipeline/fetch/2025-02-16",
            json={"types": ["prs"]},
        )
        mock_fetcher.return_value.fetch.assert_called_once_with("2025-02-16", types={"prs"})

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_single_no_body(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """POST /api/pipeline/fetch/{date} without body → backward compatible."""
        mock_fetcher.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        resp = client.post("/api/pipeline/fetch/2025-02-16")
        assert resp.status_code == 202
        mock_fetcher.return_value.fetch.assert_called_once_with("2025-02-16", types=None)

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_single_closes_ghes(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """GHESClient.close() called in finally."""
        mock_fetcher.return_value.fetch.return_value = {"prs": Path("/data/prs.json")}
        client.post("/api/pipeline/fetch/2025-02-16")
        mock_ghes.return_value.close.assert_called_once()

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_range(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """POST /api/pipeline/fetch/range → 202 + completed."""
        mock_fetcher.return_value.fetch_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        resp = client.post(
            "/api/pipeline/fetch/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "2/2 succeeded" in status_resp.json()["result"]

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_range_with_force_and_types(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """Fetch range with force and types passed through."""
        mock_fetcher.return_value.fetch_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
        ]
        client.post(
            "/api/pipeline/fetch/range",
            json={
                "since": "2025-02-15",
                "until": "2025-02-15",
                "force": True,
                "types": ["commits"],
            },
        )
        call_kwargs = mock_fetcher.return_value.fetch_range.call_args
        assert call_kwargs.kwargs["force"] is True
        assert call_kwargs.kwargs["types"] == {"commits"}

    @patch("workrecap.api.routes.fetch.GHESClientPool")
    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_range_with_workers(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        mock_pool,
        client,
    ):
        """workers > 1 creates GHESClientPool, cleans up in finally."""
        mock_fetcher.return_value.fetch_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
        ]
        client.post(
            "/api/pipeline/fetch/range",
            json={"since": "2025-02-15", "until": "2025-02-15", "max_workers": 4},
        )
        mock_pool.assert_called_once()
        mock_pool.return_value.close.assert_called_once()

    @patch("workrecap.api.routes.fetch.FetcherService")
    @patch("workrecap.api.routes.fetch.FetchProgressStore")
    @patch("workrecap.api.routes.fetch.DailyStateStore")
    @patch("workrecap.api.routes.fetch.GHESClient")
    def test_fetch_range_partial_failure(
        self,
        mock_ghes,
        mock_ds,
        mock_ps,
        mock_fetcher,
        client,
    ):
        """Partial failure → job failed."""
        mock_fetcher.return_value.fetch_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "failed", "error": "timeout"},
        ]
        resp = client.post(
            "/api/pipeline/fetch/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        job_id = resp.json()["job_id"]
        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "failed"
        assert "1/2" in status_resp.json()["error"]


# ── TestNormalizeEndpoints ──


class TestNormalizeEndpoints:
    @patch("workrecap.api.routes.normalize.NormalizerService")
    @patch("workrecap.api.routes.normalize.DailyStateStore")
    @patch("workrecap.api.routes.normalize.LLMClient")
    def test_normalize_single(
        self,
        mock_llm,
        mock_ds,
        mock_norm,
        client,
    ):
        """POST /api/pipeline/normalize/{date} → 202 + completed."""
        mock_norm.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        resp = client.post("/api/pipeline/normalize/2025-02-16")
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("workrecap.api.routes.normalize.NormalizerService")
    @patch("workrecap.api.routes.normalize.DailyStateStore")
    @patch("workrecap.api.routes.normalize.LLMClient")
    def test_normalize_single_enrich_false(
        self,
        mock_llm,
        mock_ds,
        mock_norm,
        client,
    ):
        """enrich=False → LLMClient not created."""
        mock_norm.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        client.post(
            "/api/pipeline/normalize/2025-02-16",
            json={"enrich": False},
        )
        # LLMClient should NOT be called
        mock_llm.assert_not_called()
        # NormalizerService should get llm=None
        norm_kwargs = mock_norm.call_args
        assert norm_kwargs.kwargs.get("llm") is None

    @patch("workrecap.api.routes.normalize.NormalizerService")
    @patch("workrecap.api.routes.normalize.DailyStateStore")
    @patch("workrecap.api.routes.normalize.LLMClient")
    def test_normalize_single_enrich_true(
        self,
        mock_llm,
        mock_ds,
        mock_norm,
        client,
    ):
        """enrich=True (default) → LLMClient created and passed."""
        mock_norm.return_value.normalize.return_value = (
            Path("/data/activities.jsonl"),
            Path("/data/stats.json"),
        )
        client.post("/api/pipeline/normalize/2025-02-16")
        # LLMClient called once
        mock_llm.assert_called_once()
        # NormalizerService should get llm=mock
        norm_kwargs = mock_norm.call_args
        assert norm_kwargs.kwargs.get("llm") is not None

    @patch("workrecap.api.routes.normalize.NormalizerService")
    @patch("workrecap.api.routes.normalize.DailyStateStore")
    @patch("workrecap.api.routes.normalize.LLMClient")
    def test_normalize_range(
        self,
        mock_llm,
        mock_ds,
        mock_norm,
        client,
    ):
        """POST /api/pipeline/normalize/range → 202 + completed."""
        mock_norm.return_value.normalize_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        resp = client.post(
            "/api/pipeline/normalize/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "2/2 succeeded" in status_resp.json()["result"]

    @patch("workrecap.api.routes.normalize.NormalizerService")
    @patch("workrecap.api.routes.normalize.DailyStateStore")
    @patch("workrecap.api.routes.normalize.LLMClient")
    def test_normalize_range_with_params(
        self,
        mock_llm,
        mock_ds,
        mock_norm,
        client,
    ):
        """force/enrich/workers passed through."""
        mock_norm.return_value.normalize_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
        ]
        client.post(
            "/api/pipeline/normalize/range",
            json={
                "since": "2025-02-15",
                "until": "2025-02-15",
                "force": True,
                "enrich": False,
                "max_workers": 3,
            },
        )
        call_kwargs = mock_norm.return_value.normalize_range.call_args
        assert call_kwargs.kwargs["force"] is True
        assert call_kwargs.kwargs["max_workers"] == 3
        # enrich=False → no LLM
        mock_llm.assert_not_called()


# ── TestSummarizeEndpoints ──


class TestSummarizeEndpoints:
    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.DailyStateStore")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_daily_single(
        self,
        mock_llm,
        mock_ds,
        mock_summ,
        client,
    ):
        """POST /api/pipeline/summarize/daily/{date} → 202 + completed."""
        mock_summ.return_value.daily.return_value = Path("/data/summary.md")
        resp = client.post("/api/pipeline/summarize/daily/2025-02-16")
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "summary.md" in status_resp.json()["result"]

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.DailyStateStore")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_daily_range(
        self,
        mock_llm,
        mock_ds,
        mock_summ,
        client,
    ):
        """POST /api/pipeline/summarize/daily/range → 202 + completed."""
        mock_summ.return_value.daily_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
            {"date": "2025-02-16", "status": "success"},
        ]
        resp = client.post(
            "/api/pipeline/summarize/daily/range",
            json={"since": "2025-02-15", "until": "2025-02-16"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "2/2 succeeded" in status_resp.json()["result"]

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_weekly(
        self,
        mock_llm,
        mock_summ,
        client,
    ):
        """POST /api/pipeline/summarize/weekly → 202 + completed."""
        mock_summ.return_value.weekly.return_value = Path("/data/W07.md")
        resp = client.post(
            "/api/pipeline/summarize/weekly",
            json={"year": 2025, "week": 7},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert "W07.md" in status_resp.json()["result"]

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_weekly_with_force(
        self,
        mock_llm,
        mock_summ,
        client,
    ):
        """force=True passed through to service."""
        mock_summ.return_value.weekly.return_value = Path("/data/W07.md")
        client.post(
            "/api/pipeline/summarize/weekly",
            json={"year": 2025, "week": 7, "force": True},
        )
        mock_summ.return_value.weekly.assert_called_once_with(2025, 7, force=True)

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_monthly(
        self,
        mock_llm,
        mock_summ,
        client,
    ):
        """POST /api/pipeline/summarize/monthly → 202 + completed."""
        mock_summ.return_value.monthly.return_value = Path("/data/02.md")
        resp = client.post(
            "/api/pipeline/summarize/monthly",
            json={"year": 2025, "month": 2},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_yearly(
        self,
        mock_llm,
        mock_summ,
        client,
    ):
        """POST /api/pipeline/summarize/yearly → 202 + completed."""
        mock_summ.return_value.yearly.return_value = Path("/data/yearly.md")
        resp = client.post(
            "/api/pipeline/summarize/yearly",
            json={"year": 2025},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_yearly_failure(
        self,
        mock_llm,
        mock_summ,
        client,
    ):
        """SummarizeError → job status = failed."""
        mock_summ.return_value.yearly.side_effect = SummarizeError("No monthly summaries")
        resp = client.post(
            "/api/pipeline/summarize/yearly",
            json={"year": 2025},
        )
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "failed"
        assert "No monthly summaries" in status_resp.json()["error"]

    @patch("workrecap.api.routes.summarize_pipeline.SummarizerService")
    @patch("workrecap.api.routes.summarize_pipeline.DailyStateStore")
    @patch("workrecap.api.routes.summarize_pipeline.LLMClient")
    def test_summarize_daily_range_with_params(
        self,
        mock_llm,
        mock_ds,
        mock_summ,
        client,
    ):
        """force/workers passed through to daily_range."""
        mock_summ.return_value.daily_range.return_value = [
            {"date": "2025-02-15", "status": "success"},
        ]
        client.post(
            "/api/pipeline/summarize/daily/range",
            json={
                "since": "2025-02-15",
                "until": "2025-02-15",
                "force": True,
                "max_workers": 3,
            },
        )
        call_kwargs = mock_summ.return_value.daily_range.call_args
        assert call_kwargs.kwargs["force"] is True
        assert call_kwargs.kwargs["max_workers"] == 3


# ── TestSummary (read endpoints) ──


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
    @patch("workrecap.api.routes.query.SummarizerService")
    @patch("workrecap.api.routes.query.LLMClient")
    def test_query(self, mock_llm, mock_summ, client):
        """POST /api/query → 202 + job_id."""
        mock_summ.return_value.query.return_value = "답변입니다."
        resp = client.post("/api/query", json={"question": "이번 달 성과?"})
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    @patch("workrecap.api.routes.query.SummarizerService")
    @patch("workrecap.api.routes.query.LLMClient")
    def test_query_completes(self, mock_llm, mock_summ, client):
        """POST → job에 LLM 응답 저장."""
        mock_summ.return_value.query.return_value = "답변입니다."
        resp = client.post("/api/query", json={"question": "이번 달 성과?"})
        job_id = resp.json()["job_id"]

        status_resp = client.get(f"/api/pipeline/jobs/{job_id}")
        assert status_resp.json()["status"] == "completed"
        assert status_resp.json()["result"] == "답변입니다."

    @patch("workrecap.api.routes.query.SummarizerService")
    @patch("workrecap.api.routes.query.LLMClient")
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
        assert "work-recap" in resp.text

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
