"""Notifier ABC + LogNotifier 테스트."""

import asyncio
import logging

from workrecap.scheduler.notifier import LogNotifier, Notifier, SchedulerEvent


class TestSchedulerEvent:
    def test_event_creation(self):
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="2026-02-28T02:00:00+09:00",
            completed_at="2026-02-28T02:05:00+09:00",
            target="2026-02-27",
        )
        assert event.job == "daily"
        assert event.error is None

    def test_event_with_error(self):
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="2026-02-28T02:00:00+09:00",
            target="2026-02-27",
            error="FetchError: timeout",
        )
        assert event.status == "failed"
        assert event.error == "FetchError: timeout"


class TestLogNotifier:
    def test_is_notifier_subclass(self):
        assert issubclass(LogNotifier, Notifier)

    def test_notify_success(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily",
            status="success",
            triggered_at="t1",
            target="2026-02-27",
        )
        with caplog.at_level(logging.INFO, logger="workrecap.scheduler.notifier"):
            asyncio.run(notifier.notify(event))
        assert "daily" in caplog.text
        assert "success" in caplog.text

    def test_notify_failure(self, caplog):
        notifier = LogNotifier()
        event = SchedulerEvent(
            job="daily",
            status="failed",
            triggered_at="t1",
            target="2026-02-27",
            error="boom",
        )
        with caplog.at_level(logging.ERROR, logger="workrecap.scheduler.notifier"):
            asyncio.run(notifier.notify(event))
        assert "failed" in caplog.text
        assert "boom" in caplog.text
