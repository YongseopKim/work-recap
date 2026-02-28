"""Scheduler API 엔드포인트 테스트."""

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from workrecap.api.app import create_app
from workrecap.api.deps import get_config, get_job_store
from workrecap.api.job_store import JobStore
from workrecap.config import AppConfig
from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.core import SchedulerService
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import LogNotifier


@pytest.fixture()
def test_config(tmp_path):
    return AppConfig(
        ghes_url="https://github.example.com",
        ghes_token="test-token",
        username="testuser",
        data_dir=tmp_path / "data",
        prompts_dir=tmp_path / "prompts",
    )


@pytest.fixture()
def store(test_config):
    return JobStore(test_config)


@pytest.fixture()
def schedule_config(tmp_path):
    toml = tmp_path / "schedule.toml"
    toml.write_text(
        textwrap.dedent("""\
        [scheduler]
        enabled = true
        timezone = "Asia/Seoul"

        [scheduler.daily]
        time = "02:00"
        """)
    )
    return ScheduleConfig.from_toml(toml)


@pytest.fixture()
def client(test_config, store, schedule_config, tmp_path):
    """TestClient with a real (but non-started) scheduler wired into app.state.

    The lifespan will fallback to disabled mode because get_config() uses .env.test.
    We then override app.state with our properly configured scheduler objects.
    """
    app = create_app()
    app.dependency_overrides[get_config] = lambda: test_config
    app.dependency_overrides[get_job_store] = lambda: store

    # Wrap in TestClient — lifespan runs but falls back to disabled scheduler
    tc = TestClient(app)

    # Replace app.state with our test scheduler (enabled=false for unit tests,
    # since we don't want APScheduler actually running in tests)
    disabled_config = ScheduleConfig()  # enabled=False
    history = SchedulerHistory(tmp_path / "data" / "state" / "scheduler_history.json")
    notifier = LogNotifier()
    scheduler = SchedulerService(disabled_config, history, notifier)
    app.state.scheduler = scheduler
    app.state.scheduler_history = history

    return tc


class TestSchedulerStatus:
    def test_get_status_disabled(self, client):
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "disabled"
        assert data["jobs"] == []

    def test_get_status_running(self, client, schedule_config, tmp_path):
        """Running scheduler should report 'running' with jobs."""
        history = SchedulerHistory(tmp_path / "hist.json")
        notifier = LogNotifier()
        scheduler = SchedulerService(schedule_config, history, notifier)
        # Simulate a running scheduler without an event loop
        mock_apscheduler = MagicMock()
        mock_apscheduler.get_jobs.return_value = [
            MagicMock(id="daily", next_run_time=None),
            MagicMock(id="weekly", next_run_time=None),
            MagicMock(id="monthly", next_run_time=None),
            MagicMock(id="yearly", next_run_time=None),
        ]
        scheduler._scheduler = mock_apscheduler
        scheduler._paused = False
        client.app.state.scheduler = scheduler

        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"
        assert len(data["jobs"]) == 4


class TestSchedulerHistory:
    def test_get_empty_history(self, client):
        resp = client.get("/api/scheduler/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_history_with_job_filter(self, client):
        resp = client.get("/api/scheduler/history?job=daily")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_history_with_limit(self, client):
        resp = client.get("/api/scheduler/history?limit=5")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_history_after_recording(self, client, tmp_path):
        """Record an event, then verify it appears in history."""
        from workrecap.scheduler.notifier import SchedulerEvent

        history = SchedulerHistory(tmp_path / "data" / "state" / "scheduler_history.json")
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00+00:00",
            target="2026-02-27",
            completed_at="2026-02-28T02:05:00+00:00",
        )
        history.record(event)
        client.app.state.scheduler_history = history

        resp = client.get("/api/scheduler/history")
        assert resp.status_code == 200
        entries = resp.json()
        assert len(entries) == 1
        assert entries[0]["job"] == "daily"
        assert entries[0]["status"] == "success"


class TestSchedulerTrigger:
    @patch("workrecap.api.routes.scheduler.run_daily_job", new_callable=AsyncMock)
    def test_trigger_daily(self, mock_job, client):
        resp = client.post("/api/scheduler/trigger/daily")
        assert resp.status_code == 202
        assert resp.json()["triggered"] == "daily"

    @patch("workrecap.api.routes.scheduler.run_weekly_job", new_callable=AsyncMock)
    def test_trigger_weekly(self, mock_job, client):
        resp = client.post("/api/scheduler/trigger/weekly")
        assert resp.status_code == 202
        assert resp.json()["triggered"] == "weekly"

    @patch("workrecap.api.routes.scheduler.run_monthly_job", new_callable=AsyncMock)
    def test_trigger_monthly(self, mock_job, client):
        resp = client.post("/api/scheduler/trigger/monthly")
        assert resp.status_code == 202
        assert resp.json()["triggered"] == "monthly"

    @patch("workrecap.api.routes.scheduler.run_yearly_job", new_callable=AsyncMock)
    def test_trigger_yearly(self, mock_job, client):
        resp = client.post("/api/scheduler/trigger/yearly")
        assert resp.status_code == 202
        assert resp.json()["triggered"] == "yearly"

    def test_trigger_invalid_job(self, client):
        resp = client.post("/api/scheduler/trigger/invalid")
        assert resp.status_code == 404
        assert "Unknown job" in resp.json()["detail"]


class TestSchedulerPauseResume:
    def test_pause(self, client):
        resp = client.put("/api/scheduler/pause")
        assert resp.status_code == 200
        assert resp.json()["state"] == "paused"

    def test_resume(self, client):
        resp = client.put("/api/scheduler/resume")
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"

    def test_pause_then_status_shows_paused(self, client, schedule_config, tmp_path):
        """After pausing a running scheduler, status should report 'paused'."""
        history = SchedulerHistory(tmp_path / "hist.json")
        notifier = LogNotifier()
        scheduler = SchedulerService(schedule_config, history, notifier)
        # Simulate a running scheduler without an event loop
        mock_apscheduler = MagicMock()
        mock_apscheduler.get_jobs.return_value = []
        scheduler._scheduler = mock_apscheduler
        scheduler._paused = False
        client.app.state.scheduler = scheduler

        client.put("/api/scheduler/pause")
        resp = client.get("/api/scheduler/status")
        assert resp.json()["state"] == "paused"

        client.put("/api/scheduler/resume")
        resp = client.get("/api/scheduler/status")
        assert resp.json()["state"] == "running"
