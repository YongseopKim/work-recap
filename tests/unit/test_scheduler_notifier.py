"""Notifier ABC + LogNotifier 테스트."""

import asyncio
import logging
from unittest.mock import AsyncMock

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


class TestCompositeNotifier:
    def test_is_notifier_subclass(self):
        from workrecap.scheduler.notifier import CompositeNotifier

        assert issubclass(CompositeNotifier, Notifier)

    def test_calls_all_notifiers(self):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(
            job="daily", status="success", triggered_at="t1", target="2026-02-27"
        )
        asyncio.run(composite.notify(event))
        n1.notify.assert_awaited_once_with(event)
        n2.notify.assert_awaited_once_with(event)

    def test_continues_on_failure(self, caplog):
        from workrecap.scheduler.notifier import CompositeNotifier

        n1 = AsyncMock(spec=Notifier)
        n1.notify.side_effect = RuntimeError("boom")
        n2 = AsyncMock(spec=Notifier)
        composite = CompositeNotifier([n1, n2])
        event = SchedulerEvent(
            job="daily", status="failed", triggered_at="t1", target="2026-02-27"
        )
        with caplog.at_level(logging.WARNING):
            asyncio.run(composite.notify(event))
        n2.notify.assert_awaited_once_with(event)
