"""SchedulerHistory -- 실행 이력 저장/조회 테스트."""

import pytest

from workrecap.scheduler.history import SchedulerHistory
from workrecap.scheduler.notifier import SchedulerEvent


@pytest.fixture()
def history(tmp_path):
    return SchedulerHistory(tmp_path / "scheduler_history.json")


class TestSchedulerHistory:
    def test_empty_history(self, history):
        assert history.list() == []

    def test_record_and_list(self, history):
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00+09:00",
            completed_at="2026-02-28T02:05:00+09:00",
            target="2026-02-27",
        )
        history.record(event)
        entries = history.list()
        assert len(entries) == 1
        assert entries[0]["job"] == "daily"
        assert entries[0]["status"] == "success"
        assert entries[0]["target"] == "2026-02-27"

    def test_record_failure(self, history):
        event = SchedulerEvent(
            job="weekly",
            status="failed",
            triggered_at="t1",
            target="2026-W08",
            error="SummarizeError: no data",
        )
        history.record(event)
        entries = history.list()
        assert entries[0]["error"] == "SummarizeError: no data"

    def test_max_entries(self, history):
        for i in range(200):
            event = SchedulerEvent(
                job="daily",
                status="success",
                triggered_at=f"t{i}",
                target=f"d{i}",
            )
            history.record(event)
        entries = history.list()
        assert len(entries) == 100  # default max

    def test_persistence(self, tmp_path):
        path = tmp_path / "history.json"
        h1 = SchedulerHistory(path)
        h1.record(
            SchedulerEvent(
                job="daily",
                status="success",
                triggered_at="t1",
                target="d1",
            )
        )
        h2 = SchedulerHistory(path)
        assert len(h2.list()) == 1

    def test_list_filter_by_job(self, history):
        history.record(
            SchedulerEvent(
                job="daily",
                status="success",
                triggered_at="t1",
                target="d1",
            )
        )
        history.record(
            SchedulerEvent(
                job="weekly",
                status="success",
                triggered_at="t2",
                target="w1",
            )
        )
        assert len(history.list(job="daily")) == 1
        assert len(history.list(job="weekly")) == 1
