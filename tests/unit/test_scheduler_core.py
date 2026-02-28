"""SchedulerService -- APScheduler 래퍼 테스트."""

import pytest
import pytest_asyncio

from workrecap.scheduler.config import ScheduleConfig
from workrecap.scheduler.core import SchedulerService
from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import LogNotifier


@pytest.fixture()
def schedule_config():
    return ScheduleConfig(enabled=True, timezone="Asia/Seoul")


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "history.json")


@pytest_asyncio.fixture()
async def service(schedule_config, history):
    svc = SchedulerService(schedule_config, history, LogNotifier())
    yield svc
    # Ensure cleanup even if test doesn't call shutdown
    svc.shutdown()


class TestSchedulerService:
    def test_create(self, schedule_config, history):
        svc = SchedulerService(schedule_config, history, LogNotifier())
        assert svc is not None

    @pytest.mark.asyncio
    async def test_start_registers_jobs(self, service):
        service.start()
        try:
            jobs = service.get_jobs()
            job_ids = [j["id"] for j in jobs]
            assert "daily" in job_ids
            assert "weekly" in job_ids
            assert "monthly" in job_ids
            assert "yearly" in job_ids
        finally:
            service.shutdown()

    @pytest.mark.asyncio
    async def test_status_running(self, service):
        service.start()
        try:
            status = service.status()
            assert status["state"] == "running"
            assert len(status["jobs"]) == 4
        finally:
            service.shutdown()

    def test_status_stopped(self, schedule_config, history):
        svc = SchedulerService(schedule_config, history, LogNotifier())
        status = svc.status()
        assert status["state"] == "stopped"

    @pytest.mark.asyncio
    async def test_pause_resume(self, service):
        service.start()
        try:
            service.pause()
            assert service.status()["state"] == "paused"
            service.resume()
            assert service.status()["state"] == "running"
        finally:
            service.shutdown()

    def test_disabled_config_no_start(self, tmp_path):
        cfg = ScheduleConfig(enabled=False)
        hist = SchedulerHistory(tmp_path / "h.json")
        svc = SchedulerService(cfg, hist, LogNotifier())
        svc.start()
        assert svc.status()["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_get_jobs_includes_next_run(self, service):
        service.start()
        try:
            jobs = service.get_jobs()
            for j in jobs:
                assert "next_run" in j
        finally:
            service.shutdown()
