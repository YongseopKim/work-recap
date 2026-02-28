"""Summaries available API 테스트."""

import pytest
from starlette.testclient import TestClient

from workrecap.api.app import create_app
from workrecap.api.deps import get_config, get_job_store
from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig


@pytest.fixture()
def test_config(tmp_path):
    data_dir = tmp_path / "data"
    for sub in ["state/jobs", "raw", "normalized", "summaries"]:
        (data_dir / sub).mkdir(parents=True)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=data_dir,
        prompts_dir=prompts_dir,
    )


@pytest.fixture()
def client(test_config):
    app = create_app()
    store = JobStore(test_config)
    app.dependency_overrides[get_config] = lambda: test_config
    app.dependency_overrides[get_job_store] = lambda: store
    return TestClient(app)


class TestSummariesAvailable:
    def test_empty_month(self, client):
        """데이터 없는 월 조회 시 모든 리스트가 비어있다."""
        resp = client.get("/api/summaries/available?year=2025&month=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily"] == []
        assert data["weekly"] == []
        assert data["monthly"] == []
        assert data["yearly"] is False

    def test_with_daily_summaries(self, client, test_config):
        """daily summary 파일이 있으면 해당 날짜가 리스트에 포함된다."""
        daily_dir = test_config.summaries_dir / "2025" / "daily"
        daily_dir.mkdir(parents=True)
        (daily_dir / "02-10.md").write_text("summary", encoding="utf-8")
        (daily_dir / "02-14.md").write_text("summary", encoding="utf-8")
        (daily_dir / "03-01.md").write_text("other month", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert sorted(data["daily"]) == ["02-10", "02-14"]

    def test_with_weekly_summaries(self, client, test_config):
        """weekly summary 파일이 있으면 해당 주차가 리스트에 포함된다."""
        weekly_dir = test_config.summaries_dir / "2025" / "weekly"
        weekly_dir.mkdir(parents=True)
        (weekly_dir / "W06.md").write_text("summary", encoding="utf-8")
        (weekly_dir / "W07.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert "W06" in data["weekly"]
        assert "W07" in data["weekly"]

    def test_with_monthly_summary(self, client, test_config):
        """monthly summary 파일이 있으면 리스트에 포함된다."""
        monthly_dir = test_config.summaries_dir / "2025" / "monthly"
        monthly_dir.mkdir(parents=True)
        (monthly_dir / "02.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert data["monthly"] == ["02"]

    def test_with_yearly_summary(self, client, test_config):
        """yearly summary 파일이 있으면 True를 반환한다."""
        yearly_dir = test_config.summaries_dir / "2025"
        yearly_dir.mkdir(parents=True)
        (yearly_dir / "yearly.md").write_text("summary", encoding="utf-8")

        resp = client.get("/api/summaries/available?year=2025&month=2")
        data = resp.json()
        assert data["yearly"] is True

    def test_missing_year_param(self, client):
        """year 파라미터 누락 시 422."""
        resp = client.get("/api/summaries/available?month=2")
        assert resp.status_code == 422

    def test_missing_month_param(self, client):
        """month 파라미터 누락 시 422."""
        resp = client.get("/api/summaries/available?year=2025")
        assert resp.status_code == 422
